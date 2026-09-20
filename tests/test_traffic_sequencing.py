"""Tower and the sim's own AI traffic: going around for an occupied runway, and the landing order."""

import pytest
from helpers.airports import kbfi
from test_phase import own

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.sim_api import AirportData, AtcTransmission, TrafficSnapshot, TrafficTarget


def said(engine: AtcEngine, events: list) -> list[str]:
    """Instruction ids ATC actually transmitted, plus anything still waiting to be."""
    return [e.instruction_id for e in events if isinstance(e, AtcTransmission)] + \
           [item.instruction_id for item in engine._scheduled]


def approaching(engine: AtcEngine, *, distance_nm: float) -> object:
    """An own-ship on final for KBFI's 14R, ``distance_nm`` out."""
    geometry = engine.geometry("KBFI")
    end = geometry.end("14R")
    north = end.threshold[1] + distance_nm * 1852.0 * 0.766  # back up the approach on the runway heading
    east = end.threshold[0] - distance_nm * 1852.0 * 0.643
    lat, lon = geometry.frame.to_latlon(east, north)
    return own(500.0, lat=lat, lon=lon, hdg_true=end.heading_true, hdg_mag=end.heading_true, on_ground=False,
               alt_msl_ft=1000, alt_indicated_ft=1000, alt_agl_ft=900, gs_kt=130, ias_kt=130, com1_mhz=120.6)


def at_runway(engine: AtcEngine, runway: str, *, gs_kt: float = 0.0) -> TrafficTarget:
    geometry = engine.geometry("KBFI")
    end = geometry.end(runway)
    lat, lon = geometry.frame.to_latlon(*end.threshold)
    return TrafficTarget(object_id=1, atc_id="SWA123", atc_model="B737", lat=lat, lon=lon, alt_ft=25.0,
                         hdg_true=end.heading_true, gs_kt=gs_kt, on_ground=True)


def tower_engine() -> AtcEngine:
    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KBFI", seed=3))
    engine.handle(AirportData(t=0.0, airport=kbfi()))
    engine.state.flight.destination = "KBFI"
    engine.state.phase = "APPROACH"
    engine.state.assignments.arrival_runway = "14R"
    return engine


def test_an_aircraft_on_the_runway_sends_this_one_around():
    engine = tower_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(at_runway(engine, "14R"),)))
    out = engine.handle(approaching(engine, distance_nm=1.0))
    assert "tower.go_around_traffic" in said(engine, out)


def test_a_clear_runway_does_not():
    engine = tower_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=()))
    out = engine.handle(approaching(engine, distance_nm=1.0))
    assert "tower.go_around_traffic" not in said(engine, out)


def test_an_aircraft_rolling_off_the_runway_does_not():
    """Landing traffic at speed is on its way off; a controller does not send anyone around for that."""
    engine = tower_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(at_runway(engine, "14R", gs_kt=60.0),)))
    out = engine.handle(approaching(engine, distance_nm=1.0))
    assert "tower.go_around_traffic" not in said(engine, out)


def test_traffic_ahead_on_the_same_final_gets_a_landing_order():
    engine = tower_engine()
    geometry = engine.geometry("KBFI")
    end = geometry.end("14R")
    east = end.threshold[0] - 3 * 1852.0 * 0.643
    north = end.threshold[1] + 3 * 1852.0 * 0.766
    lat, lon = geometry.frame.to_latlon(east, north)
    ahead = TrafficTarget(object_id=2, atc_id="SWA123", atc_model="B737", lat=lat, lon=lon, alt_ft=900.0,
                          hdg_true=end.heading_true, gs_kt=130.0, on_ground=False)
    engine.handle(TrafficSnapshot(t=1.0, targets=(ahead,)))
    out = engine.handle(approaching(engine, distance_nm=8.0))
    assert "tower.sequence" in said(engine, out), said(engine, out)
    spoken = next(e.text for e in out if isinstance(e, AtcTransmission))
    assert "number two" in spoken and "737" in spoken, spoken


def taxiing(engine: AtcEngine, *, heading: float = 90.0) -> object:
    """An own-ship taxiing on KBFI's apron, talking to ground."""
    return own(900.0, lat=47.5300, lon=-122.3020, hdg_true=heading, hdg_mag=heading, on_ground=True,
               alt_msl_ft=21, alt_indicated_ft=21, alt_agl_ft=0, gs_kt=12, ias_kt=12, com1_mhz=121.9)


def crossing_ahead(lat: float, lon: float, heading: float) -> TrafficTarget:
    return TrafficTarget(object_id=7, atc_id="ASA55", atc_model="A320", lat=lat, lon=lon, alt_ft=21.0,
                         hdg_true=heading, gs_kt=10.0, on_ground=True)


def ground_engine() -> AtcEngine:
    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KBFI", seed=3))
    engine.handle(AirportData(t=0.0, airport=kbfi()))
    engine.state.phase = "TAXI_OUT"
    return engine


def test_traffic_crossing_in_front_while_taxiing_holds_this_one():
    engine = ground_engine()
    # 100 m east of us, crossing from our left to our right while we taxi east.
    engine.handle(TrafficSnapshot(t=1.0, targets=(crossing_ahead(47.5300, -122.30067, 180.0),)))
    out = engine.handle(taxiing(engine))
    assert "ground.give_way" in said(engine, out), said(engine, out)


def test_traffic_going_the_same_way_is_not_in_the_way():
    engine = ground_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(crossing_ahead(47.5300, -122.30067, 90.0),)))
    out = engine.handle(taxiing(engine))
    assert "ground.give_way" not in said(engine, out)


def test_traffic_behind_is_not_in_the_way():
    engine = ground_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(crossing_ahead(47.5300, -122.30333, 180.0),)))
    out = engine.handle(taxiing(engine))
    assert "ground.give_way" not in said(engine, out)
