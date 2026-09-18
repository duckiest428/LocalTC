"""Voice input without Whisper or a microphone: vocabulary, audio handling, the voice service, the engine's wait
for transcripts, joystick push-to-talk, and speaking scenarios. Real Whisper: tests/test_stt_whisper.py."""

import asyncio
import wave
from dataclasses import dataclass
from pathlib import Path

import msgspec
import numpy as np
import pytest

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.llm import trigger
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.bus import EventBus
from localtc.config import Config, with_recorded
from localtc.replay import Recording
from localtc.scenario import load_scenario, run
from localtc.sim_api import AtcTransmission, PttPressed, PttReleased, Transcript
from localtc.stt.audio import AudioCapture, read_wav, resample, write_wav
from localtc.stt.service import VoiceService
from localtc.stt.vocabulary import VocabularyHints, build_prompt, fixup
from localtc.voice import flight_hints, recorded_clips, spoken_callsign, token_error_rate

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
HAPPY = HERE / "scenarios" / "ifr_happy_path.toml"


# --- vocabulary ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("heard", "fixed"), [
    ("Whole short runway 16R", "hold short runway 16R"),
    ("Squak 4521", "squawk 4521"),
    ("Decent and maintain 3,000", "descend and maintain 3,000"),
    ("Climate maintain 5,000", "climb and maintain 5,000"),
    ("Runaway 34L", "runway 34L"),
    ("Expect 12,000,000 minutes after", "Expect 12,000, 10 minutes after"),
    ("Up to 1-2000, DP69", "Up to 12000, DP69"),
    ("Level 1, 2000.", "Level 12000."),
    ("Rolling 0-6 left, Delta Papa 6-9er", "Rolling 06 left, Delta Papa 69"),
    ("squawk 5-0-1-5", "squawk 5015"),
    ("FL240", "flight level 240"),
    ("Cleared for takeoff runway 34L", "Cleared for takeoff runway 34L"),  # nothing to fix
])
def test_fixups(heard, fixed):
    assert fixup(heard) == fixed


def test_the_prompt_names_the_flight_but_never_the_numbers_to_read_back():
    """Priming Whisper with the expected squawk could make it hear it when the pilot said another."""
    result = run(load_scenario(HAPPY), HAPPY.parent, voice=None)
    engine = result.engine
    hints = flight_hints(engine)
    prompt = build_prompt(hints)
    assert hints.callsign == "N172LT" and hints.callsign_spoken == "November 172 Lima Tango"
    assert "Paine Clearance" in prompt and "Boeing Tower" in prompt and "34L" in prompt and "14R" in prompt
    assert engine.state.assignments.squawk not in prompt
    assert "124.675" not in prompt.replace("Contact departure 124.675", "")  # only in the fixed style sentence
    assert prompt.endswith("This is N172LT (November 172 Lima Tango).")


def test_spoken_callsign():
    assert spoken_callsign("DP69") == "Delta Papa 69"
    assert spoken_callsign("C-GABC") == "Charlie Golf Alpha Bravo Charlie"


def test_token_error_rate_ignores_number_writing():
    assert token_error_rate("climb and maintain five thousand", "Climb and maintain 5,000.") == 0.0
    assert token_error_rate("squawk four five two one", "squawk 4512") == 0.5  # one wrong of two tokens


# --- audio -------------------------------------------------------------------------------------------------


def test_resample_and_wav_round_trip(tmp_path):
    tone = np.sin(np.linspace(0, 440 * 2 * np.pi, 48000)).astype(np.float32) * 0.5
    assert len(resample(tone, 48000)) == 16000
    assert len(resample(tone[:44100], 44100)) == 16000
    write_wav(tmp_path / "a.wav", resample(tone, 48000, 8000), 8000)
    back = read_wav(tmp_path / "a.wav")  # 8 kHz on disk, 16 kHz when read
    assert len(back) == 16000 and abs(np.max(np.abs(back)) - 0.5) < 0.02


def test_capture_keeps_the_moment_before_the_key_went_down():
    capture = AudioCapture(None, pre_roll_s=0.3)  # no device opened: blocks are fed by hand
    block = lambda value: np.full((1600, 1), value, dtype=np.float32)  # 0.1 s  # noqa: E731
    for n in range(10):
        capture._callback(block(n), 1600, None, None)  # 1 s of speech-free audio
    capture.begin()
    for n in range(10, 15):
        capture._callback(block(n), 1600, None, None)
    clip = capture.end()
    assert len(clip) == 8 * 1600  # 0.3 s of pre-roll + the 0.5 s held
    assert clip[0] == 7 and clip[-1] == 14
    assert len(capture.end()) == 0  # nothing until the next press


# --- the voice service -------------------------------------------------------------------------------------


@dataclass
class Result:
    text: str
    confidence: float = 0.9
    no_speech: float = 0.01
    latency_ms: float = 120.0
    audio_s: float = 2.0


class FakeTranscriber:
    def __init__(self, text: str = "Whole short runway 16R, Cessna 2LT") -> None:
        self.text, self.calls = text, []

    def transcribe(self, audio, *, prompt="", hotwords=""):
        self.calls.append((len(audio), prompt, hotwords))
        if self.text == "boom":
            raise RuntimeError("model crashed")
        return Result(self.text)


class FakeCapture:
    def __init__(self, seconds: float, level: float = 0.1) -> None:
        self.audio = np.full(int(seconds * 16000), level, dtype=np.float32)
        self.began = 0

    def begin(self):
        self.began += 1

    def end(self):
        return self.audio


class FakeRecorder:
    def __init__(self):
        self.saved = []

    def save_audio(self, pcm, *, sample_rate):
        self.saved.append((len(pcm), sample_rate))
        return f"audio/{len(self.saved):04d}.wav"


def talk(capture, transcriber, recorder=None, hints=None) -> list[Transcript]:
    async def main():
        bus = EventBus()
        clock = iter(range(100))
        service = VoiceService(bus, lambda: float(next(clock)), transcriber, capture, recorder=recorder,
                               hints=hints, tail_s=0.0)
        heard = bus.subscribe(Transcript)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0)
        service.press()
        await asyncio.sleep(0.01)
        service.release()
        await asyncio.sleep(0.05)
        bus.close()
        await task
        return [e async for e in heard]

    return asyncio.run(main())


def test_push_to_talk_to_transcript():
    capture, transcriber, recorder = FakeCapture(2.0), FakeTranscriber(), FakeRecorder()
    hints = lambda: VocabularyHints(callsign="N172LT", stations=("Paine Ground",))  # noqa: E731
    [transcript] = talk(capture, transcriber, recorder, hints)
    assert transcript.text == "hold short runway 16R, Cessna 2LT"  # fixed up
    assert (transcript.source, transcript.audio_ref, transcript.stt_ms) == ("voice", "audio/0001.wav", 120.0)
    assert recorder.saved == [(2 * 32000, 16000)]  # the clip, as 16-bit PCM beside the recording
    assert capture.began == 1
    _, prompt, hotwords = transcriber.calls[0]
    assert "Paine Ground" in prompt and "N172LT" in hotwords


@pytest.mark.parametrize("capture", [FakeCapture(0.1), FakeCapture(2.0, level=0.0)], ids=["click", "silence"])
def test_no_speech_gives_an_empty_transcript(capture):
    transcriber = FakeTranscriber()
    [transcript] = talk(capture, transcriber)
    assert transcript.text == "" and transcriber.calls == []


def test_a_failing_model_does_not_stop_voice_input():
    [transcript] = talk(FakeCapture(2.0), FakeTranscriber("boom"))
    assert transcript.text == ""


# --- the engine waits for the transcript --------------------------------------------------------------------------


def _parked_engine(await_transcripts: bool):
    from localtc.airports import load_airport_dir

    engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, callsign="N172LT", seed=7,
                                    await_transcripts=await_transcripts))
    from localtc.sim_api import AirportData, OwnshipState

    for airport in load_airport_dir(FIXTURES / "airports"):
        engine.handle(AirportData(t=0.0, airport=airport))
    own = next(e for e in Recording(FIXTURES / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState))
    own = msgspec.structs.replace(own, com1_mhz=127.25)  # clearance delivery
    engine.handle(own)
    return engine, own


def test_atc_waits_while_the_pilot_is_transcribed():
    engine, own = _parked_engine(True)
    tick = lambda dt: [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + dt))  # noqa: E731
                       if isinstance(o, AtcTransmission)]
    engine.handle(Transcript(t=own.t + 1, text="Paine Clearance, N172LT, IFR to Boeing Field, ready to copy"))
    engine.handle(PttPressed(t=own.t + 4))  # the pilot keys up again before ATC answers...
    engine.handle(PttReleased(t=own.t + 6))
    assert tick(7) == [] and tick(12) == []  # ...and ATC waits for what they said
    assert tick(15) != []  # up to STT_WAIT_S; the transcript never came


def test_an_empty_transcript_gets_no_reply():
    engine, own = _parked_engine(True)
    engine.handle(PttPressed(t=own.t + 1))
    engine.handle(PttReleased(t=own.t + 2))
    assert engine.handle(Transcript(t=own.t + 3, text="", source="voice")) == []
    assert [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + 10)) if isinstance(o, AtcTransmission)] == []
    assert engine.state.exchanges == type(engine.state.exchanges)(maxlen=30) or not engine.state.exchanges


def test_low_speech_confidence_asks_the_model():
    context = InterpretContext(phase="PARKED")
    grammar = GrammarInterpreter().interpret("Paine Ground, 2LT, ready to taxi", None, context)
    assert trigger(grammar, "Paine Ground, 2LT, ready to taxi", confidence=0.9) is None
    assert trigger(grammar, "Paine Ground, 2LT, ready to taxi", confidence=0.3) == "low_confidence"
    assert trigger(grammar, "Paine Ground, 2LT, ready to taxi") is None  # typed: no confidence


# --- joystick push-to-talk through the sim -----------------------------------------------------------------------


def test_joystick_button_through_simconnect():
    from helpers.sim_fakes import FakeSimConnect, build_message

    from localtc.config import LiveConfig
    from localtc.sim_bridge.protocol import RecvEvent, RecvId
    from localtc.sim_bridge.simconnect_source import EVT_PTT_DOWN, EVT_PTT_UP, SimConnectSource

    fake = FakeSimConnect()
    cfg = LiveConfig(ownship_hz=0, traffic_interval_s=0, nearest_airport_interval_s=0, retry_max_s=0.1,
                     ptt_input="joystick:0:button:3")

    async def main():
        source = SimConnectSource(cfg, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)
        fake.push(build_message(RecvEvent(uEventID=EVT_PTT_DOWN, dwData=0), RecvId.EVENT))
        fake.push(build_message(RecvEvent(uEventID=EVT_PTT_UP, dwData=0), RecvId.EVENT))
        got = []

        async def collect():
            async for ev in source.events():
                if isinstance(ev, (PttPressed, PttReleased)):
                    got.append(type(ev).__name__)
                    if len(got) == 2:
                        return

        await asyncio.wait_for(collect(), 3)
        await source.stop()
        return got

    assert asyncio.run(main()) == ["PttPressed", "PttReleased"]
    assert fake.input_maps == [("joystick:0:button:3", EVT_PTT_DOWN, EVT_PTT_UP)]


# --- speaking scenarios and voice recordings ------------------------------------------------------------------------


class ScriptVoice:
    """Speaks at 3 words a second and is heard perfectly."""

    def speak(self, text, t):
        class Clip:
            duration_s = round(len(text.split()) / 3, 1)
            audio_ref = None
            confidence = 0.9

        Clip.text = text
        return Clip


def test_a_transmission_belongs_to_the_frequency_it_was_keyed_on():
    """Reading back "... Boeing Tower 120.6" and switching to tower before the transcript arrives must not send
    the readback to tower (it was said to approach). Found by flying the whole scenario with speech."""
    spoken = run(load_scenario(HAPPY), HAPPY.parent, voice=ScriptVoice()).outputs
    assert not [o for o in spoken if isinstance(o, AtcTransmission) and o.instruction_id == "common.say_again"]


def test_spoken_transmissions_take_time():
    typed = run(load_scenario(HAPPY), HAPPY.parent).outputs
    fed = []
    spoken = run(load_scenario(HAPPY), HAPPY.parent, voice=ScriptVoice(), on_input=fed.append).outputs
    readbacks = lambda outs: [(o.instruction_id, o.status) for o in outs if type(o).__name__ == "ReadbackEvaluated"]  # noqa: E731
    assert readbacks(spoken) == readbacks(typed) and all(s == "correct" for _, s in readbacks(spoken))
    ptt = [e for e in fed if isinstance(e, (PttPressed, PttReleased, Transcript))]
    first = ptt[:3]
    assert [type(e).__name__ for e in first] == ["PttPressed", "PttReleased", "Transcript"]
    assert first[1].t - first[0].t == pytest.approx(len(first[2].text.split()) / 3, abs=0.1)


def test_the_voice_recording_pairs_audio_with_what_was_said():
    recording = Recording(FIXTURES / "voice_kpae")
    clips = recorded_clips(recording)
    assert len(clips) == 10 and all(c.audio.is_file() for c in clips)
    assert clips[0].reference.startswith("Paine Clearance, november one seven two lima tango")
    assert all(c.released_t > c.pressed_t for c in clips)
    with wave.open(str(clips[0].audio)) as wav:
        assert (wav.getframerate(), wav.getnchannels()) == (8000, 1)


def test_replays_use_the_recorded_flight_settings():
    recording = Recording(FIXTURES / "voice_kpae")
    cfg = with_recorded(Config(), recording.header.config)
    assert (cfg.flight.destination, cfg.flight.callsign, cfg.atc.seed) == ("KBFI", "", 7)
