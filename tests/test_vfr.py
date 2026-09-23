"""VFR: the airspace classes, the calls a VFR pilot makes, and what ATC does with them."""

from pathlib import Path

import msgspec
import pytest
from helpers.airports import make_airport

from localtc.airports import load_airport_dir
from localtc.atc_core.airport import AirportGeometry, classes
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.readback.intents import match_intents, resolve
from localtc.atc_core.readback.normalize import normalize
from localtc.replay import Recording
from localtc.scenario import load_scenario, run
from localtc.sim_api import AirportData, AtcAlert, OwnshipState
from localtc.ui.zones import vfr_classes

HERE = Path(__file__).parent
SCENARIOS = HERE / "scenarios"
FIXTURES = HERE / "fixtures"


# --- airspace classes ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(("icao", "towered", "approach", "icao_region", "kind"), [
    ("KSEA", True, True, False, "B"),
    ("KPDX", True, True, False, "C"),
    ("KPAE", True, True, False, "D"),
    ("S43", False, False, False, ""),  # no tower: Class E/G, ATC stays out of it
    ("LIRF", True, True, True, "CTR"),
    ("LIRU", True, False, True, "ATZ"),
])
def test_the_airspace_around_an_airport(icao, towered, approach, icao_region, kind):
    assert classes.zone_for(icao, towered=towered, approach=approach, icao_region=icao_region).kind == kind


def test_class_b_is_a_wedding_cake():
    zone = classes.zone_for("KSEA", towered=True, approach=True, icao_region=False)
    assert classes.class_b_floor_msl(zone, 433, 5) == 433  # the surface close in
    assert classes.class_b_floor_msl(zone, 433, 15) == 3433
    assert classes.class_b_floor_msl(zone, 433, 25) == 6433
    assert classes.class_b_floor_msl(zone, 433, 35) is None


def test_bearing_words():
    assert classes.bearing_words(47.0, -122.0, 47.2, -122.0) == "north"
    assert classes.bearing_words(47.0, -122.0, 46.9, -122.15) == "southwest"


# --- what the pilot says --------------------------------------------------------------------------------------

def intent(text: str):
    match, ambiguous = resolve(match_intents(normalize(text)))
    assert not ambiguous, text
    return match.intent, match.values


@pytest.mark.parametrize(("text", "expected", "values"), [
    ("Seattle Approach, Cessna 2LT, 10 miles north of Paine, 3,500, request flight following to Boeing Field",
     "request_flight_following", {}),
    ("Seattle Departure, Cessna 2LT, 1,900 climbing 4,500, request a Class Bravo clearance",
     "checkin", {"class_b": True}),
    ("Paine Ground, Cessna 2LT, at parking, VFR northbound, ready to taxi", "ready_to_taxi", {"direction": "northbound"}),
    ("Paine Ground, Cessna 2LT, remaining in the pattern, ready to taxi", "ready_to_taxi", {"direction": "closed-traffic"}),
    ("Paine Tower, Cessna 2LT, holding short 34L, ready for departure, straight out", "ready_for_departure",
     {"direction": "straight-out"}),
    ("Paine Tower, Cessna 2LT, midfield left downwind 34L, touch and go", "position_report", {"option": "touch_and_go"}),
    ("Paine Tower, Cessna 2LT, request the option", "request_option", {"option": "option"}),
    ("Frequency change approved, Cessna 2LT", "acknowledge", {}),
])
def test_vfr_calls(text, expected, values):
    got, got_values = intent(text)
    assert got == expected
    assert values.items() <= got_values.items()


# --- ATC ------------------------------------------------------------------------------------------------------

def transcript(name: str) -> str:
    path = SCENARIOS / f"{name}.toml"
    return "\n".join(run(load_scenario(path), path.parent).lines)


def atc_ids(name: str) -> list[str]:
    path = SCENARIOS / f"{name}.toml"
    return [o.instruction_id for o in run(load_scenario(path), path.parent).outputs if getattr(o, "instruction_id", None)]


def test_flight_following_through_class_b():
    ids = atc_ids("vfr_flight_following")
    for expected in ("vfr.takeoff", "vfr.frequency_change", "vfr.squawk", "vfr.radar_contact", "vfr.cleared_class_b",
                     "departure.handoff_center", "vfr.service_terminated", "vfr.enter_downwind", "tower.land"):
        assert expected in ids, expected
    assert ids.index("vfr.radar_contact") < ids.index("vfr.cleared_class_b") < ids.index("vfr.service_terminated")
    text = transcript("vfr_flight_following")
    assert "southbound departure approved" in text and "maintain VFR at or below 4,500" in text
    assert "ALERT" not in text and "say again" not in text


def test_pattern_work_touch_and_go_then_full_stop():
    ids = atc_ids("vfr_pattern")
    assert ids.index("vfr.takeoff_closed") < ids.index("vfr.touch_and_go") < ids.index("vfr.closed_traffic") \
        < ids.index("tower.land")
    text = transcript("vfr_pattern")
    assert "make left closed traffic, cleared for takeoff" in text
    assert "ALERT" not in text and "go_around" not in " ".join(ids) and "Approach" not in text


def test_pattern_work_in_icao_words():
    text = transcript("vfr_pattern_icao")
    assert "cleared for takeoff, left hand circuit" in text and "join left hand circuit" in text
    assert "QNH" in text and "ALERT" not in text


def test_entering_class_b_without_a_clearance_is_an_alert():
    engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=4500, callsign="N172LT", seed=7, rules="VFR"))
    for airport in load_airport_dir(FIXTURES / "airports"):
        engine.handle(AirportData(t=0.0, airport=airport))
    ksea = make_airport("KSEA", "Seattle-Tacoma Intl", (47.449, -122.309), 433, 340, ("16C", "34C"),
                        {"tower": (119.9, "Seattle Tower")})
    engine.handle(AirportData(t=0.0, airport=ksea))
    own = next(e for e in Recording(FIXTURES / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState) and not e.on_ground
               and e.alt_indicated_ft > 3000)
    engine.handle(next(e for e in Recording(FIXTURES / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState)))
    engine.state.phase = "CRUISE"
    near_sea = msgspec.structs.replace(own, t=own.t, lat=47.52, lon=-122.31, alt_msl_ft=4000.0, alt_indicated_ft=4000.0)
    alerts = [o for o in engine.handle(near_sea) if isinstance(o, AtcAlert)]
    assert [a.kind for a in alerts] == ["class_b_entry"]
    again = msgspec.structs.replace(near_sea, t=near_sea.t + 5)
    assert not [o for o in engine.handle(again) if isinstance(o, AtcAlert) and o.kind == "class_b_entry"]  # once


def test_class_b_needs_the_altitude_too():
    ksea = AirportGeometry(make_airport("KSEA", "Seattle-Tacoma Intl", (47.449, -122.309), 433, 340, ("16C", "34C"), {}))
    zone = classes.zone_for("KSEA", towered=True, approach=True, icao_region=False)
    fifteen_north = 47.449 + 15 / 60
    assert classes.inside_class_b(zone, ksea, fifteen_north, -122.309, 4500)
    assert not classes.inside_class_b(zone, ksea, fifteen_north, -122.309, 2500)  # under the 3,000 ft shelf
    assert not classes.inside_class_b(zone, ksea, 47.449, -122.309, 10500)  # over the top


# --- the VFR map ----------------------------------------------------------------------------------------------

KNOWN = [
    {"icao": "KSEA", "name": "Seattle-Tacoma", "lat": 47.449, "lon": -122.309, "elev_ft": 433, "tower": True,
     "approach": True, "runway_m": 3600},
    {"icao": "KPAE", "name": "Paine", "lat": 47.906, "lon": -122.282, "elev_ft": 606, "tower": True,
     "approach": True, "runway_m": 2800},
    {"icao": "S43", "name": "Harvey", "lat": 47.908, "lon": -122.105, "elev_ft": 22, "tower": False,
     "approach": False, "runway_m": 800},
    {"icao": "KPDX", "name": "Portland", "lat": 45.589, "lon": -122.597, "elev_ft": 31, "tower": True,
     "approach": True, "runway_m": 3400},
]


def test_the_vfr_map_shows_the_airspace_in_view():
    seattle = (47.0, -123.0, 48.3, -121.5)
    shown = {a["icao"]: a for a in vfr_classes(KNOWN, seattle)}
    assert set(shown) == {"KSEA", "KPAE", "S43"}  # Portland is out of view
    sea = shown["KSEA"]
    assert sea["class"] == "B" and [r["nm"] for r in sea["rings"]] == [10, 20, 30]
    assert [(r["floor"], r["ceiling"]) for r in sea["rings"]] == [(0, 10000), (3400, 10000), (6400, 10000)]
    assert shown["KPAE"]["class"] == "D" and shown["KPAE"]["rings"] == [{"nm": 4.0, "floor": 0, "ceiling": 3100}]
    assert shown["S43"]["class"] == "" and shown["S43"]["rings"] == [] and not shown["S43"]["towered"]


def test_zoomed_far_out_the_vfr_map_keeps_to_the_flight():
    continent = (20.0, -130.0, 55.0, -60.0)
    assert [a["icao"] for a in vfr_classes(KNOWN, continent, ["KPDX"])] == ["KPDX"]
