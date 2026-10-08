"""The voices behind one interface (localtc.tts.providers): Piper, Kokoro and Azure given the same words, each person
the same voice every time, the radio and intercom as before, and every way a voice can fail falling back in the same
order (the chosen one, Kokoro, Piper, then text) without ever stopping ATC.

The real synthesizers aren't needed: Kokoro gets a fake engine, Azure a fake transport. ``-m voices`` runs Piper and
Kokoro for real (they need their downloads) and checks they keep up with speech.
"""

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from localtc.bus import EventBus
from localtc.sim_api import AtcTransmission, AtisBroadcast, CrewSpeech, RadioChatter, Transcript
from localtc.tts import azure, kokoro
from localtc.tts.aviation import digits_in, speakable
from localtc.tts.azure import AudioCache, AzureVoices, Usage, ssml
from localtc.tts.kokoro import KokoroVoices
from localtc.tts.persona import Casting, persona_for
from localtc.tts.player import Clip
from localtc.tts.providers import PiperVoices, TtsError, VoiceChain, check_audio
from localtc.tts.service import VoiceOut
from localtc.tts.synth import Speech

RATE = 22050
PHRASES = [
    "Delta 234, climb and maintain FL350, squawk 4521.",
    "Speedbird 12, runway 27L, cleared for takeoff, wind 270 at 8.",
    "American 45, turn left heading 250, descend and maintain 6,000, contact approach 119.2.",
    "Cessna 2LT, taxi to runway 34R via A, B, hold short of runway 34L.",
    "United 9, cleared to land runway 16C, altimeter 29.92.",
    "N172LT, information C is current.",
]


def tone(seconds: float = 0.5, rate: int = RATE) -> np.ndarray:
    return (0.5 * np.sin(np.arange(int(seconds * rate)) / 3)).astype(np.float32)


# --- fakes --------------------------------------------------------------------------------------------------------


@dataclass
class FakeSpeech:
    audio: np.ndarray
    rate: int = RATE
    latency_ms: float = 5.0


class FakeSynth:
    """Piper's synthesizer, deterministic: the audio depends on the words and the speaker."""

    speakers = 904

    def __init__(self) -> None:
        self.said: list[tuple[str, int | None]] = []

    def synthesize(self, text, speaker=None):
        self.said.append((text, speaker))
        seed = (len(text) * 31 + (speaker or 0)) % 97
        return FakeSpeech((0.5 * np.sin(np.arange(2205) / (2 + seed / 50))).astype(np.float32))


class Fake:
    """A provider that does what it's told: speak, fail with a kind, hang, or answer garbage."""

    local = True

    def __init__(self, pid: str, *, fail: str = "", delay: float = 0.0, audio=None, rate: int = 24000) -> None:
        self.id = self.name = pid
        self.fail, self.delay, self.audio, self.rate = fail, delay, audio, rate
        self.said: list[str] = []
        self.not_ready = ""

    def ready(self) -> str:
        return self.not_ready

    def synthesize(self, text, persona):
        self.said.append(text)
        if self.delay:
            time.sleep(self.delay)
        if self.fail == "crash":
            raise RuntimeError("synthesizer bug")
        if self.fail:
            raise TtsError(self.fail, "told to", retry_after=5.0)
        return Speech(tone(0.5, self.rate) if self.audio is None else self.audio, self.rate, 12.0, self.id, "v1")


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


class FakeKokoro:
    """kokoro_onnx.Kokoro: a style vector per voice, audio from create()."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def get_voice_style(self, name):
        return np.full((4, 1, 8), float(len(name)), dtype=np.float32)

    def create(self, text, voice, speed=1.0, lang="en-us"):
        self.calls.append((text, lang, speed))
        return tone(0.4, 24000), 24000


class AzureFake:
    """Azure's REST API: records each request; answers PCM, or the status it's told to."""

    def __init__(self, status: int = 200, headers: dict | None = None, body: bytes | None = None, voices: list | None = None):
        self.status, self.headers, self.body, self.voices = status, headers or {}, body, voices
        self.requests: list[tuple[str, str, bytes | None, dict]] = []

    def __call__(self, method, url, body, headers, timeout_s):
        self.requests.append((method, url, body, headers))
        if method == "GET":
            return (200, {}, json.dumps(self.voices).encode()) if self.voices is not None else (404, {}, b"")
        if isinstance(self.status, Exception):
            raise self.status
        pcm = (tone(0.5, 24000) * 32767).astype("<i2").tobytes() if self.body is None else self.body
        return self.status, self.headers, pcm if self.status == 200 else (self.body or b"refused")

    @property
    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]


def azure_voices(transport=None, **kw) -> AzureVoices:
    return AzureVoices("k3y", "eastus", transport=transport or AzureFake(), **kw)


def speak(events, voices, *, settle: float = 0.15, **kw) -> FakePlayer:
    player = FakePlayer()

    async def main():
        bus = EventBus()
        service = VoiceOut(bus, voices, player, **kw)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0)
        for event in events:
            bus.publish(event)
            await asyncio.sleep(settle)
        bus.close()
        await task

    asyncio.run(main())
    return player


def atc(text: str, station: str = "Paine Tower", t: float = 1.0, **kw) -> AtcTransmission:
    return AtcTransmission(t=t, station=station, frequency_mhz=120.2, text=text, controller="tower", **kw)


# --- the words: aviation English, the same for every voice --------------------------------------------------------


@pytest.mark.parametrize(("text", "said"), [
    ("altimeter 29.92", "altimeter two niner point niner two"),
    ("maintain 1,500", "maintain one five zero zero"),
    ("climb FL350", "climb flight level three five zero"),
    ("runway 27L", "runway two seven left"),
    ("runway 16C", "runway one six center"),
    ("contact approach 119.2", "contact approach one one niner point two"),
    ("squawk 4521", "squawk four five two one"),
    ("N172LT", "november one seven two lima tango"),
    ("Cessna 2LT", "Cessna two lima tango"),
    ("taxi via A, B, hold short", "taxi via alpha, bravo, hold short"),
    ("information C.", "information charlie."),
    ("heading 090", "heading zero niner zero"),
    ("A Cessna is behind you", "A Cessna is behind you"),  # an article, not a taxiway
    ("I have the field", "I have the field"),
    ("cleared for takeoff", "cleared for takeoff"),
])
def test_aviation_words(text, said):
    assert speakable(text) == said


@pytest.mark.parametrize("text", PHRASES)
def test_no_number_is_left_for_a_voice_to_read_its_own_way_and_none_changes(text):
    words = speakable(text)
    assert not any(c.isdigit() for c in words)
    assert digits_in(words) == digits_in(text)  # every digit kept, in order
    assert speakable(words) == words  # already speakable: unchanged


def test_atcs_own_spoken_words_pass_through():
    spoken = "speedbird one two, runway two seven left, cleared for takeoff, wind two seven zero at eight."
    assert speakable(spoken) == spoken


@pytest.mark.parametrize("text", PHRASES)
def test_every_provider_is_given_the_identical_words(text):
    synth, kokoro_engine, transport = FakeSynth(), FakeKokoro(), AzureFake()
    providers = [azure_voices(transport), KokoroVoices(Path("/nonexistent"), engine=kokoro_engine), PiperVoices(synth)]
    words = speakable(text)
    for provider in providers:
        speech = check_audio(provider.synthesize(words, persona_for("Paine Tower", manner="tower")), words)
        assert speech.audio.dtype == np.float32 and speech.audio.ndim == 1
    sent = transport.posts[0][2].decode()
    assert f">{words}<" in sent or f">{words}</prosody>" in sent  # the words, as they are, inside the SSML
    assert kokoro_engine.calls[0][0] == words
    assert synth.said[0][0] == words


def test_the_service_hands_every_provider_the_same_line_when_the_first_ones_fail():
    first, second, piper = Fake("azure", fail="network"), Fake("kokoro", fail="timeout"), FakeSynth()
    chain = VoiceChain([first, second, PiperVoices(piper)])
    speak([atc("Delta 234, climb FL350.")], chain)
    assert first.said == second.said == [piper.said[0][0]] == ["Delta two three four, climb flight level three five zero."]


# --- the people: stable, different, regional ---------------------------------------------------------------------


def test_a_station_is_the_same_person_every_time():
    a = persona_for("Phoenix Tower", "atc", manner="tower:hurried", locale="en-US")
    assert a == persona_for("Phoenix Tower", "atc", manner="tower:hurried", locale="en-US")
    assert a.sex in ("F", "M") and -6 <= a.pitch <= 6
    assert persona_for("phoenix tower").sex == a.sex  # the name, whatever its case


def test_roles_are_different_people():
    keys = {persona_for("", role).key for role in ("copilot", "cabin", "ground_crew")}
    assert len(keys) == 3
    assert persona_for("", "copilot", sex="F").sex == "F" and persona_for("", "copilot", sex="male").sex == "M"
    atis = persona_for("Paine Field ATIS", "atis")
    assert atis.manner == "atis" and atis.pitch == 0 and atis.style == ""  # the recording: even, no manner
    assert persona_for("Paine Tower", "atc", manner="tower:calm").style == "calm"
    assert persona_for("Paine Tower", "atc", manner="tower:hurried").style == ""  # nothing that would exaggerate


def test_the_copilot_keeps_its_sex_in_every_provider():
    from localtc.tts.voices import sex_of

    for speaker in (235, 612, 149, 372):
        p = persona_for("", "copilot", piper_speaker=speaker)
        assert p.sex == sex_of(speaker)
        _, voice = KokoroVoices(Path("/x"), engine=FakeKokoro()).voice_for(p)
        assert voice[1] == p.sex.lower()  # af_/am_/bf_/bm_


def test_casting_gives_each_person_their_own_voice_while_there_are_enough():
    casting = Casting()
    pool = ["v1", "v2", "v3", "v4"]
    stations = ["Paine Tower", "Paine Ground", "Seattle Approach", "Seattle Center"]
    cast = [casting.cast(persona_for(s), pool) for s in stations]
    assert sorted(cast) == pool  # four people, four voices
    assert casting.cast(persona_for("Paine Tower"), pool) == cast[0]  # and each keeps theirs
    again = Casting()
    assert [again.cast(persona_for(s), pool) for s in stations] == cast  # the same flight, the same cast
    assert casting.cast(persona_for("Boeing Tower"), pool) in pool  # a fifth shares


def test_the_pilots_own_pick_stands():
    casting = Casting()
    pool = ["v1", "v2", "v3"]
    assert casting.cast(persona_for("", "copilot", pick=1), pool) == "v2"
    assert casting.cast(persona_for("", "copilot", pick=1), pool) == "v2"


@pytest.mark.parametrize(("locale", "lang"), [("en-GB", "en-gb"), ("en-AU", "en-gb"), ("en-US", "en-us"), ("", "en-us"),
                                              ("en-CA", "en-us")])
def test_kokoro_speaks_the_regions_english(locale, lang):
    engine = FakeKokoro()
    voices = KokoroVoices(Path("/x"), engine=engine)
    voices.synthesize("cleared to land", persona_for("Heathrow Tower", locale=locale))
    assert engine.calls[0][1] == lang


def test_kokoro_blends_are_voices_of_their_own():
    voices = KokoroVoices(Path("/x"), engine=FakeKokoro())
    blend = voices.style("am_michael+am_fenrir")
    assert np.allclose(blend, 0.6 * len("am_michael") + 0.4 * len("am_fenrir"))


@pytest.mark.parametrize(("locale", "prefix"), [("en-GB", "en-GB-"), ("en-AU", "en-AU-"), ("en-US", "en-US-"),
                                                ("en-IE", "en-IE-"), ("", "en-US-")])
def test_azure_casts_from_the_regions_voices(locale, prefix):
    voices = azure_voices()
    loc, voice = voices.voice_for(persona_for("Heathrow Tower", locale=locale))
    assert voice.startswith(prefix) or (locale == "en-IE" and voice.startswith("en-GB-"))


def test_azure_reads_the_regions_voice_list_and_leaves_out_childrens_and_previews():
    listed = [
        {"ShortName": "en-GB-RyanNeural", "Locale": "en-GB", "Gender": "Male", "VoiceType": "Neural", "Status": "GA",
         "StyleList": ["cheerful", "chat"]},
        {"ShortName": "en-GB-MaisieNeural", "Locale": "en-GB", "Gender": "Female", "VoiceType": "Neural", "Status": "GA"},
        {"ShortName": "en-GB-SoniaNeural", "Locale": "en-GB", "Gender": "Female", "VoiceType": "Neural", "Status": "GA"},
        {"ShortName": "en-GB-OllieMultilingualNeural", "Locale": "en-GB", "Gender": "Male", "VoiceType": "Neural"},
        {"ShortName": "fr-FR-DeniseNeural", "Locale": "fr-FR", "Gender": "Female", "VoiceType": "Neural", "Status": "GA"},
    ]
    voices = azure_voices(AzureFake(voices=listed)).voices()
    assert voices == {"en-GB": {"M": ("en-GB-RyanNeural",), "F": ("en-GB-SoniaNeural",)}}


# --- SSML and Azure's allowance ----------------------------------------------------------------------------------


def test_ssml_escapes_the_words_and_bills_only_what_azure_bills():
    doc, billable = ssml("Tom & Jerry <3", "en-US-GuyNeural", "en-US")
    assert "Tom &amp; Jerry &lt;3" in doc and billable == len("Tom &amp; Jerry &lt;3")
    doc, billable = ssml("cleared to land", "en-US-GuyNeural", "en-US", rate=1.15, pitch=-3)
    assert '<prosody rate="+15%" pitch="-3%">cleared to land</prosody>' in doc
    assert billable == len('<prosody rate="+15%" pitch="-3%">cleared to land</prosody>')
    assert "prosody" not in ssml("x", "v", "en-US", rate=1.0, pitch=0)[0]  # no markup that changes nothing


def test_azure_speaks_the_regions_voice_with_its_pace_and_pitch():
    transport = AzureFake()
    voices = azure_voices(transport, rate=1.1)
    speech = voices.synthesize("cleared to land", persona_for("Heathrow Tower", locale="en-GB", manner="tower"))
    method, url, body, headers = transport.posts[0]
    assert url == "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
    assert headers["Ocp-Apim-Subscription-Key"] == "k3y" and headers["X-Microsoft-OutputFormat"] == "raw-24khz-16bit-mono-pcm"
    assert headers["Content-Type"] == "application/ssml+xml"
    assert b'xml:lang="en-GB"' in body and b"<voice name=\"en-GB-" in body
    assert speech.rate == 24000 and speech.provider == "azure" and abs(speech.audio).max() > 0.4


def test_azure_counts_its_characters_by_month_and_keeps_them(tmp_path):
    month = ["2026-10"]
    usage = Usage(tmp_path / "usage.json", limit=1000, month=lambda: month[0])
    voices = azure_voices(usage=usage)
    voices.synthesize("cleared to land", persona_for("A Tower", "atis"))  # no pace or pitch: just the words
    assert usage.chars == len("cleared to land") and usage.summary()["calls"] == 1
    assert Usage(tmp_path / "usage.json", month=lambda: month[0]).chars == usage.chars  # kept on disk
    month[0] = "2026-11"
    assert usage.chars == 0  # a new month


def test_azure_stops_at_the_monthly_allowance_and_piper_carries_on(tmp_path):
    usage = Usage(tmp_path / "u.json", limit=100)
    usage.add(97)
    transport, piper = AzureFake(), FakeSynth()
    chain = VoiceChain([azure_voices(transport, usage=usage), PiperVoices(piper)])
    speech = chain.speak("cleared to land runway two seven left", persona_for("A Tower"))
    assert speech.provider == "piper" and not transport.posts  # nothing sent past the allowance
    row = chain.status()[0]
    assert row["failures"] == {"quota": 1} and row["resting_s"] > 0 and row["usage"]["left"] == 3


def test_azure_keeps_to_twenty_requests_a_minute():
    clock = [0.0]
    voices = azure_voices(clock=lambda: clock[0])
    for _ in range(20):
        voices._take_slot()
    with pytest.raises(TtsError) as exc:
        voices._take_slot()
    assert exc.value.kind == "rate_limit" and exc.value.retry_after == pytest.approx(60.0)
    clock[0] = 61.0
    voices._take_slot()


def test_a_line_heard_before_comes_from_the_cache_and_costs_nothing(tmp_path):
    transport = AzureFake()
    usage = Usage(None)
    voices = azure_voices(transport, usage=usage, cache=AudioCache(tmp_path))
    who = persona_for("Paine Field ATIS", "atis")
    a = voices.synthesize("paine field information charlie", who)
    chars = usage.chars
    b = voices.synthesize("paine field information charlie", who)
    assert len(transport.posts) == 1 and usage.chars == chars and usage.summary()["cached"] == 1
    assert np.allclose(a.audio, b.audio, atol=1 / 32768)


def test_the_cache_stays_within_its_size(tmp_path):
    cache = AudioCache(tmp_path, max_mb=0.001)  # about 1 kB
    for i in range(5):
        cache.put(f"k{i}", b"\x00\x01" * 300)
    assert sum(p.stat().st_size for p in tmp_path.glob("*.pcm")) <= 1100


@pytest.mark.parametrize(("status", "headers", "body", "kind"), [
    (401, {}, b"", "auth"),
    (403, {}, b"Out of call volume quota.", "quota"),
    (429, {"Retry-After": "7"}, b"", "rate_limit"),
    (500, {}, b"oops", "unavailable"),
    (503, {}, b"", "unavailable"),
    (400, {}, b"bad ssml", "error"),
])
def test_azures_refusals_are_told_apart(status, headers, body, kind):
    voices = azure_voices(AzureFake(status=status, headers=headers, body=body))
    with pytest.raises(TtsError) as exc:
        voices.synthesize("cleared to land", persona_for("A Tower"))
    assert exc.value.kind == kind
    if kind == "rate_limit":
        assert exc.value.retry_after == 7.0


@pytest.mark.parametrize(("error", "kind"), [(TimeoutError("timed out"), "timeout"), (OSError("no route"), "network")])
def test_azure_unreachable(error, kind):
    with pytest.raises(TtsError) as exc:
        azure_voices(AzureFake(status=error)).synthesize("cleared to land", persona_for("A Tower"))
    assert exc.value.kind == kind


def test_azure_needs_a_key_and_a_region():
    assert "key" in AzureVoices("", "eastus").ready()
    assert "region" in AzureVoices("k", "").ready()
    assert "region" in AzureVoices("k", "east us; drop").ready()
    assert AzureVoices("k", "westeurope").ready() == ""


# --- falling back -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["timeout", "quota", "rate_limit", "network", "unavailable", "auth", "error", "crash"])
def test_every_failure_falls_back_to_the_next_voice(kind):
    first, piper = Fake("azure", fail=kind), FakeSynth()
    chain = VoiceChain([first, PiperVoices(piper)])
    speech = chain.speak("cleared to land", persona_for("A Tower"))
    assert speech is not None and speech.provider == "piper"
    assert chain.status()[0]["failures"] == {"error" if kind == "crash" else kind: 1}


def test_the_fallback_order_is_the_chosen_one_then_kokoro_then_piper():
    from localtc.app import voice_chain
    from localtc.config import Config

    cfg = Config()
    cfg.tts.provider, cfg.tts.kokoro = "azure", True
    assert [p.id for p in voice_chain(cfg, FakeSynth(), key="k").providers] == ["azure", "kokoro", "piper"]
    cfg.tts.provider, cfg.tts.kokoro = "kokoro", False
    assert [p.id for p in voice_chain(cfg, FakeSynth()).providers] == ["kokoro", "piper"]
    cfg.tts.provider = "piper"
    assert [p.id for p in voice_chain(cfg, FakeSynth()).providers] == ["piper"]
    assert voice_chain(cfg, None) is None  # nothing at all: text only


def test_cloud_is_off_unless_chosen():
    from localtc.config import Config

    cfg = Config()
    assert cfg.tts.provider == "piper" and not cfg.tts.kokoro


def test_a_failed_provider_rests_and_comes_back():
    clock = [0.0]
    first, piper = Fake("azure", fail="network"), FakeSynth()
    chain = VoiceChain([first, PiperVoices(piper)], clock=lambda: clock[0])
    chain.speak("one", persona_for("A Tower"))
    chain.speak("two", persona_for("A Tower"))
    assert first.said == ["one"]  # resting: not asked again at once
    clock[0] = 31.0
    first.fail = ""
    assert chain.speak("three", persona_for("A Tower")).provider == "azure"


def test_a_refused_key_is_out_until_its_changed():
    clock = [0.0]
    first = Fake("azure", fail="auth")
    chain = VoiceChain([first, PiperVoices(FakeSynth())], clock=lambda: clock[0])
    chain.speak("one", persona_for("A Tower"))
    clock[0] = 10_000.0
    chain.speak("two", persona_for("A Tower"))
    assert first.said == ["one"] and chain.status()[0]["why"]
    first.fail = ""
    chain.reset("azure")
    assert chain.speak("three", persona_for("A Tower")).provider == "azure"


def test_a_slow_voice_is_given_up_on_in_time_and_left_to_finish():
    slow, piper = Fake("kokoro", delay=0.6), FakeSynth()
    chain = VoiceChain([slow, PiperVoices(piper)], timeout_s=0.15)
    started = time.monotonic()
    assert chain.speak("one", persona_for("A Tower")).provider == "piper"
    assert time.monotonic() - started < 0.5  # not the slow one's 0.6 s
    assert chain.speak("two", persona_for("A Tower")).provider == "piper"  # resting after its time out
    assert slow.said == ["one"]


@pytest.mark.parametrize(("audio", "why"), [
    (np.zeros(0, dtype=np.float32), "no audio"),
    (np.full(1000, np.nan, dtype=np.float32), "not finite"),
    (np.zeros(24000, dtype=np.float32), "silent"),
    (tone(60, 24000), "for"),  # a minute of audio for a few words
    (np.ones((3, 3, 3), dtype=np.float32), "shape"),
])
def test_bad_audio_is_never_played(audio, why):
    bad, piper = Fake("kokoro", audio=audio), FakeSynth()
    chain = VoiceChain([bad, PiperVoices(piper)])
    assert chain.speak("cleared to land", persona_for("A Tower")).provider == "piper"
    assert why in chain.status()[0]["last_error"]


def test_audio_is_made_mono_float_within_range():
    stereo = np.stack([tone(0.5), tone(0.5)], axis=1) * 3
    speech = check_audio(Speech(stereo, RATE, 1.0, "x", "v"), "cleared to land")
    assert speech.audio.ndim == 1 and speech.audio.dtype == np.float32 and np.abs(speech.audio).max() <= 1.0
    pcm = (tone(0.5) * 32767).astype(np.int16)
    assert check_audio(Speech(pcm, RATE, 1.0, "x", "v"), "cleared").audio.dtype == np.float32


def test_with_no_voice_atc_is_text_and_carries_on():
    chain = VoiceChain([Fake("azure", fail="network"), Fake("kokoro", fail="unavailable")])
    player = speak([atc("cleared to land", t=1.0), atc("contact ground", t=2.0)], chain)
    assert player.played == [] and chain.text_only == 2


def test_a_voice_crashing_never_stops_the_next_line():
    class Flaky(Fake):
        def synthesize(self, text, persona):
            if "first" in text:
                raise MemoryError("out of memory")  # not even a TtsError
            return super().synthesize(text, persona)

    chain = VoiceChain([Flaky("kokoro"), Fake("piper")])
    player = speak([atc("first"), atc("second", t=2.0)], chain)
    assert len(player.played) == 2 and chain.status()[0]["failures"] == {"error": 1}


def test_the_service_survives_a_bug_of_its_own(monkeypatch):
    calls = []

    def render(self, text, who, kind, manner=None):
        calls.append(text)
        if len(calls) == 1:
            raise ValueError("bug")
        return Clip(tone(), RATE, kind)

    monkeypatch.setattr(VoiceOut, "render", render)
    player = speak([atc("one"), atc("two", t=2.0)], VoiceChain([Fake("p")]))
    assert len(calls) == 2 and len(player.played) == 1


def test_a_provider_that_isnt_ready_is_skipped_without_counting_as_a_call():
    first = Fake("kokoro")
    first.not_ready = "model not downloaded"
    chain = VoiceChain([first, PiperVoices(FakeSynth())])
    assert chain.speak("x ray", persona_for("A")).provider == "piper"
    row = chain.status()[0]
    assert row["calls"] == 0 and row["why"] == "model not downloaded" and not row["ready"]


def test_kokoro_without_its_download_says_so():
    voices = KokoroVoices(Path("/nonexistent"))
    assert voices.ready()
    with pytest.raises(TtsError) as exc:
        voices.synthesize("x", persona_for("A"))
    assert exc.value.kind == "unavailable"


# --- the radio and the intercom, whoever speaks -------------------------------------------------------------------


@pytest.mark.parametrize("rate", [22050, 24000])
def test_radio_and_intercom_are_kept_for_every_provider(rate):
    chain = VoiceChain([Fake("azure", rate=rate)])
    player = speak([
        atc("cleared to land"),
        RadioChatter(t=2.0, station="Paine Tower", frequency_mhz=120.2, speaker="pilot", callsign="Alaska 12", text="Alaska 12, wilco"),
        Transcript(t=3.0, text="Cleared to land, 2LT", source="copilot"),
        CrewSpeech(t=4.0, text="Flaps two."),
    ], chain)
    kinds = [c.kind for c in player.played]
    assert kinds == ["atc", "atc", "pilot", "intercom"]
    n = int(0.5 * rate)
    radio, chatter, pilot, intercom = player.played
    assert len(radio.audio) > n  # key-up and squelch tail
    assert len(pilot.audio) == n and len(intercom.audio) == n  # no squelch on our side, none on the intercom
    assert np.abs(radio.audio).max() == pytest.approx(0.9, abs=1e-3)
    assert np.abs(intercom.audio).max() == pytest.approx(0.8, abs=1e-3)
    assert all(c.rate == rate for c in player.played)


def test_the_atis_still_loops_and_stops():
    from localtc.sim_api import RadioTuned

    synth = FakeSynth()
    atis = AtisBroadcast(t=1.0, airport="EGLL", station="Heathrow", frequency_mhz=128.075, letter="C",
                         text="Heathrow information Charlie.", spoken="heathrow information charlie.", locale="en-GB")
    player = speak([atis, RadioTuned(t=9.0, frequency_mhz=121.9, controller="ground", station="Heathrow Ground")],
                   VoiceChain([PiperVoices(synth)]), settle=2.5)
    assert len(player.played) >= 2 and player.cuts == ["atis"] and len(synth.said) == 1


def test_each_station_speaks_its_regions_english():
    engine = FakeKokoro()
    chain = VoiceChain([KokoroVoices(Path("/x"), engine=engine)])
    speak([atc("cleared to land", station="Heathrow Tower", locale="en-GB"),
           atc("cleared to land", station="Paine Tower", t=2.0, locale="en-US")], chain)
    assert [c[1] for c in engine.calls] == ["en-gb", "en-us"]
    engine2 = FakeKokoro()
    speak([atc("cleared to land", station="Heathrow Tower", locale="en-GB")],
          VoiceChain([KokoroVoices(Path("/x"), engine=engine2)]), regional=False)
    assert engine2.calls[0][1] == "en-us"


def test_the_copilots_voice_is_its_own_in_every_provider():
    engine = FakeKokoro()
    voices = KokoroVoices(Path("/x"), engine=engine)
    chain = VoiceChain([voices])
    speak([CrewSpeech(t=1.0, text="Flaps two."), atc("cleared to land", t=2.0)], chain, crew_sex="F", crew_pick=0)
    assert voices.casting.given["pilot"] == "af_heart"  # the first of the female voices: the pilot's pick
    assert voices.casting.given["Paine Tower"] != "af_heart"


# --- replay: the same flight sounds the same ---------------------------------------------------------------------


def flight() -> list:
    return [
        atc("Delta 234, cleared to land runway 27L.", station="Heathrow Tower", t=1.0, locale="en-GB"),
        atc("Delta 234, contact ground 121.9.", station="Heathrow Ground", t=2.0, locale="en-GB"),
        Transcript(t=3.0, text="Ground 121.9, Delta 234", source="copilot"),
        RadioChatter(t=4.0, station="Heathrow Ground", frequency_mhz=121.9, speaker="pilot", callsign="Speedbird 9",
                     text="Speedbird 9, holding short"),
    ]


@pytest.mark.parametrize("make", [lambda: PiperVoices(FakeSynth()), lambda: KokoroVoices(Path("/x"), engine=FakeKokoro()),
                                  lambda: azure_voices()])
def test_a_replay_sounds_the_same(make):
    runs = []
    for _ in range(2):
        provider = make()
        player = speak(flight(), VoiceChain([provider]), settle=0.05)
        runs.append(([c.audio for c in player.played], dict(getattr(provider, "casting", Casting()).given)))
    (audio_a, cast_a), (audio_b, cast_b) = runs
    assert len(audio_a) == 4 and all(np.array_equal(a, b) for a, b in zip(audio_a, audio_b, strict=True))
    assert cast_a == cast_b


def test_the_clips_kept_for_replay_are_the_processed_audio(monkeypatch):
    from localtc.dsp.clips import CLIPS, clip_id

    monkeypatch.setattr(CLIPS, "enabled", True)
    CLIPS.clear()
    ev = atc("cleared to land")
    speak([ev], VoiceChain([Fake("azure")]))
    assert CLIPS.get(clip_id(ev)) is not None
    CLIPS.clear()


# --- latency and real time -----------------------------------------------------------------------------------------


def test_the_status_reports_latency_and_real_time_factor():
    chain = VoiceChain([Fake("kokoro")])
    for i in range(3):
        chain.speak(f"line {i}", persona_for("A"))
    row = chain.status()[0]
    assert row["latency_ms"] == 12 and row["rtf"] == pytest.approx(12 / 1000 / 0.5, abs=0.001)
    assert row["ok"] == 3 and row["speaking"]


def _downloaded():
    from localtc.tts.voices import DEFAULT_VOICE, default_voices_dir, installed

    return installed(DEFAULT_VOICE), kokoro.installed(default_voices_dir()) and not kokoro.available()


@pytest.mark.voices
def test_piper_and_kokoro_keep_up_with_speech():
    """On this PC, the real voices: each line synthesized faster than it's spoken (Kokoro's real-time factor about
    0.2-0.3 on a fast CPU, Piper's 0.03)."""
    from localtc.tts.synth import PiperSynth
    from localtc.tts.voices import DEFAULT_VOICE, default_voices_dir, voice_path

    piper_ok, kokoro_ok = _downloaded()
    if not (piper_ok and kokoro_ok):
        pytest.skip("needs the Piper voice and the Kokoro model downloaded")
    providers = [PiperVoices(PiperSynth(voice_path(DEFAULT_VOICE), rate=1.15)),
                 KokoroVoices(default_voices_dir(), rate=1.15, threads=4)]
    for provider in providers:
        if hasattr(provider, "load"):
            provider.load()
        provider.synthesize("radio check", persona_for("warm up"))
        for text in PHRASES[:3]:
            words = speakable(text)
            speech = check_audio(provider.synthesize(words, persona_for("Paine Tower", manner="tower")), words)
            seconds = len(speech.audio) / speech.rate
            assert speech.latency_ms / 1000 / seconds < (0.2 if provider.id == "piper" else 0.8), provider.id
            assert 1.0 < seconds < 15.0


def test_engine_tells_the_voice_its_regions_english():
    from localtc.atc_core.region import accent

    assert accent("EGLL") == "en-GB" and accent("YSSY") == "en-AU" and accent("KSEA") == "en-US"
    assert accent("CYYZ") == "en-CA" and accent("EIDW") == "en-IE" and accent("VIDP") == "en-IN"
    assert accent("LFPG") == "" and accent("") == ""  # nobody's first language: no accent in particular


def test_old_recordings_without_a_locale_still_load():
    import msgspec

    from localtc.sim_api import BusEvent

    raw = b'{"type":"atc_transmission","t":1.0,"station":"Paine Tower","frequency_mhz":120.2,"text":"hi"}'
    ev = msgspec.json.decode(raw, type=BusEvent)
    assert ev.locale == ""


def test_azure_module_names_its_limits():
    assert azure.FREE_CHARS == 500_000 and azure.FREE_PER_MINUTE == 20
