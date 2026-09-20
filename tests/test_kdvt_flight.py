"""Regressions from the second KDVT -> KGEU flight (2026-09-19): taxi hold-short points, a departure request
worded freely, a turn on departure, and calls made on a frequency the pilot has already been handed off from."""

from pathlib import Path

import pytest

from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_kdvt_kgeu_2"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def after(lines: list[str], text: str, count: int = 1) -> list[str]:
    start = next(i for i, line in enumerate(lines) if text in line)
    return [line for line in lines[start + 1 :] if " ATC " in line][:count]


def test_the_taxi_clearance_names_the_hold_short_point(replay):
    assert "runway 25L at D, taxi via C, D" in after(replay, "Request Taxi to Runway")[0]


def test_asking_for_the_departure_at_the_hold_short_line(replay):
    assert "cleared for takeoff" in after(replay, "We'd like to get the departure")[0]


@pytest.mark.parametrize(("text", "intent"), [
    ("Tower, DP32, holding short runway 25L, we'd like to get the departure", "ready_for_departure"),
    ("DP32 request the departure runway 25L", "ready_for_departure"),
    ("DP32, we'd like a left turn right after departure", "request_turn"),
    ("DP32 on the approach for runway 19", "report_final"),
])
def test_calls_this_flight_made(text, intent):
    heard = GrammarInterpreter().interpret(text, None, InterpretContext(phase="RUNWAY_HOLD"))
    assert heard.intent == intent


def test_a_turn_on_departure_is_approved():
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation

    engine = AtcEngine(EngineConfig(callsign="N172LT", seed=3))
    engine.state.phase = "RUNWAY_HOLD"
    tower = Facility(controller="tower", station="Paine Tower", mhz=120.2, airport="KPAE")
    engine._turn_request(Interpretation(kind="request", intent="request_turn", values={"turn": "left"}), tower, 10.0, None)
    assert engine._scheduled and engine._scheduled[0].instruction_id in ("tower.turn_approved", "tower.turn_unable")


def test_a_call_on_the_old_frequency_gets_the_new_one_again(replay):
    """The pilot kept talking to Luke Approach after reading back "contact Glendale Tower"."""
    answers = [line for line in replay if " ATC " in line and "contact Glendale Tower" in line]
    assert len(answers) >= 2  # the handoff, then the reminder when the pilot called approach again
    assert "say again" not in after(replay, "DP32 on the approach runway 19")[0]
