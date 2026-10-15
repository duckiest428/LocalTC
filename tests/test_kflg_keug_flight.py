"""Regressions from Flagstaff to Eugene (2026-10-15, a jet as NBV, the copilot reading back, LocalTC's traffic on),
with the pilot's notes: the centre never turned the flight on course after "fly runway heading"; "check altitude"
straight after a descent at pilot's discretion; one centre from Flagstaff to Oregon; approach taking over 30 miles out
at 11,000 ft and 300 kt; "number two, follow the 777 on a 1 mile final" for a parked aircraft, and the landing clearance
at 100 ft; and Eugene's tower (no ground controller) sending the flight to Flagstaff Ground.
"""

from pathlib import Path

import pytest

from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_kflg_keug"


@pytest.fixture(scope="module")
def flight() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True, copilot="assist").lines


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def atc(lines: list[str], start: float = 0.0, end: float = 1e9) -> list[str]:
    return [line for line in lines if " ATC " in line and start <= at(line) <= end]


def test_the_centre_taking_the_flight_from_tower_turns_it_on_course(flight):
    centre = atc(flight, 800, 900)
    assert any("Albuquerque Center" in line and "direct OATES" in line for line in centre)


def test_no_check_altitude_for_a_descent_at_pilots_discretion(flight):
    assert not [line for line in flight if "check altitude" in line]
    assert not [line for line in flight if "altitude_deviation" in line]


def test_the_centres_hand_the_flight_on_along_the_way(flight):
    handoffs = " ".join(line for line in atc(flight) if "Center" in line.split(":")[0] and "contact" in line)
    for centre in ("Los Angeles Center", "Salt Lake Center", "Oakland Center", "Seattle Center"):
        assert f"contact {centre}" in handoffs


def test_approach_takes_the_arrival_with_room_to_get_down(flight):
    handoff = next(line for line in atc(flight) if "contact Cascade Approach" in line)
    assert at(handoff) < 7150  # about 50 miles out, descending through 17,000 ft (not 30 miles out at 11,000)


def test_a_parked_aircraft_is_nobody_to_follow_and_the_landing_clearance_comes_on_final(flight):
    tower = atc(flight, 7680, 7900)
    assert not [line for line in tower if "follow the" in line or "traffic 2 miles ahead" in line]
    land = next(line for line in tower if "cleared to land" in line)
    assert at(land) < 7760  # by about 5 miles out; it came at 100 ft


def test_tower_gives_the_taxi_in_where_there_is_no_ground(flight):
    after = atc(flight, 7905, 8100)
    assert not [line for line in after if "Flagstaff" in line]
    assert any("Eugene Tower" in line and "taxi" in line for line in after)
