"""After landing: the way in from where the aircraft stopped (ahead of it, not a U-turn), no "hold short" of a runway
it only passes beside, and a stand its wings fit. Haneda's own layout (the sim's facility data, 2026-10-12)."""

import math
from pathlib import Path

import pytest

from localtc.airports import load_airport
from localtc.atc_core.airport import aircraft_size
from localtc.atc_core.airport import gates as stands
from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.airport.taxi_route import TaxiGraph
from localtc.sim_api import TrafficTarget

RJTT = Path(__file__).parent / "fixtures" / "airports_real" / "RJTT.json"
# Where the A350 stopped after vacating 04 by T8, facing 291.
STOPPED = (35.561896, 139.771186, 291.0)


@pytest.fixture(scope="module")
def haneda() -> AirportGeometry:
    return AirportGeometry(load_airport(RJTT))


def test_the_way_in_starts_ahead_not_with_a_u_turn(haneda):
    lat, lon, heading = STOPPED
    graph = TaxiGraph(haneda)
    gate = stands.named(haneda, "58")
    route = graph.parking_route(lat, lon, gate.index, heading=heading)
    (ax, ay), (bx, by) = graph.positions[route.nodes[0]], graph.positions[route.nodes[1]]
    first = math.degrees(math.atan2(bx - ax, by - ay)) % 360
    assert abs((first - heading + 180) % 360 - 180) <= 100, (route.via, first)


def test_no_hold_short_of_the_runway_just_left_when_the_stand_is_this_side(haneda):
    """ "Hold short 09L" after turning off 27R towards the stands, and Haneda's "hold short runway 22": the route
    passed the runway's hold line and clipped its edge, never going to its far side."""
    lat, lon, heading = STOPPED
    graph = TaxiGraph(haneda)
    runway = next(r for r in haneda.runways if r.name == "22/04")
    side = runway.along_across(haneda.xy(lat, lon))[1] > 0
    near = min((g for g in stands.gates(haneda) if (runway.along_across(haneda.xy(g.spot.lat, g.spot.lon))[1] > 0) == side),
               key=lambda g: math.dist(haneda.xy(lat, lon), haneda.xy(g.spot.lat, g.spot.lon)))
    route = graph.parking_route(lat, lon, near.index, heading=heading)
    assert route is not None and route.crossings == ()


def test_a_route_to_the_far_side_still_crosses(haneda):
    lat, lon, heading = STOPPED
    route = TaxiGraph(haneda).parking_route(lat, lon, stands.named(haneda, "58").index, heading=heading)
    assert route.crossings == ("22/04",)


def test_narrow_taxiways_are_kept_off_a_big_aircrafts_route(haneda):
    lat, lon, heading = STOPPED
    gate = stands.named(haneda, "58")
    wide = TaxiGraph(haneda, wingspan_m=64.8).parking_route(lat, lon, gate.index, heading=heading)
    widths = {(p.start, p.end): p.width_m for p in haneda.airport.taxi_paths}
    used = [widths.get((a[1], b[1])) or widths.get((b[1], a[1])) for a, b in zip(wide.nodes, wide.nodes[1:])]
    narrow = [w for w in used if w and w < 23.0 * 0.65]
    assert not narrow, used


def test_sizes_by_type_and_title():
    assert aircraft_size.designator("A330-300 (RR)") == "A333"
    assert aircraft_size.designator("A350-1000 (Default Cabin)") == "A35K"
    assert aircraft_size.code_letter(aircraft_size.wingspan_m("A35K")) == "E"
    assert aircraft_size.code_letter(aircraft_size.wingspan_m("A20N")) == "C"
    assert aircraft_size.taxiway_width_m(aircraft_size.wingspan_m("A388")) == 25.0
    assert not aircraft_size.fits_stand(26.0, 64.8) and aircraft_size.fits_stand(32.0, 64.8)
    assert aircraft_size.wingspan_m("ZZZZ") == 0.0 and aircraft_size.fits_stand(10.0, 0.0)


def test_a_stand_the_wings_fit(haneda):
    gate = stands.assign(haneda, airline=True, aircraft_type="A35K", traffic=(), seed="BAW7")
    assert gate is not None and gate.spot.radius_m + 1.0 >= 64.8 / 2


def test_the_airlines_own_terminal(haneda):
    """British Airways parked at one terminal: a stand there, not across the field."""
    some = [g for g in stands.gates(haneda) if g.spot.kind == "gate_heavy" and aircraft_size.fits_stand(g.spot.radius_m, 64.8)]
    theirs = some[len(some) // 2]
    parked = TrafficTarget(object_id=1, atc_id="BAW5", lat=theirs.spot.lat, lon=theirs.spot.lon, alt_ft=20,
                           hdg_true=0, gs_kt=0, on_ground=True)
    gate = stands.assign(haneda, airline=True, aircraft_type="A35K", traffic=(parked,), seed="BAW7", operator="BAW")
    apart = math.dist(haneda.xy(gate.spot.lat, gate.spot.lon), haneda.xy(theirs.spot.lat, theirs.spot.lon))
    assert apart < 600, (gate.display, apart)
