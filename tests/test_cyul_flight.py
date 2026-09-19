"""Regressions from the CYHU -> CYUL voice flight (2026-09-19): a 12 nm hop at 3,500 ft, Whisper base.en,
llama3.2:3b. Replayed with the pilot's recorded transcripts and the grammar alone (see test_kphx_flight.py).
"""

from pathlib import Path

import pytest

from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_cyhu_cyul"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def after(lines: list[str], text: str, count: int = 1) -> list[str]:
    start = next(i for i, line in enumerate(lines) if text in line)
    return lines[start + 1 : start + 1 + count]


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def test_a_letters_then_digits_callsign_is_never_shortened(replay):
    assert Callsign("NZXT42").short.abbreviated is False
    assert Callsign("N172LT").short.abbreviated is True and Callsign("C-FABC").short.abbreviated is True
    assert not any("T42," in line and "NZXT42" not in line for line in atc(replay))


def test_request_ifr_with_to_heard_as_2():
    heard = GrammarInterpreter().interpret("Ground NZXT42 information Charlie on board. Request IFR 2 Montreal Trudeau.",
                                           None, InterpretContext(phase="PARKED"))
    assert heard.intent == "request_ifr_clearance"


def test_stand_by_from_the_pilot_needs_no_answer(replay):
    assert "say again" not in after(replay, "Stand by NZXT42.")[0]


def test_no_expect_clause_when_cruise_is_the_initial_altitude(replay):
    clearance = next(line for line in atc(replay) if "cleared to Montreal" in line)
    assert "climb and maintain 3,500, departure frequency 125.15" in clearance and "expect" not in clearance


def test_a_readback_with_digits_said_one_by_one(replay):
    assert "clearance.ifr_at_cruise correct" in after(replay, "Climb maintain a 3,500")[0]
    assert [str(t) for t in normalize("departure 1, 2, 5.15")] == ["departure", "<125.15>"]
    assert [str(t) for t in normalize("taxi via A, C, K")] == ["taxi", "via", "a", "c", "k"]  # taxiway A is not a filler


def test_confirming_the_taxi_clearance_gets_affirmative(replay):
    for asked in ("just to confirm. Text to 06 left.", "just to confirm. Taxi to 06L"):
        answer = after(replay, asked)[0]
        assert "ATC" in answer and ("affirm" in answer), answer


def test_ready_to_depart_gets_the_takeoff_clearance(replay):
    assert "cleared for takeoff" in after(replay, "ready to depart")[0]


def test_short_hop_arrival_stops_the_climb_instead_of_descending(replay):
    arrival = next(line for line in atc(replay) if "expect ILS" in line)
    assert "maintain 3,000" in arrival and "descend" not in arrival


def test_an_unanswered_readback_does_not_block_the_approach_handoff(replay):
    assert any("contact Montreal Approach 118.9" in line for line in atc(replay))
    handoffs = [line for line in atc(replay) if "contact Montreal Approach" in line]
    assert len(handoffs) <= 6  # said again for a silent pilot, but not for the rest of the flight
