"""The mixer (each thing heard at its own volume), the radio's sound and the push-to-talk's: what can be checked
without a sound card."""

import asyncio

import numpy as np

from localtc.bus import EventBus
from localtc.config import Config, MixerConfig
from localtc.dsp.radio import SidetoneFilter, key_sound, radio_effect
from localtc.sim_api import PttPressed, PttReleased, RadioChatter
from localtc.tts.player import AudioPlayer, Clip
from localtc.tts.providers import VoiceChain
from localtc.tts.service import VoiceOut
from localtc.ui.controller import mic_verdict
from test_tts_providers import Fake, FakePlayer

RATE = 22050


def quiet_player(**kw) -> AudioPlayer:
    player = AudioPlayer(**kw)
    player._started = True  # the queues only: nothing played
    return player


def test_each_channel_plays_at_its_own_volume_under_the_master():
    player = quiet_player(volume=0.5, mix=MixerConfig(atc=1.2, chatter=0.4, muted=["atis"]).gains())
    assert player.gain("atc") == 0.6 and player.gain("chatter") == 0.2
    assert player.gain("atis") == 0.0  # muted, its volume kept for later
    assert Clip(np.zeros(1), RATE, "intercom").mixer_channel == "copilot"
    assert Clip(np.zeros(1), RATE, "pilot").mixer_channel == "copilot_radio"
    assert Clip(np.zeros(1), RATE, "atc", channel="chatter").mixer_channel == "chatter"


def test_the_radios_clicks_never_wait_for_a_line_and_keying_doesnt_cut_them():
    player = quiet_player()
    atc, click = Clip(np.zeros(10), RATE, "atc"), Clip(np.zeros(10), RATE, "effect")
    player.play(atc)
    player.play(click)
    assert player.effects.queue.get_nowait() is click and player.radio.queue.get_nowait() is atc
    player.play(click)
    player.keyed()
    assert not click.done.is_set()


def test_the_mixer_is_a_settings_section_with_old_files_loading():
    cfg = Config()
    assert cfg.mixer.sidetone == 0.0  # off unless asked: with speakers the microphone would hear it
    assert set(cfg.mixer.gains()) == {"atc", "atis", "chatter", "copilot_radio", "copilot", "effects", "sidetone"}


def test_a_distant_station_sounds_weaker_and_a_flight_deck_noisier():
    t = np.arange(RATE * 2) / RATE
    voice = (np.sin(2 * np.pi * 300 * t) * (np.sin(2 * np.pi * 1.5 * t) > 0)).astype(np.float32)  # words and gaps
    near = radio_effect(voice, RATE, seed=4, squelch=False, static=0.5)
    far = radio_effect(voice, RATE, seed=4, squelch=False, static=0.5, strength=0.2)
    gap = slice(int(RATE * 0.40), int(RATE * 0.60))  # between the "words"
    assert np.abs(far[gap]).mean() > np.abs(near[gap]).mean()  # more hiss when weak
    deck = radio_effect(voice, RATE, seed=4, squelch=False, static=0.0, cabin=1.0)
    room = radio_effect(voice, RATE, seed=4, squelch=False, static=0.0)
    assert np.abs(deck[gap]).mean() > np.abs(room[gap]).mean() * 2  # the background swells between the words
    assert len(near) == len(voice) and np.array_equal(near, radio_effect(voice, RATE, seed=4, squelch=False, static=0.5))


def test_the_squelch_opens_with_a_moment_of_carrier_and_closes_with_its_tail():
    voice = np.ones(RATE // 2, np.float32) * 0.5
    out = radio_effect(voice, RATE, seed=1)
    assert len(out) > len(voice) + RATE * 0.25  # keyed before, the tail after
    assert np.max(np.abs(out)) <= 0.9 + 1e-6


def test_the_push_to_talk_clicks_and_the_receiver_rushes_when_let_go():
    down, up = key_sound(RATE, press=True), key_sound(RATE, press=False)
    assert 0 < len(down) < len(up) < RATE * 0.2
    assert abs(np.max(np.abs(down)) - 0.9) < 1e-3


def test_the_sidetone_filter_is_seamless_across_blocks_and_changes_rate():
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(16000) * 0.1).astype(np.float32)
    whole = SidetoneFilter(16000, 48000).process(audio)
    f = SidetoneFilter(16000, 48000)
    pieces = np.concatenate([f.process(audio[i:i + 160]) for i in range(0, 16000, 160)])
    assert abs(len(pieces) - 48000) <= 2 and abs(len(whole) - 48000) <= 2
    n = min(len(whole), len(pieces))
    assert np.max(np.abs(whole[:n] - pieces[:n])) < 1e-3  # block by block, the same as all at once


def test_a_microphone_test_says_how_its_level_is():
    rng = np.random.default_rng(2)
    speech = lambda level: (rng.standard_normal(16000 * 2) * level).astype(np.float32)  # noqa: E731
    assert mic_verdict(np.zeros(32000, np.float32))["verdict"] == "silent"
    assert mic_verdict(speech(0.01))["verdict"] == "quiet"
    assert mic_verdict(speech(0.1))["verdict"] == "good"
    assert mic_verdict(np.clip(speech(1.0), -1, 1))["verdict"] == "loud"


class _Player(FakePlayer):
    def keyed(self):
        pass


def test_pressing_the_key_clicks_and_another_flights_line_is_on_its_own_channel():
    async def main():
        bus = EventBus()
        player = _Player()
        out = VoiceOut(bus, VoiceChain([Fake("p")]), player)
        task = asyncio.create_task(out.run())
        await asyncio.sleep(0)
        bus.publish(PttPressed(t=1.0, radio=1))
        bus.publish(PttReleased(t=2.0, radio=1))
        bus.publish(RadioChatter(t=3.0, station="Seattle Tower", frequency_mhz=119.9, speaker="pilot", callsign="Alaska 12",
                                 text="Cleared to land 34L, Alaska 12", spoken="cleared to land three four left, alaska twelve"))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if len(player.played) >= 3:
                break
        task.cancel()
        return player.played

    played = asyncio.run(main())
    assert [c.kind for c in played[:2]] == ["effect", "effect"]
    assert played[2].mixer_channel == "chatter"
