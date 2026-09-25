"""Regressions from the KDVT -> KGEU flight (2026-09-19, the first one flown in the app): a C172 with a failed
airspeed indicator, cleared for the RNAV 01 but north of the field on 19's centerline when tower cleared it."""

from pathlib import Path

import pytest

from localtc.atc_core.readback import (
    GrammarInterpreter,
    InterpretContext,
    PendingReadback,
)
from localtc.atc_core.values import Approach
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_kdvt_kgeu"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def after(lines: list[str], text: str, count: int = 1) -> list[str]:
    start = next(i for i, line in enumerate(lines) if text in line)
    return [line for line in lines[start + 1 :] if " ATC " in line][:count]


@pytest.mark.parametrize("text", ["DP32 Request IFR Taxi to Active Runway.", "Deer Valley Clearance DP32. Request taxi to active runway."])
def test_asking_for_taxi_is_not_an_ifr_request(text):
    heard = GrammarInterpreter().interpret(text, None, InterpretContext(phase="PARKED"))
    assert heard.intent == "ready_to_taxi"


def test_ground_gives_the_taxi_clearance(replay):
    assert "via C" in after(replay, "DP32 Request IFR Taxi to Active Runway.")[0]


def test_disregarded_direct_is_not_a_request():
    heard = GrammarInterpreter().interpret("Direct. Sorry, disregard. Continuing as filed.", None, InterpretContext(phase="CRUISE"))
    assert heard.intent != "request_direct"


def test_trucks_are_rolled_and_the_landing_runway_is_the_cleared_approach(replay):
    answers = after(replay, "some trucks on the ground", 2)
    assert "equipment" in answers[0] and "standing by" in answers[0]
    # Runway 01, not 19: the aircraft was maneuvering 8 nm north of the field. And "continue", not yet
    # cleared to land: that waits for a final with the runway seen empty.
    assert "continue" in answers[1] and "runway 01" in answers[1], answers[1]


def test_an_instrument_failure_is_acknowledged_not_say_again(replay):
    answer = after(replay, "instrument failure on our speed")[0]
    assert "assistance" in answer and "say again" not in answer


def test_tower_on_121_reads_back_121_0():
    """ "Glendale Tower on 121" is 121.0: the pilot drops the ".0" and the readback is still right."""
    pending = PendingReadback(instruction_id="approach.cleared", controller="approach",
                              expected={"approach": Approach("RNAV", "01"), "frequency": 121.0},
                              required=("frequency",), optional=("approach",))  # as approach.cleared has them
    heard = GrammarInterpreter().interpret("Glendale Tower on 121. Cleared for the RNAV on 01. DP32.", pending,
                                           InterpretContext(phase="APPROACH"))
    assert heard.status == "correct", (heard.status, heard.missing, heard.mismatched)


def test_asking_tower_for_another_runway_on_final(replay):
    answer = after(replay, "request. Arnav. Runway 01.")[0]
    assert "runway 01" in answer and "cleared to land" in answer
