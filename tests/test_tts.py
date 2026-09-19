"""Phase 4: ATC's voice. The radio effect, voice selection, the voice-out service, and speech pacing.

Piper itself is exercised by ``pytest -m piper`` (it needs the downloaded voice); everything here runs
without it.
"""

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path

import msgspec
import numpy as np
import pytest

from localtc.airports import load_airport_dir
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.bus import EventBus
from localtc.dsp.radio import bandpass, radio_effect, resample
from localtc.replay import Recording
from localtc.sim_api import AirportData, AtcTransmission, AtisBroadcast, OwnshipState, RadioTuned, Transcript
from localtc.tts.player import Clip
from localtc.tts.service import VoiceOut, radio_words
from localtc.tts.voices import speaker_for

FIXTURES = Path(__file__).parent / "fixtures"
RATE = 22050


def band_energy(audio: np.ndarray, rate: int, low: float, high: float) -> float:
    spectrum = np.abs(np.fft.rfft(audio)) ** 2
    f = np.fft.rfftfreq(len(audio), 1 / rate)
    return float(spectrum[(f >= low) & (f < high)].sum() / spectrum.sum())


# --- the radio effect ----------------------------------------------------------------------------------------


def test_the_radio_passes_voice_frequencies_only():
    noise = np.random.default_rng(1).standard_normal(RATE).astype(np.float32)
    filtered = bandpass(noise, RATE)
    assert band_energy(filtered, RATE, 300, 3400) > 0.9
    assert band_energy(noise, RATE, 300, 3400) < 0.35


def test_radio_effect_is_deterministic_bounded_and_adds_squelch():
    t = np.arange(RATE) / RATE
    voice = (np.sin(2 * np.pi * 440 * t) * np.linspace(0.05, 1.0, RATE)).astype(np.float32)
    a, b = radio_effect(voice, RATE, seed=3), radio_effect(voice, RATE, seed=3)
    assert np.array_equal(a, b)
    assert np.max(np.abs(a)) == pytest.approx(0.9, abs=1e-3)
    assert len(a) > len(voice)  # the key-up and the squelch tail
    assert len(radio_effect(voice, RATE, squelch=False)) == len(voice)
    # Compression: the quiet start comes out much closer to the loud end than 1:20.
    body = radio_effect(voice, RATE, static=0.0, squelch=False)
    quiet, loud = np.abs(body[1000:3000]).max(), np.abs(body[-3000:-1000]).max()
    assert quiet / loud > 0.3


def test_resample_keeps_duration():
    audio = np.zeros(22050, dtype=np.float32)
    assert len(resample(audio, 22050, 48000)) == 48000


# --- words and voices ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "said"), [
    ("altimeter 29.92", "altimeter two niner point niner two"),
    ("maintain 1,500", "maintain one five zero zero"),
    ("contact tower", "contact tower"),
])
def test_numbers_in_free_text_are_read_digit_by_digit(text, said):
    assert radio_words(text) == said


def test_each_station_keeps_its_voice():
    assert speaker_for("Phoenix Tower", 904) == speaker_for("phoenix tower", 904)
    voices = {speaker_for(name, 904, tuple(range(40))) for name in
              ("Phoenix Tower", "Phoenix Approach", "Goodyear Ground", "Goodyear Tower", "Albuquerque Center")}
    assert len(voices) >= 4  # different stations, different voices (mostly)
    assert speaker_for("Phoenix Tower", 1) is None  # a single-speaker voice


# --- the service ------------------------------------------------------------------------------------------------


@dataclass
class FakeSpeech:
    audio: np.ndarray
    rate: int = RATE
    latency_ms: float = 5.0

    @property
    def seconds(self) -> float:
        return len(self.audio) / self.rate


class FakeSynth:
    speakers = 904

    def __init__(self) -> None:
        self.said: list[tuple[str, int | None]] = []

    def synthesize(self, text, speaker=None):
        self.said.append((text, speaker))
        return FakeSpeech(np.sin(np.arange(2205) / 3).astype(np.float32))


class FakePlayer:
    def __init__(self) -> None:
        self.played: list[Clip] = []
        self.cuts: list[str] = []

    def play(self, clip: Clip) -> Clip:
        self.played.append(clip)
        threading.Timer(0.01, clip.done.set).start()
        return clip

    def cut(self, kind: str) -> None:
        self.cuts.append(kind)


def speak(events, *, settle: float = 0.1) -> tuple[FakeSynth, FakePlayer]:
    synth, player = FakeSynth(), FakePlayer()

    async def main():
        bus = EventBus()
        service = VoiceOut(bus, synth, player)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0)
        for event in events:
            bus.publish(event)
            await asyncio.sleep(settle)
        bus.close()
        await task

    asyncio.run(main())
    return synth, player


def test_atc_and_the_copilot_are_spoken_in_their_own_voices():
    synth, player = speak([
        AtcTransmission(t=1.0, station="Paine Tower", frequency_mhz=120.2, text="N172LT, cleared for takeoff.",
                        spoken="november one seven two lima tango, cleared for takeoff."),
        Transcript(t=5.0, text="Cleared for takeoff, 2LT", source="copilot"),
        Transcript(t=9.0, text="Paine Tower, N172LT, ready", source="voice"),  # the real pilot: not spoken back
    ])
    assert [c.kind for c in player.played] == ["atc", "pilot"]
    assert synth.said[0][0].startswith("november one seven two")  # the spoken form, not the display text
    assert synth.said[0][1] != synth.said[1][1]


def test_the_atis_repeats_until_tuned_away():
    atis = AtisBroadcast(t=1.0, airport="KPAE", station="Paine Field", frequency_mhz=128.65, letter="C",
                         text="Paine Field information Charlie.", spoken="paine field information charlie.")
    synth, player = speak([atis, RadioTuned(t=9.0, frequency_mhz=121.8, controller="ground", station="Paine Ground")],
                          settle=2.5)
    assert len(player.played) >= 2 and all(c.kind == "atis" for c in player.played)
    assert player.cuts == ["atis"]
    assert len(synth.said) == 1  # synthesized once, replayed


# --- pacing: ATC waits for its own words --------------------------------------------------------------------------


def _engine(speech_s_per_char: float) -> tuple[AtcEngine, OwnshipState]:
    engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, callsign="N172LT", seed=7,
                                    speech_s_per_char=speech_s_per_char, unscripted=False))
    for airport in load_airport_dir(FIXTURES / "airports"):
        engine.handle(AirportData(t=0.0, airport=airport))
    own = next(e for e in Recording(FIXTURES / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState))
    own = msgspec.structs.replace(own, com1_mhz=127.25)
    engine.handle(own)
    return engine, own


def test_readback_correct_waits_until_the_clearance_has_been_spoken():
    times = {}
    for pace in (0.0, 0.06):
        engine, own = _engine(pace)
        engine.handle(Transcript(t=own.t + 1, text="Paine Clearance, N172LT, IFR to Boeing Field, ready to copy"))
        out = []
        for dt in range(2, 40):
            out += [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + dt)) if isinstance(o, AtcTransmission)]
        clearance = out[0]
        engine.handle(Transcript(t=clearance.t + 0.5, text="Cleared to Boeing Field, climb and maintain 5000, departure "
                                                           "124.675, squawk " + engine.state.assignments.squawk + ", 2LT"))
        for dt in range(1, 30):
            out += [o for o in engine.handle(msgspec.structs.replace(own, t=clearance.t + dt)) if isinstance(o, AtcTransmission)]
        times[pace] = out[1].t - clearance.t
    assert times[0.06] > times[0.0] + 5  # a long clearance takes ~10 s to say


def test_a_whole_flight_flows_with_atc_speaking_at_voice_pace():
    from localtc.scenario import load_scenario, run

    path = Path(__file__).parent / "scenarios" / "copilot_full.toml"
    lines = run(load_scenario(path), path.parent, speech_s_per_char=0.055).lines
    phases = [line.split("->")[1].split("(")[0].strip() for line in lines if " PHASE " in line]
    assert phases[-3:] == ["LANDING", "TAXI_IN", "PARKED"]
    assert not [line for line in lines if "say again" in line or "ALERT" in line or "incorrect" in line]
