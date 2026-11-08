"""Budapest towards Luton (6 November 2026, an A321neo as HA-NGA, on LocalTC 0.5.5), flown by another pilot:

- "Budapest Departure, Airbus November Golf Alpha, good day" was read as an acknowledgement and went unanswered, so
  departure never counted the flight as checked in, and never handed it on: the pilot climbed to FL310 on it and
  tuned Budapest Control themselves. A first call on a radar frequency is the check-in, whatever's in it.
- Budapest Control kept the flight across Austria and into Germany, and then sent it back to Budapest Departure, 300
  miles away. A controller left behind sends the flight on to the one working it now.
"""

from pathlib import Path

import pytest

from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_lhbp_eggw"


@pytest.fixture(scope="module")
def recorded():
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    sc = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(sc, FLIGHT, recording=FLIGHT, recorded_pilot=True)


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def test_the_first_call_to_departure_is_the_check_in_and_departure_hands_the_flight_on(recorded):
    lines = atc(recorded.lines)
    answer = next(line for line in lines if at(line) > 2595)
    assert "Budapest Departure" in answer and at(answer) < 2602, answer
    assert any("Budapest Departure" in line and "contact" in line and "Radar" in line for line in lines if at(line) < 3660)


def test_a_centre_left_behind_sends_the_flight_on_never_back(recorded):
    later = [line for line in atc(recorded.lines) if at(line) > 3660 and "Budapest Control" in line]
    assert later and all("Budapest Departure" not in line for line in later), later
    assert any("contact" in line and "Radar" in line for line in later)
