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
