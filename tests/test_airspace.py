"""Who controls where: the real enroute centres and approach areas behind LocalTC's handoffs and the Live Map."""

from pathlib import Path

import pytest

from localtc.atc_core.airspace import Airspace
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

AIRSPACE = Airspace.load()


@pytest.mark.parametrize(("place", "lat", "lon", "center"), [
    ("San Diego", 32.7336, -117.1897, "Los Angeles Center"),
    ("Phoenix", 33.4343, -112.0116, "Albuquerque Center"),
    ("Paine Field", 47.9063, -122.2816, "Seattle Center"),
    ("Vancouver", 49.1947, -123.1792, "Vancouver Centre"),  # Canada spells it so
    ("Calgary", 51.1225, -114.0133, "Edmonton Centre"),
    ("Rome", 41.8003, 12.2389, "Roma Radar"),
    ("Heathrow", 51.47, -0.45, "London Control"),  # "London TMA (Up to FL195) - London" in the source
    ("mid-Atlantic", 50.0, -30.0, "Shanwick Oceanic"),
    ("Sydney", -33.94, 151.17, "Melbourne Centre"),  # "Melbourne 129.8*": a sector named by its frequency
])
def test_the_centre_for_a_place(place, lat, lon, center):
    assert AIRSPACE.center_at(lat, lon).name == center


@pytest.mark.parametrize(("icao", "lat", "lon", "name"), [
    ("KSAN", 32.7336, -117.1897, "SoCal Approach"),
    ("KPHX", 33.4343, -112.0116, "Phoenix Approach"),
    ("KBFI", 47.53, -122.3019, "Seattle Approach"),  # not named for Boeing Field, but it's in there
    ("CYVR", 49.1947, -123.1792, "Vancouver Arrival"),
    ("KGEU", 33.5269, -112.2951, "Luke Approach"),  # what MSFS publishes at Glendale too
])
def test_the_approach_area_for_an_airport(icao, lat, lon, name):
    area = AIRSPACE.approach_for(icao, lat, lon)
    assert area.name == name and area.contains(lat, lon)


def test_departures_have_their_own_area_where_there_is_one():
    assert AIRSPACE.approach_for("CYVR", 49.1947, -123.1792, role="departure").name == "Vancouver Departure"


def test_no_military_areas_and_no_frequencies_in_names():
    names = [a.name for a in AIRSPACE.centers]
    assert not [n for n in names if "military" in n.lower()]
    assert not [n for n in names if any(c.isdigit() for c in n)]


def test_the_data_is_small_enough_to_ship():
    here = Path(AIRSPACE.centers[0].__class__.__module__.replace(".", "/"))
    files = list((Path(__file__).parent.parent / "src" / here).glob("*.json.gz"))
    assert {f.name for f in files} == {"centers.json.gz", "approaches.json.gz"}
    assert sum(f.stat().st_size for f in files) < 600_000


# --- the handoffs, on a real flight ---------------------------------------------------------------------


FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kphx"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def test_san_diego_to_phoenix_goes_through_the_centres_it_flies_through(replay):
    """ "Seattle Center" all the way from San Diego to Phoenix, because that was [atc] center_name. The
    flight is Los Angeles Center's out of SoCal, and Albuquerque's across the Arizona line."""
    contacts = [line.split("contact ", 1)[1].split(" 1")[0] for line in replay if " ATC " in line and "contact " in line]
    order = list(dict.fromkeys(contacts))
    airborne = order[order.index("Socal Departure"):]
    assert airborne[:4] == ["Socal Departure", "Los Angeles Center", "Albuquerque Center", "Phoenix Approach"], order
    assert not any("Seattle" in line for line in replay if " ATC " in line)


def test_a_centre_boundary_is_crossed_before_it_is_handed_over(replay):
    """Handed to Albuquerque after crossing the line, not on approaching it."""
    handoff = next(line for line in replay if "contact Albuquerque Center" in line)
    t = float(handoff.split("]")[0].strip("[ "))
    import gzip
    import json

    with gzip.open(FLIGHT / "session.jsonl.gz", "rt") as f:
        own = [json.loads(line) for line in f if '"ownship_state"' in line]
    at = min(own, key=lambda o: abs(o["t"] - t))
    assert AIRSPACE.center_at(at["lat"], at["lon"]).name == "Albuquerque Center"
