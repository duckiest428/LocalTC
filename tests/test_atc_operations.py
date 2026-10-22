"""Phase 3 of the ATC: conditional clearances kept as state and watched, holds and the sequence, terrain-aware
minimums, and dynamic routing around hazards."""

import json
import math

from helpers.airports import kbfi

from localtc.atc_core.airport.aircraft_size import heavier, wake_category
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.facilities import Facility
from localtc.atc_core.holds import Hold, entry_for, racetrack
from localtc.atc_core.routing import Hazard, hazards_from
from localtc.atc_core.terrain import TerrainModel, minimum_over
from localtc.sim_api import AirportData, OpsSnapshot, TerrainGrid, TrafficSnapshot, TrafficTarget


def engine_at_kbfi(style: str = "faa") -> AtcEngine:
    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KPAE", seed=3, phraseology=style))
    engine.handle(AirportData(t=0.0, airport=kbfi()))
    engine.state.flight.origin = "KBFI"
    return engine


def on_final(engine: AtcEngine, runway: str, miles: float, object_id: int = 7, model: str = "B737",
             climbing: bool = False) -> TrafficTarget:
    geo = engine.geometry("KBFI")
    end = geo.end(runway)
    ux, uy = (end.threshold[0] - end.runway.center[0], end.threshold[1] - end.runway.center[1])
    scale = miles * 1852.0 / (ux * ux + uy * uy) ** 0.5
    lat, lon = geo.frame.to_latlon(end.threshold[0] + ux * scale, end.threshold[1] + uy * scale)
    return TrafficTarget(object_id=object_id, atc_id="SWA1", atc_model=model, lat=lat, lon=lon,
                         alt_ft=miles * 300 + 20, hdg_true=end.heading_true, gs_kt=140.0, on_ground=False)


TOWER = Facility("tower", "Boeing Tower", 120.6, "KBFI")
CENTER = Facility("center", "Seattle Center", 125.1)


def released(engine: AtcEngine, traffic: tuple[TrafficTarget, ...], t: float = 1.0) -> str:
    engine._scheduled.clear()
    engine.handle(TrafficSnapshot(t=t, targets=traffic))
    engine._release(t, TOWER, "14R", answering=False)
    return engine._scheduled[-1].instruction_id if engine._scheduled else ""


# --- conditional clearances -----------------------------------------------------------------------------------


def test_icao_lines_the_departure_up_behind_the_landing_traffic_and_watches_it():
    engine = engine_at_kbfi("icao")
    arrival = on_final(engine, "14R", 2.0)
    assert released(engine, (arrival,)) == "tower.luaw_behind"
    condition = engine.conditions.active("behind_landing")
    assert condition is not None and condition.target == arrival.object_id and condition.runway == "14R"
    # Landed: the condition is met, and the departure goes once the runway's clear.
    landed = TrafficTarget(**{**{f: getattr(arrival, f) for f in arrival.__struct_fields__}, "on_ground": True,
                              "alt_ft": 20.0, "gs_kt": 60.0})
    engine.handle(TrafficSnapshot(t=30.0, targets=(landed,)))
    engine._watch_behind_landing(condition, _own(engine, 30.0))
    assert condition.state == "satisfied" and engine._takeoff_hold == "occupied"


def test_the_faa_never_conditions_a_line_up():
    engine = engine_at_kbfi("faa")
    assert released(engine, (on_final(engine, "14R", 2.0),)) == "tower.hold_short_traffic"
    assert engine.conditions.active("behind_landing") is None


def test_an_arrival_just_beyond_the_margin_makes_it_no_delay():
    engine = engine_at_kbfi()
    assert released(engine, (on_final(engine, "14R", 5.5),)) == "tower.takeoff"
    item = next(s for s in engine._scheduled if s.instruction_id == "tower.takeoff")
    assert item.note is not None and "no delay" in item.note.display
    assert engine.conditions.active("no_delay") is not None


def test_behind_a_heavy_departure_the_wake_time_comes_first():
    engine = engine_at_kbfi()
    engine.state.flight.aircraft_type = "C172"
    engine._wake_departures["14R"] = (0.0, "H", "Boeing 777")
    assert released(engine, (), t=30.0) == "tower.hold_wake"
    assert engine.conditions.active("wake", "14R") is not None
    # Two minutes on: cleared, with the caution.
    engine._scheduled.clear()
    engine._release(130.0, TOWER, "14R", answering=False)
    item = engine._scheduled[-1]
    assert item.instruction_id == "tower.takeoff" and "caution wake turbulence" in item.note.display


def test_wake_categories():
    assert [wake_category(x) for x in ("A388", "B77W", "A320", "C172", "A330-300 (RR)")] == ["J", "H", "M", "L", "H"]
    assert heavier("H", "M") and heavier("J", "H") and not heavier("M", "L") and not heavier("H", "H")


# --- holds ---------------------------------------------------------------------------------------------------


def test_hold_entries_by_the_70_degree_line():
    # Inbound 360, right turns: arriving heading 090 is direct, 150 teardrop, 225 parallel.
    assert entry_for(90, 360, "right") == "direct"
    assert entry_for(150, 360, "right") == "teardrop"
    assert entry_for(225, 360, "right") == "parallel"
    assert entry_for(360, 360, "right") == "direct"
    # Left turns mirror it.
    assert entry_for(210, 360, "left") == "teardrop" and entry_for(135, 360, "left") == "parallel"


def test_the_racetrack_starts_and_ends_at_the_fix_on_the_holding_side():
    pts = racetrack(47.0, -122.0, 0.0, "right", 4.0, 1.3)
    assert pts[0] == pts[-1]
    assert abs(pts[0][0] - 47.0) < 1e-4 and abs(pts[0][1] + 122.0) < 1e-4  # the fix
    assert max(p[1] for p in pts) > -122.0 + 0.03  # right turns, inbound north: the hold lies east
    assert min(p[0] for p in pts) < 47.0 - 4.0 / 60.0  # the outbound leg runs south of the fix


def test_a_requested_hold_is_given_with_its_efc_and_left_on_request():
    from localtc.atc_core.readback import Interpretation
    from localtc.atc_core.route import RouteFix

    engine = engine_at_kbfi()
    engine.state.flight.destination = "KBFI"
    engine.route = type(engine.route)((RouteFix("BLAKO", 47.80, -122.31), RouteFix("SEA", 47.43, -122.31)))
    own = _own(engine, 100.0, lat=47.9, lon=-122.31, hdg_true=180.0, on_ground=False, alt=8000.0, zulu_s=14 * 3600.0)
    engine.state.aircraft = own
    center = CENTER
    interp = Interpretation(kind="request", intent="request_hold", values={"fix": "BLAKO"}, text="request holding at BLAKO")
    engine._hold_request(interp, center, 100.0, own)
    item = engine._scheduled[-1]
    assert item.instruction_id == "common.hold" and item.slots["fix"] == "BLAKO" and item.slots["efc"] > "1400"
    assert engine.hold is not None and engine.hold.reason == "requested" and engine.hold.entry == "direct"
    assert engine._holding()
    engine._scheduled.clear()
    engine._hold_request(Interpretation(kind="request", intent="request_hold", values={"leave": True}, text=""),
                         center, 400.0, own)
    assert engine._scheduled[-1].instruction_id == "common.leave_hold" and engine._scheduled[-1].slots["fix"] == "SEA"


def test_arrival_demand_holds_the_flight_with_its_place_in_the_sequence():
    from localtc.atc_core.route import RouteFix

    engine = engine_at_kbfi()
    engine.state.flight.destination = "KBFI"
    geo = engine.geometry("KBFI")
    apt = geo.airport
    engine.route = type(engine.route)((RouteFix("BLAKO", apt.lat + 0.5, apt.lon), ))
    own = _own(engine, 100.0, lat=apt.lat + 1.2, lon=apt.lon, hdg_true=180.0, on_ground=False, alt=12000.0)
    # Six arrivals ahead, on their way in from the north.
    traffic = tuple(TrafficTarget(object_id=100 + i, atc_id=f"ASA{i}", atc_model="B738", lat=apt.lat + 0.1 + 0.1 * i,
                                  lon=apt.lon, alt_ft=4000 + 1000 * i, hdg_true=180.0, gs_kt=250, on_ground=False)
                    for i in range(6))
    engine.handle(TrafficSnapshot(t=99.0, targets=traffic))
    engine._traffic_before = {}
    why = engine._hold_reason(own)
    assert why == ("arrival traffic", 7)
    assert engine._give_hold(own, CENTER, *why, 100.0)
    assert engine.hold.position == 7 and engine.hold.delay_min == 12 and "number 7" in engine._hold_message(engine.hold).display


def test_hold_to_json_for_the_maps():
    h = Hold("BLAKO", 47.8, -122.3, 180.0, 160, "right", 1.0, 8000, "requested", 0.0, 900.0, "1415")
    j = h.to_json()
    assert j["fix"] == "BLAKO" and len(j["racetrack"]) > 10 and j["efc"] == "1415"


# --- terrain ---------------------------------------------------------------------------------------------------


def grid(lat0: float, lon0: float, rows: int, cols: int, ft) -> TerrainGrid:
    return TerrainGrid(t=0.0, lat0=lat0, lon0=lon0, dlat=2 / 60, dlon=2 / 60 / math.cos(math.radians(lat0 + rows / 60)),
                       rows=rows, cols=cols, ft=tuple(ft))


def test_terrain_minimums_from_the_grid():
    model = TerrainModel()
    assert model.highest_ft(47.0, 11.0, 3) is None  # nothing known: no minimum, never a guess
    cells = [500] * 400
    cells[10 * 20 + 10] = 9200  # one peak in the middle
    model.add(grid(46.67, 10.5, 20, 20, cells))
    assert model.highest_ft(47.0, 11.0, 3.0) == 9200
    assert model.mva_ft(47.0, 11.0) == 11200  # mountains: 2,000 ft above
    assert model.minimum_ft(46.75, 10.6, 2.0) == 1500
    assert minimum_over(2400) == 3400 and minimum_over(3000) == 5000
    # Along a heading towards the peak, the corridor's minimum is the peak's.
    assert model.corridor_ft(46.85, 11.0, 0.0, 12.0) == 11200


def test_the_ops_terrain_grid_from_a_tile(tmp_path):
    import numpy as np

    from localtc.ops.terrain import Terrain, tile_xy

    terrain = Terrain(tmp_path)
    x, y = tile_xy(47.0, 11.0)
    tile = np.full((256, 256), 1000, dtype=np.int32)
    tile[int((y % 1) * 256), int((x % 1) * 256)] = 8000
    terrain._tiles[(9, int(x), int(y))] = tile
    got = terrain.grid(47.0, 11.0, 6.0)
    assert got is not None and max(got["ft"]) == 8000 and got["rows"] == 6


def test_the_engine_keeps_the_initial_climb_above_the_mountains():
    engine = engine_at_kbfi()
    geo = engine.geometry("KBFI")
    rows = 40
    engine.handle(grid(geo.airport.lat - 0.66, geo.airport.lon - 0.95, rows, rows, [12000] * rows * rows))
    assert engine._initial_altitude() == 14000  # 12,000 ft of ground: 2,000 ft above it


# --- routing ---------------------------------------------------------------------------------------------------


def test_hazards_from_the_ops_snapshot_and_the_line_through_them():
    square = [[47.0, -122.0], [47.0, -121.0], [48.0, -121.0], [48.0, -122.0]]
    ev = OpsSnapshot(t=0.0, kind="hazards", data=json.dumps([{"id": "R1", "kind": "restricted", "name": "R-6714",
                                                                "polygon": square, "floor_ft": 0, "ceiling_ft": 18000}]))
    (h,) = hazards_from(ev)
    assert isinstance(h, Hazard) and h.contains(47.5, -121.5) and not h.contains(46.5, -121.5)
    assert h.crosses((46.5, -121.5), (48.5, -121.5)) and not h.crosses((46.5, -123.0), (48.5, -123.0))
    assert h.at_level(10000) and not h.at_level(30000)


def test_a_restricted_area_on_the_route_ahead_gets_vectors_around_it():
    from localtc.atc_core.route import RouteFix

    engine = engine_at_kbfi()
    engine.state.flight.destination = "KBFI"
    engine.route = type(engine.route)((RouteFix("ALPHA", 46.0, -121.5), RouteFix("BRAVO", 47.0, -121.5),
                                       RouteFix("CHRLY", 48.5, -121.5)))
    square = [[46.4, -121.7], [46.4, -121.3], [46.6, -121.3], [46.6, -121.7]]
    engine.handle(OpsSnapshot(t=0.0, kind="hazards", data=json.dumps(
        [{"id": "R1", "kind": "restricted", "name": "R-6714", "polygon": square, "floor_ft": 0, "ceiling_ft": 18000}])))
    own = _own(engine, 50.0, lat=45.8, lon=-121.5, hdg_true=0.0, on_ground=False, alt=12000.0)
    engine.state.aircraft = own
    center = CENTER
    assert engine._hazards_ahead(own, center)
    item = engine._scheduled[-1]
    assert item.instruction_id == "center.reroute" and item.slots["fix"] == "BRAVO"
    assert engine.amendments[-1].kind == "reroute"
    # Past it, the line to BRAVO clear: "proceed direct BRAVO".
    engine._scheduled.clear()
    later = _own(engine, 400.0, lat=46.8, lon=-121.2, hdg_true=0.0, on_ground=False, alt=12000.0)
    assert engine._watch_amendments(later, center)
    assert engine._scheduled[-1].instruction_id == "common.direct" and engine._scheduled[-1].slots["fix"] == "BRAVO"


def test_operations_go_out_for_the_maps():
    engine = engine_at_kbfi()
    data = engine.operations_json(_own(engine, 1.0))
    assert set(data) == {"conditions", "hold", "routing", "hazards", "minimum_altitude_ft"}


def _own(engine: AtcEngine, t: float, *, lat: float | None = None, lon: float | None = None, hdg_true: float = 140.0,
         on_ground: bool = True, alt: float = 20.0, zulu_s: float | None = None):
    from localtc.sim_api import OwnshipState

    geo = engine.geometry("KBFI")
    return OwnshipState(t=t, lat=lat if lat is not None else geo.airport.lat, lon=lon if lon is not None else geo.airport.lon,
                        alt_msl_ft=alt, alt_indicated_ft=alt, alt_agl_ft=alt - 20, hdg_true=hdg_true, hdg_mag=hdg_true - 15,
                        gs_kt=0.0 if on_ground else 250.0, ias_kt=0.0 if on_ground else 250.0, vs_fpm=0.0,
                        on_ground=on_ground, squawk="1200", com1_mhz=0.0, com2_mhz=0.0, altimeter_inhg=29.92,
                        zulu_s=zulu_s, xpdr_mode="alt")


def test_a_shortcut_is_never_to_the_destination_or_into_the_filed_star():
    """Denver Center: "cleared direct KDEN" in the cruise, with the LAWGR3 filed. SimBrief's route ends with the airport,
    as the STAR's last fix: it was a fix further down the route. The STAR's start is as far as a shortcut goes."""
    from localtc.atc_core.route import RouteFix

    engine = engine_at_kbfi()
    engine.state.flight.destination = "KBFI"
    engine.cfg.star = "STAR1"
    engine.route = type(engine.route)((
        RouteFix("ALPHA", 45.0, -122.0, via="DCT"), RouteFix("BRAVO", 45.6, -119.5, via="DCT"),
        RouteFix("CHRLY", 46.2, -122.0, via="DCT"), RouteFix("DELTA", 46.8, -122.2, via="STAR1"),
        RouteFix("ECHOO", 47.2, -122.3, via="STAR1"), RouteFix("KBFI", 47.53, -122.3, via="STAR1")))
    engine.state.phase = "CRUISE"
    own = _own(engine, 50.0, lat=44.8, lon=-122.0, hdg_true=10.0, on_ground=False, alt=36000.0)
    engine.state.aircraft = own
    assert engine._shortcut(own, CENTER)
    fix = engine._scheduled[-1].slots["fix"]
    assert fix in ("CHRLY", "DELTA") and engine._scheduled[-1].instruction_id == "common.direct"
