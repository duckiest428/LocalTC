"""Orlando to Indianapolis (27 September 2026, an A321neo as Frontier 4837, on LocalTC 0.3.5), with the notes the
pilot marked on the way and what came of them:

- The language model timed out on nearly every question (the sim had the machine), and ATC said "stand by" and
  then nothing: the departure runway asked for three times, a taxi request, a tail-left request. Questions the
  grammar can place are now answered from the sim's data without the model, a call is never left unanswered,
  and a slow model shows as the controller thinking in the app instead of a "stand by" on the radio.
- "Tail right" from a gate whose taxi route leaves to the right (the tail goes left for that).
- A tug taking up the slack read as taxiing off without a clearance; two position samples a fraction of a
  millisecond apart read as a teleport (at Indianapolis, back to "taxi out", talking to Orlando Ground).
- Parked 737s called as "stopped on the taxiway ahead"; chatter from made-up callsigns.
- "Read back runway 36R" to "line up and wait"; the ride asked after; the copilot's handoffs all at once.
- Vectors off a STAR that runs straight down the ILS 32 final; "vectors ILS 32" with every heading; after
  the go-around, no handoff to tower. And the takeoff was "fly runway heading" on an RNAV SID.
"""

import re
from pathlib import Path

import msgspec
import pytest

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.llm import LlmInterpreter
from localtc.atc_core.llm.backend import LlmReply
from localtc.atc_core.llm.triggers import trigger
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback import (
    GrammarInterpreter,
    InterpretContext,
    PendingReadback,
)
from localtc.atc_core.readback.questions import question_topic
from localtc.atc_core.values import Callsign
from localtc.config import load_config, with_recorded
from localtc.llm.ollama import Placement
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AtcAlert,
    AtcThinking,
    AtcTransmission,
    OwnshipState,
    PhaseChanged,
    RadioChatter,
    TrafficSnapshot,
    TrafficTarget,
    Transcript,
)

FLIGHT = Path(__file__).parent / "fixtures" / "real_kmco_kind"
LIBRARY = TemplateLibrary.load()
FFT = Callsign.named("FFT4837")


def scenario(**meta) -> Scenario:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    return Scenario(scenario=ScenarioMeta(recording=str(FLIGHT), **meta), flight=cfg.flight, atc=cfg.atc)


@pytest.fixture(scope="module")
def recorded():
    """The flight as flown: the pilot's calls, and the copilot's from the climb on, judged by today's ATC."""
    return run(scenario(), FLIGHT, recording=FLIGHT, recorded_pilot=True, recorded_copilot=True)


@pytest.fixture(scope="module")
def copilot():
    """The copilot on the radio all the way, going wherever ATC sends it."""
    return run(scenario(copilot="full"), FLIGHT, recording=FLIGHT)


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def answer_to(lines: list[str], words: str) -> str:
    start = next(i for i, line in enumerate(lines) if "PILOT" in line and words in line)
    return next(line for line in lines[start + 1:] if " ATC " in line or "PILOT" in line)


def pending(instruction: str, **slots) -> PendingReadback:
    r = LIBRARY.render(instruction, {**slots, "callsign": FFT})
    return PendingReadback(instruction, instruction.split(".")[0], r.expected, r.required, r.optional)


def heard(text: str, waiting: PendingReadback | None = None, phase: str = "PARKED"):
    return GrammarInterpreter().interpret(text, waiting, InterpretContext(callsign=FFT, phase=phase))


# --- questions: the grammar's to answer, and never left unanswered -----------------------------------------------

@pytest.mark.parametrize(("text", "topic"), [
    ("And Orlando Ground, Frontier 4837. Can we get an updated altimeter?", "altimeter"),
    ("And Orlando Ground Frontier 4837. Any idea what our expected departure runway will be? Thank you very much.",
     "runway"),
    ("Frontier 4837, any idea what our expected departure runway will be? Thank you very much.", "runway"),
    ("Frontier 4837 Request, updated alt-terminator.", "altimeter"),  # "altimeter", as speech-to-text heard it
    ("Frontier 4837. Request updated altimeter.", "altimeter"),
])
def test_questions_the_grammar_answers_without_the_model(text, topic):
    result = heard(text)
    assert (result.intent, result.values) == ("question", {"topic": topic})
    assert trigger(result, text) is None  # no model, no wait


def test_the_departure_runway_is_answered_in_so_many_words(recorded):
    for asked in ("Any idea what our expected", "any idea what our expected", "do you have any idea"):
        assert "expect runway 36R for departure" in answer_to(recorded.lines, asked)


def test_a_question_with_a_report_gets_both(recorded):
    """ "Short final for runway 32. And can we also get an updated altimeter?" """
    assert question_topic("Frontier 4837. Short final for runway 32. And can we also get an updated altimeter?") == "altimeter"
    reply = answer_to(recorded.lines, "Short final for runway 32")
    assert "runway 32" in reply and "altimeter 30.01" in reply


class AlwaysLate:
    """A model the sim is keeping too busy to answer anything in time."""

    model = "late"

    def complete(self, request, *, timeout_s: float) -> LlmReply:
        return LlmReply(None, timeout_s * 1000, "timeout")


def parked_at_orlando(**kwargs) -> tuple[AtcEngine, OwnshipState]:
    engine = AtcEngine(EngineConfig(destination="KIND", cruise_ft=36000, callsign="FFT4837", seed=4), **kwargs)
    own = None
    for event in Recording(FLIGHT).events():
        if event.t > 400:
            break
        if isinstance(event, SIM_EVENT_TYPES):
            engine.handle(event)
            if isinstance(event, OwnshipState):
                own = event
    return engine, own


@pytest.mark.parametrize("text", [
    "Frontier 4837, what's the story with the delays today?",  # nobody could place it: "say again", not silence
    "And Orlando Ground Frontier 4837. Any idea what our expected departure runway will be? Thank you very much.",
])
def test_with_the_model_always_late_nothing_goes_unanswered(text):
    engine, own = parked_at_orlando(interpreter=LlmInterpreter(AlwaysLate(), mode="fallback", timeout_s=4.0))
    ground = engine.facility("ground")
    engine.handle(msgspec.structs.replace(own, t=own.t + 1, com1_mhz=ground.mhz))
    out = engine.handle(Transcript(t=own.t + 2, text=text, source="voice"))
    if engine.deferred is not None:
        assert [o.busy for o in out if isinstance(o, AtcThinking)] == [True]  # shown, not said
        out += engine.resolve_deferred()
    out += engine.handle(msgspec.structs.replace(own, t=own.t + 12, com1_mhz=ground.mhz))
    said = [o for o in out if isinstance(o, AtcTransmission)]
    assert said and all(o.instruction_id != "common.stand_by" for o in said), said


def test_a_pleasantry_gets_a_word_back(recorded):
    assert "thank" in answer_to(recorded.lines, "how are you doing today").lower()


# --- the pushback --------------------------------------------------------------------------------------------------

def test_the_tail_goes_the_other_way_from_the_taxi_route(recorded):
    """Gate 57 faces 199; the route leaves to the west (the right): tail left, and the nose comes round to it."""
    assert "tail left" in answer_to(recorded.lines, "early pushback here from Gate 57")


@pytest.mark.parametrize("text", [
    ("Sorry, Orlando Ground, can we tail left? Since a tailing right does not make any sense from our gate. "
     "Frontier 4837."),
    "Pushback and start-up at discretion to... Sorry, can we get it to left? Frontier 4837.",
])
def test_asking_for_the_tail_the_other_way(text):
    result = heard(text)
    assert (result.intent, result.values.get("tail")) == ("request_pushback", "left")


def test_the_tug_taking_up_the_slack_is_not_taxiing(recorded):
    phases = [line for line in recorded.lines if "PHASE" in line and 1000 < at(line) < 1200]
    assert phases and "PUSHBACK" in phases[0] and not any("TAXI_OUT" in p for p in phases)
    assert not [line for line in recorded.lines if "taxi_without_clearance" in line]


def test_can_we_get_taxi():
    assert heard("Roger, Frontier 4837. Can we get taxi?").intent == "ready_to_taxi"


# --- no position jumps out of nothing ------------------------------------------------------------------------------

def test_samples_a_moment_apart_are_not_a_teleport(recorded):
    assert not [line for line in recorded.lines if "position jump" in line]
    at_indy = [line for line in recorded.lines if at(line) > 9900]
    assert not [line for line in at_indy if "Orlando" in line or "out_of_range" in line]


# --- parked is parked ---------------------------------------------------------------------------------------------

def test_parked_aircraft_are_not_stopped_on_the_taxiway(recorded):
    assert not [line for line in atc(recorded.lines) if "stopped" in line]


def test_what_counts_as_parked():
    engine = AtcEngine(EngineConfig(destination="KIND"))

    def target(oid: int, atc_id: str = "", flight: str = "", gs: float = 0.0) -> TrafficTarget:
        return TrafficTarget(object_id=oid, atc_id=atc_id, airline="Southwest", flight_number=flight, atc_model="737",
                             lat=39.7, lon=-86.29, alt_ft=780, hdg_true=0, gs_kt=gs, on_ground=True)

    scenery = [target(1, "ASXGSA"), target(2, "N8655D"), target(3)]
    ai = [target(4, "SWA1740"), target(5, "", "2064"), target(6, "N123AB")]
    engine.handle(TrafficSnapshot(t=1.0, targets=(*scenery, *ai[:2], msgspec.structs.replace(ai[2], gs_kt=12.0))))
    engine.handle(TrafficSnapshot(t=4.0, targets=(*scenery, *ai)))
    assert [engine._parked(t) for t in scenery] == [True, True, True]
    assert [engine._parked(t) for t in ai] == [False, False, False]  # a flight's callsign, or seen moving


# --- the chatter is the sim's own traffic --------------------------------------------------------------------------

def test_chatter_is_the_aircraft_in_the_sim():
    result = run(scenario(), FLIGHT, recording=FLIGHT, recorded_pilot=True, recorded_copilot=True, chatter=True,
                 speech_s_per_char=0.06)
    heard_from = {o.callsign for o in result.outputs if isinstance(o, RadioChatter)}

    def said(cs: Callsign) -> str:
        return f"{cs.telephony} {cs.flight_number}" if cs.is_airline else cs.ident

    in_the_sim = set()
    for event in Recording(FLIGHT).events():
        if isinstance(event, TrafficSnapshot):
            for t in event.targets:
                in_the_sim |= {said(Callsign.named(t.atc_id)), said(Callsign.from_sim(t.atc_id, t.airline, t.flight_number))}
    assert len(heard_from) > 10
    assert heard_from <= in_the_sim, heard_from - in_the_sim  # "Breeze 1909" is the sim's MXY1909, and so on
    assert not heard_from & {"Jazz 43", "Air Transat 393", "Flair 1082"}  # made up, on the flight as flown


# --- readbacks: less pesky, and heard through speech-to-text -------------------------------------------------------

def test_line_up_and_wait_needs_no_runway():
    assert heard("Line up and wait for Frontier 4837.", pending("tower.luaw", runway="36R"), "RUNWAY_HOLD").status == "correct"


@pytest.mark.parametrize(("text", "instruction", "slots"), [
    ("Great for takeoff on way 36 right. Fly runway heading from to 4837.", "tower.takeoff_rnav",
     {"runway": "36R", "fix": "FACTS"}),
    ("Cut to land runway 32 from to your 4837.", "tower.land", {"runway": "32", "wind": None}),
    ("Clear direct BTLR Frontier 4837.", "common.direct", {"fix": "BTTLR"}),
])
def test_what_speech_to_text_makes_of_it(text, instruction, slots):
    if slots.get("wind", 1) is None:
        from localtc.atc_core.values import Wind

        slots = {**slots, "wind": Wind(10, 11)}
    assert heard(text, pending(instruction, **slots), "APPROACH").status == "correct"


def test_ils_32r_is_the_ils_32_where_there_is_only_a_32(recorded):
    readback = next(line for line in recorded.lines if "READBACK" in line and at(line) > 9690)
    assert "approach.intercept_cleared correct" in readback


# --- the departure --------------------------------------------------------------------------------------------------

def test_an_rnav_sid_is_rnav_to_its_first_fix(recorded):
    takeoff = next(line for line in atc(recorded.lines) if "cleared for takeoff" in line)
    assert "RNAV to FACTS, runway 36R, cleared for takeoff" in takeoff and "heading" not in takeoff


# --- the arrival: fly the STAR, no vectors off it; the go-around handed to tower again -----------------------------

def test_a_star_down_the_final_is_flown_and_the_approach_cleared_on_it(copilot):
    arrival = [line for line in atc(copilot.lines) if 7300 < at(line) < 8850]
    assert not [line for line in arrival if "heading" in line], arrival  # no vectors on the GIIBS4
    cleared = [line for line in arrival if "cleared ILS RWY 32 approach" in line]
    assert len(cleared) == 1 and "Indianapolis Approach" in cleared[0]
    assert any("contact Indy Tower" in line for line in arrival if at(line) > at(cleared[0]))


def test_after_the_go_around_approach_sends_it_to_tower_again(copilot, recorded):
    for result in (copilot, recorded):
        again = [line for line in atc(result.lines) if at(line) > 8900]
        assert any("contact Indy Tower" in line for line in again), again
        assert not [line for line in atc(result.lines) if "Orlando" in line and at(line) > 8000]


def test_vectors_for_the_ils_is_said_once(copilot):
    headings = [line for line in atc(copilot.lines) if at(line) > 8850 and "heading" in line]
    assert headings
    assert sum("ILS RWY 32 approach" in line for line in headings if "cleared" not in line) <= 1


def test_speeds_only_come_down(copilot):
    speeds = [int(m.group(1)) for line in atc(copilot.lines) if (m := re.search(r"(\d+) knots", line))]
    assert speeds and speeds == sorted(speeds, reverse=True)


# --- the copilot at a human pace --------------------------------------------------------------------------------

def test_the_copilot_changes_frequency_and_checks_in_with_time_between(copilot):
    lines = copilot.lines
    for i, line in enumerate(lines):
        if "TUNE" not in line or at(line) < 1800:
            continue
        readback = next(x for x in reversed(lines[:i]) if "PILOT" in x)
        checkin = next((x for x in lines[i + 1:] if "PILOT" in x), None)
        assert at(line) - at(readback) >= 3.0, (readback, line)
        if checkin is not None and "," in checkin.split(":", 2)[-1]:
            assert at(checkin) - at(line) >= 5.0, (line, checkin)


# --- the model on the CPU -----------------------------------------------------------------------------------------

def test_a_sliver_of_video_memory_is_still_the_cpu():
    """CPU only, Ollama reported 0.1 GB of the 2.6 GB model on the graphics card; LocalTC took that for the card,
    unloaded it and loaded it again at every session start."""
    assert not Placement(size=2_600_000_000, size_vram=100_000_000).gpu
    assert "on the CPU" in Placement(size=2_600_000_000, size_vram=100_000_000).describe()
    assert Placement(size=2_600_000_000, size_vram=2_600_000_000).gpu


def test_the_model_is_on_the_cpu_by_default():
    from localtc.config import LlmConfig

    assert LlmConfig().cpu_only


def test_no_alerts_but_the_ones_that_happened(recorded):
    alerts = {o.kind for o in recorded.outputs if isinstance(o, AtcAlert)}
    # (Not "landed without a landing clearance": as flown, nobody sent the pilot to tower the second time.)
    assert not alerts & {"taxi_without_clearance", "out_of_range", "no_atc_on_frequency"}
    assert not [o for o in recorded.outputs if isinstance(o, PhaseChanged) and o.reason == "position jump"]
