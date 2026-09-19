"""Regressions from the KDVT -> KGEU flight (2026-09-19, the first one flown in the app): a C172 with a failed
airspeed indicator, cleared for the RNAV 01 but north of the field on 19's centerline when tower cleared it."""

from pathlib import Path

import pytest

from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
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
    assert "taxi via C" in after(replay, "DP32 Request IFR Taxi to Active Runway.")[0]


def test_disregarded_direct_is_not_a_request():
    heard = GrammarInterpreter().interpret("Direct. Sorry, disregard. Continuing as filed.", None, InterpretContext(phase="CRUISE"))
    assert heard.intent != "request_direct"


def test_trucks_are_rolled_and_the_landing_runway_is_the_cleared_approach(replay):
    answers = after(replay, "some trucks on the ground", 2)
    assert "equipment standing by" in answers[0]
    assert "runway 01, cleared to land" in answers[1]  # not 19: the aircraft was maneuvering 8 nm north of the field


def test_an_instrument_failure_is_acknowledged_not_say_again(replay):
    answer = after(replay, "instrument failure on our speed")[0]
    assert "assistance" in answer and "say again" not in answer


def test_tower_on_121_reads_back_121_0(replay):
    assert "approach.cleared correct" in next(line for line in replay if "READBACK" in line and "approach.cleared" in line)


def test_asking_tower_for_another_runway_on_final(replay):
    assert "runway 01, cleared to land" in after(replay, "request. Arnav. Runway 01.")[0]
