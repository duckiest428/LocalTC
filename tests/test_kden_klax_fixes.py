"""Regressions from a Denver -> Los Angeles flight sent in from the app (A321, Whisper on the CPU after the graphics
card failed, the copilot on the radios): the departure (tests/fixtures/real_kden_klax_departure, the recording to the
cruise, replayed with the pilot's own calls) and what went wrong around it: the restart that hung at "Loading Whisper",
ATC's voice on another device after the device list was read again, every device listed four times."""

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from localtc.atc_core.airport import gates as stands
from localtc.atc_core.llm.phrase import PhraseError, check_reply
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_kden_klax_departure"


def _replay(copilot: str | None):
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True, copilot=copilot)


@pytest.fixture(scope="module")
def alone():
    """The pilot's own calls only: the handoffs not read back."""
    return _replay(None)


@pytest.fixture(scope="module")
def with_copilot():
    """With the copilot reading back and tuning, as in the flight."""
    return _replay("assist")


def at(lines: list[str], start: float, end: float) -> list[str]:
    out = []
    for line in lines:
        try:
            t = float(line.split("]")[0].strip("[ "))
        except ValueError:
            continue
        if start <= t <= end:
            out.append(line)
    return out


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


# --- the departure -------------------------------------------------------------------------------------------------


def test_the_runway_change_on_the_taxi_brings_the_departure_for_the_new_runway(with_copilot):
    """Cleared on ROCKI7 for 26 a few seconds before Denver's real ATIS came in, then sent to 25 at the taxi: the
    filed BAYLR6 (25's) is said with the change, not left for the pilot to find out."""
    taxi = next(line for line in atc(with_copilot.lines) if "taxi via" in line)
    assert "runway change, runway 26 no longer in use, BAYLR6 departure" in taxi


def test_a_check_in_said_a_moment_before_the_switch_is_answered_by_departure(alone):
    """"Denver Departure, with you climbing 9,000" heard on the tower's frequency, the pilot on departure's a moment
    later: departure answers it (radar contact and the climb), not fourteen minutes of nothing under the mountains."""
    after = atc(at(alone.lines, 1995.0, 2010.0))
    assert any("Denver Departure 126.1" in line and "radar contact" in line and "17,000" in line for line in after)


def test_a_request_for_more_altitude_gets_a_climb_not_unable_terrain(with_copilot):
    """"You told me to descend to 9,000 ... request altitude increase": the 9,000 is the complaint, not the request."""
    answer = atc(at(with_copilot.lines, 2264.0, 2275.0))
    assert answer and "climb and maintain 17,000" in answer[0] and "unable" not in answer[0]


def test_request_flight_level_290_is_a_request_not_a_check_in(with_copilot):
    answer = atc(at(with_copilot.lines, 2541.0, 2547.0))
    assert answer and "climb and maintain FL290" in answer[0]
    ctx = InterpretContext(callsign=Callsign("NKS283"), phase="CRUISE", station_role="center")
    for said in ("Spirit 283, request flight level 290.", "Spirit 283 request FL290", "request one seven thousand"):
        heard = GrammarInterpreter().interpret(said, None, ctx)
        assert heard.intent == "request_altitude" and not heard.needs_fallback, said
    assert GrammarInterpreter().interpret("Denver Center, Spirit 283, flight level 360. With you.", None,
                                          ctx).intent == "checkin"


def test_localtcs_arrivals_land_on_the_arrival_atis_runways(with_copilot):
    """Denver's departure ATIS (25, 8, 34L) landed LocalTC's arrivals on 25 every two minutes, and the pilot was held
    short of it for a quarter of an hour; the arrival ATIS says 34R, 35L and 35R."""
    engine = with_copilot.engine
    landing = {engine.runway_for("KDEN", landing=True, key=f"A{i}", lat=39.9 + i * 0.01, lon=-104.6) for i in range(6)}
    assert landing and landing <= {"34R", "35L", "35R"}
    assert engine.runway_for("KDEN", landing=False, key="D1") in {"25", "08", "34L"}


def test_a_departure_held_a_while_has_localtcs_arrivals_leave_it_a_gap(with_copilot):
    engine = with_copilot.engine
    engine.state.flight.origin = "KDEN"
    engine.state.clearances.pop("takeoff", None)
    engine._takeoff_wait, engine._takeoff_hold = "25", "arrival"
    engine._held_since = ("25", engine._t - 60)
    assert engine.departure_waiting("KDEN") is None  # a minute: still waiting its turn
    engine._held_since = ("25", engine._t - 200)
    assert engine.departure_waiting("KDEN") == "25"
    engine._takeoff_wait = None
    assert engine.departure_waiting("KDEN") is None


# --- the language model's runways ------------------------------------------------------------------------------------


def test_a_runway_side_the_airport_hasnt_is_never_said():
    """"Am I at the correct runway?" -- "runway 25L": Denver has one 25. Turned away even when the model may answer
    beyond the sim's data."""
    runways = {"runways here": "07/25, 08/26, 16L/34R, 16R/34L, 17L/35R, 17R/35L", "departure runway": "25"}
    with pytest.raises(PhraseError, match="25L"):
        check_reply('{"reply":"Spirit 283, runway 25L"}', runways, ("Spirit 283",), beyond_facts=True)
    assert check_reply('{"reply":"Spirit 283, runway 25"}', runways, ("Spirit 283",), beyond_facts=True)
    assert check_reply('{"reply":"runway 34R is for arrivals"}', runways, ("Spirit 283",), beyond_facts=True)


# --- the gate asked for ----------------------------------------------------------------------------------------------


def test_a_gate_with_its_letter_after_the_number_is_read_whole():
    """"Request taxi to gate 46B" was taken for gate 46, and Los Angeles gave Gate 14 without a word."""
    assert stands.requested(normalize("Los Angeles Ground Spirit 283 is vacated runway 25R. Request taxi to gate 46B.")) == "46B"
    assert stands.requested(normalize("request taxi to gate 46 bravo")) == "46B"
    assert stands.requested(normalize("gate 46")) == "46"
    assert stands.requested(normalize("taxi to gate Echo 9")) == "E9"


# --- the copilot --------------------------------------------------------------------------------------------------


def test_level_at_is_where_it_levelled_off():
    """"Level at FL360. Love this view." at FL290, cleared to FL360 and not climbing yet."""
    from localtc.sim_api import CrewSpeech, PhaseChanged
    from test_crew_monitor import crew, fly, own

    pm = crew()
    level = dict(on_ground=False, alt_indicated_ft=29000, alt_msl_ft=29000, alt_agl_ft=28000, ias_kt=270, gs_kt=450,
                 flaps_index=0, gear_down=False)
    out = fly(pm, [own(1 + i, **level) for i in range(5)] + [PhaseChanged(t=6.5, previous="DEPARTURE", phase="CRUISE")]
              + [own(7 + i, **level) for i in range(20)])
    words = [o.text for o in out if isinstance(o, CrewSpeech)]
    assert any("FL290" in w for w in words) and not any("FL350" in w for w in words), words


# --- Whisper and the restart ------------------------------------------------------------------------------------------


def _fake_whisper(monkeypatch, tmp_path, fails_on_card: bool = True):
    import sys
    import types

    from localtc.stt import whisper

    made = []

    class Segment:
        text, start, end, avg_logprob, no_speech_prob = "ready", 0.0, 1.0, -0.1, 0.01

    class Model:
        def __init__(self, path, device, compute_type):
            self.device, self.hf_tokenizer = device, None
            made.append(device)

        def transcribe(self, audio, **kw):
            if self.device == "cuda" and fails_on_card:
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return iter([Segment()]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(whisper, "add_nvidia_dll_dirs", lambda: ["x"])
    (tmp_path / "faster-whisper-small.en").mkdir(exist_ok=True)
    (tmp_path / "faster-whisper-small.en" / "model.bin").write_bytes(b"")
    return whisper, made


def test_the_graphics_card_failing_once_is_remembered_for_the_run(monkeypatch, tmp_path):
    """A flight restarted to apply a setting tried the card again and hung at "Loading Whisper": once it has failed,
    the next flight in the same run goes straight to the CPU."""
    whisper, made = _fake_whisper(monkeypatch, tmp_path)
    first = whisper.WhisperTranscriber("small.en", device="cuda", compute_type="float16", models_dir=tmp_path)
    first.warm_up()
    assert first.choice.device == "cpu" and whisper.card_failed()
    again = whisper.WhisperTranscriber("small.en", device="cuda", compute_type="float16", models_dir=tmp_path)
    assert again.choice.device == "cpu"
    again.warm_up()
    assert made == ["cuda", "cpu"]  # and the CPU model already loaded is the one used again


def test_a_flight_restarted_uses_the_whisper_already_loaded(monkeypatch, tmp_path):
    whisper, made = _fake_whisper(monkeypatch, tmp_path, fails_on_card=False)
    for _ in range(3):
        whisper.WhisperTranscriber("small.en", device="cuda", compute_type="float16", models_dir=tmp_path).warm_up()
    assert made == ["cuda"]


def test_a_warm_up_that_never_answers_on_the_card_moves_to_the_cpu(monkeypatch):
    from localtc import app

    stuck = threading.Event()

    class Transcriber:
        def __init__(self) -> None:
            self.choice = SimpleNamespace(device="cuda")
            self.moved = ""

        def warm_up(self) -> float:
            if self.choice.device == "cuda":
                stuck.wait(5)  # the card's first words never come
            return 0.1

        def to_cpu(self, why: str) -> None:
            self.moved, self.choice = why, SimpleNamespace(device="cpu")

    t = Transcriber()
    started = time.monotonic()
    seconds = asyncio.run(app.warm_whisper(t, wait_s=0.2))
    stuck.set()
    assert seconds == 0.1 and t.choice.device == "cpu" and "0 s" in t.moved and time.monotonic() - started < 3


def test_loading_the_model_isnt_timed_only_the_cards_first_words():
    """A model downloading (or a slow card loading it) took longer than the card's limit, and Whisper was put on the
    CPU at every start: "it says Whisper isn't on the graphics card, and it settles on the CPU"."""
    from localtc import app

    class Transcriber:
        def __init__(self) -> None:
            self.choice = SimpleNamespace(device="cuda")
            self.moved = ""

        def load(self) -> None:
            time.sleep(0.5)  # longer than the limit below

        def warm_up(self) -> float:
            return 0.1

        def to_cpu(self, why: str) -> None:
            self.moved, self.choice = why, SimpleNamespace(device="cpu")

    t = Transcriber()
    seconds = asyncio.run(app.warm_whisper(t, wait_s=0.2))
    assert t.choice.device == "cuda" and not t.moved and seconds >= 0.5


# --- the audio devices ---------------------------------------------------------------------------------------------


def test_the_voice_finds_its_output_again_after_the_device_list_is_read_again(monkeypatch):
    """Reading the device list again (the microphone reopened) numbers the devices afresh: the number kept from the
    start played ATC on another device until the app was restarted."""
    from localtc.dsp.portaudio import GATE
    from localtc.tts import player as P

    numbers = {"VAC 2 (Virtual Audio Cable)": 7}
    monkeypatch.setattr(P, "find_output", lambda spec: numbers[spec] if spec else None)
    voice = P.AudioPlayer("VAC 2 (Virtual Audio Cable)")
    assert voice.device == 7
    numbers["VAC 2 (Virtual Audio Cable)"] = 9  # another device plugged in, the list read again
    assert voice.device == 7  # not looked for again until the list is read again
    monkeypatch.setattr(GATE, "generation", GATE.generation + 1)
    assert voice.device == 9
    monkeypatch.setattr(P, "find_output", lambda spec: (_ for _ in ()).throw(ValueError("gone")) if spec else None)
    monkeypatch.setattr(GATE, "generation", GATE.generation + 1)
    assert voice.device is None  # unplugged: the system's default until it's back
    voice.set_output("")
    assert voice.device is None and voice.spec is None


def test_each_device_is_listed_once():
    from localtc.ui.controller import once_each

    devices = [(0, "Microsoft Sound Mapper - Output", 44100), (1, "VAC 2 (Virtual Audio Cable)", 44100),
               (2, "Analogue 1 + 2 (3- Focusrite US", 48000), (5, "VAC 2 (Virtual Audio Cable)", 44100),
               (6, "Analogue 1 + 2 (3- Focusrite USB Audio)", 48000), (9, "Output (VAC 2)", 44100)]
    listed = {0: {"hostapi": 0}, 1: {"hostapi": 0}, 2: {"hostapi": 0}, 5: {"hostapi": 1}, 6: {"hostapi": 2},
              9: {"hostapi": 3}}
    shown = once_each(devices, listed, 0)
    assert [i for i, _, _ in shown] == [0, 1, 2]
    assert shown[2] == (2, "Analogue 1 + 2 (3- Focusrite US", "Analogue 1 + 2 (3- Focusrite USB Audio)")


def test_only_settings_a_flight_cant_take_up_ask_for_a_restart():
    from localtc.ui.controller import applied_live

    assert applied_live({"tts": {"output_device": "VAC 2"}, "voice": {"input_device": "Focusrite"}})
    assert applied_live({"mixer": {"atc": 0.4}, "tts": {"volume": 0.3}})
    assert not applied_live({"voice": {"model": "small.en"}})
    assert not applied_live({"tts": {"voice": "en_US-amy"}})
    assert not applied_live({"atc": {"style": "icao"}})
