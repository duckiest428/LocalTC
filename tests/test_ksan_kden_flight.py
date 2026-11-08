"""San Diego to Denver (5 November 2026, an A320neo as Frontier 4990, on LocalTC 0.5.5), with the pilot's notes:

- The LONGZ3 arrival ends at KAILE, five miles off the 17R final, and approach turned the flight onto headings there
  with the approach's own waypoints still in the FMS ("we still have upcoming waypoints for the approach we can use,
  no?"). The sim's navdata has the ILS 17R's KAILE transition (KAILE, OVVAL, HOOPE): read now, and the approach is
  cleared from the STAR. (Denver's approaches were read from the sim after the flight and put into the fixture at
  8,000 s, where the app asks for them now.) The sim has the LONGZ4, the plan the LONGZ3: one arrival of that name,
  so it's that one.
- Tower sent the flight off at M2 with the high-speed M4 ahead of it, the one it turned off at.
"""

from pathlib import Path

import pytest

from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kden"


def scenario(**meta) -> Scenario:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    return Scenario(scenario=ScenarioMeta(recording=str(FLIGHT), **meta), flight=cfg.flight, atc=cfg.atc)


@pytest.fixture(scope="module")
def copilot():
    """The copilot on the radio all the way, as it was from the climb on."""
    return run(scenario(copilot="full"), FLIGHT, recording=FLIGHT)


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def test_the_star_ends_where_the_approach_goes_on_and_it_is_cleared_without_vectors(copilot):
    arrival = [line for line in atc(copilot.lines) if 9400 < at(line) < 10700]
    assert any("cleared ILS runway 17R approach" in line for line in arrival), arrival
    assert not [line for line in arrival if "heading" in line], arrival


def test_approach_says_nothing_more_once_it_has_handed_the_flight_to_tower(copilot):
    lines = atc(copilot.lines)
    handoff = next(at(line) for line in lines if "Denver Approach" in line and "contact Denver Tower" in line)
    assert not [line for line in lines if "Denver Approach" in line and at(line) > handoff + 1 and "knots" in line]


def test_tower_sends_it_off_at_the_high_speed_exit_it_can_make(copilot):
    vacate = [line for line in atc(copilot.lines) if "vacate" in line or "turn right onto" in line or "turn left onto" in line]
    assert vacate and "M2" not in vacate[0], vacate


def _joined_at(tmp_path, t0: float) -> Path:
    """The flight as if LocalTC had started at ``t0`` (the app restarted in the air): the sim's data about the world
    kept, what the flight did before then gone."""
    import gzip
    import json

    keep = {"header", "airport_data", "departures_data", "approaches_data", "weather_report", "atis_report",
            "terrain_grid", "nearby_airports", "aircraft_identity", "aircraft_input_events", "model_list"}
    out = tmp_path / f"joined_{int(t0)}"
    out.mkdir()
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt", encoding="utf-8") as fin, \
            gzip.open(out / "session.jsonl.gz", "wt", encoding="utf-8") as fout:
        for line in fin:
            row = json.loads(line)
            if row.get("type") in keep or row.get("t", 0) >= t0:
                fout.write(line)
    return out


@pytest.mark.parametrize("t0, station, words", [(6000, "Los Angeles Center", "radar contact"),
                                                 (10215, "Denver Approach", "17R")])
def test_started_in_the_air_it_carries_on_from_where_the_flight_is(tmp_path, t0, station, words):
    """The app restarted mid-flight, a new flight with the filed plan: the clearance and takeoff behind it, the
    controller for where it is worked out, and the copilot over to them and checking in."""
    from localtc.sim_api import AtcAlert

    flight = _joined_at(tmp_path, t0)
    cfg = with_recorded(load_config(), Recording(flight).header.config)
    result = run(Scenario(scenario=ScenarioMeta(recording=str(flight), copilot="full"), flight=cfg.flight, atc=cfg.atc),
                 flight, recording=flight)
    joined = [o for o in result.outputs if isinstance(o, AtcAlert) and o.kind == "joined"]
    assert len(joined) == 1 and station in joined[0].detail
    first = next(line for line in atc(result.lines) if station in line and "traffic" not in line)
    assert words in first and at(first) - t0 < 60, first
    assert not [line for line in result.lines if "clearance" in line.lower() and "request" in line.lower()]
