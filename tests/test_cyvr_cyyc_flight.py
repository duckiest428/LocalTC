"""Regressions from the CYVR -> CYYC flight (2026-09-20), an A220 flown mostly by voice.

Speech-to-text is what this flight brought out: the words a controller says quietly come back from
Whisper mangled, and a readback that fails on those words is a readback the pilot can never get right.
It also covers the airline callsign, the pushback reply, and the handoffs that came too late.
"""

from pathlib import Path

import pytest

from localtc.atc_core.readback import GrammarInterpreter, InterpretContext, PendingReadback
from localtc.atc_core.readback.extract import candidates
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_cyvr_cyyc"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def after(lines: list[str], text: str, count: int = 1) -> list[str]:
    start = next(i for i, line in enumerate(lines) if text in line)
    return [line for line in lines[start + 1 :] if " ATC " in line][:count]


# --- what speech-to-text did to the readbacks ----------------------------------------------------


@pytest.mark.parametrize("said", [
    "Taxi runway 8R via INNER Lima, Holshore runway 13. Romeo is current. Air Canada 216.",
    "Taxi runway 8 right via I and an ER Lima. Hold short runway 13.",
])
def test_hold_short_however_it_comes_out_of_whisper(said):
    """ "Hold short" came back as holshore, holtoire and portrait, and each was a rejected readback."""
    pending = PendingReadback(instruction_id="ground.taxi_out_hold_short", controller="ground",
                              expected={"runway": "08R", "hold_short": "13"}, required=("runway", "hold_short"))
    heard = GrammarInterpreter().interpret(said, pending, InterpretContext(phase="TAXI_OUT"))
    assert heard.status == "correct", (heard.status, heard.missing, heard.mismatched)


def test_the_hold_short_alone_after_being_asked_for_it():
    """ATC asked "read back hold short runway 13" and got "Portrait runway 13R Canada 216"."""
    pending = PendingReadback(instruction_id="ground.taxi_out_hold_short", controller="ground",
                              expected={"runway": "08R", "hold_short": "13"}, required=("hold_short",), attempts=1)
    heard = GrammarInterpreter().interpret("Portrait runway 13R Canada 216", pending, InterpretContext(phase="TAXI_OUT"))
    assert heard.status == "correct", (heard.status, heard.missing, heard.mismatched)


def test_a_bare_flight_level_in_a_readback():
    """ "expect 350" and "climb maintain 350" are FL350; both were rejected as the wrong altitude."""
    assert candidates("cruise", normalize("expect 350, 10 minutes after departure"), 35000) == [35000]
    assert candidates("altitude", normalize("Climb maintain 350, Air Canada 216"), 35000) == [35000]
    # A number that is not the flight level ATC gave is still wrong.
    assert candidates("altitude", normalize("maintain 350"), 5000) == [350]


# --- calls that got "say again" ------------------------------------------------------------------


@pytest.mark.parametrize(("text", "intent"), [
    ("Air Canada 216 Radio Check.", "radio_check"),
    ("Tail left, Air Canada 216.", "acknowledge"),
    ("Air Canada 216 Tower, holding short NIMA for Departure on runway 8R.", "ready_for_departure"),
    ("Air Canada 216, holding short Lima for departure on runway 8R.", "ready_for_departure"),
    ("Ground Air Canada 216. Request pushback and startup.", "request_pushback"),
])
def test_calls_this_flight_made(text, intent):
    heard = GrammarInterpreter().interpret(text, None, InterpretContext(phase="TAXI_OUT"))
    assert heard.intent == intent


@pytest.mark.parametrize("text", [
    "Vancouver Ground, Air Canada 216, Request Altimeter Update.",
    "Air Canada 216. Request. Barrow.",
])
def test_asking_for_the_altimeter_is_a_question(text):
    from localtc.atc_core.llm.triggers import question_topic

    assert question_topic(text) == "altimeter"


def test_a_radio_check_is_answered(replay):
    assert "read you five by five" in after(replay, "Radio Check")[0].lower() \
        or "loud and clear" in after(replay, "Radio Check")[0].lower()


# --- the callsign --------------------------------------------------------------------------------


def test_an_airline_code_is_read_as_the_airline():
    """A plan filed as ACA216 was spelled out "alpha charlie alpha two one six"."""
    callsign = Callsign.named("ACA216")
    assert callsign.is_airline and callsign.telephony == "Air Canada" and callsign.flight_number == "216"


def test_a_registration_is_still_a_registration():
    assert not Callsign.named("N172LT").is_airline
    assert not Callsign.named("XYZ99").is_airline  # a code nobody knows keeps its letters


def test_the_flight_is_called_air_canada(replay):
    assert any("Air Canada 216" in line for line in replay), replay[:5]


# --- what ATC does and when ----------------------------------------------------------------------


def test_no_incursion_crossing_a_runway_the_route_goes_across(replay):
    """The taxi route to 08R crosses runway 13. Being routed over a runway and then blamed for
    crossing it is ATC's mistake, not the pilot's: the clearance to cross comes as they reach it."""
    assert not any("runway_incursion" in line for line in replay), \
        [line for line in replay if "ALERT" in line]
    assert any("cross runway 13" in line for line in replay), \
        [line for line in replay if "ATC" in line][:12]


def test_the_climb_is_handed_to_a_centre_before_the_top_of_it(replay):
    """To Vancouver Centre, whose airspace Vancouver is in, spelled the Canadian way."""
    handoff = next((line for line in replay if "Vancouver Centre" in line.split(":", 1)[-1] and "contact" in line), None)
    cruise = next((line for line in replay if "-> CRUISE" in line), None)
    assert handoff is not None and cruise is not None
    assert float(handoff.split("]")[0].strip("[ ")) < float(cruise.split("]")[0].strip("[ "))


def test_no_local_altimeter_while_above_the_transition_altitude(replay):
    descent = next(line for line in replay if "descend and maintain" in line and "expect" in line)
    assert "altimeter" not in descent, descent
