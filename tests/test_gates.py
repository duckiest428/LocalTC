"""Gate assignment: an airliner is sent to a free gate its size, and ground's route ends there."""

from pathlib import Path

import pytest

from localtc.app import engine_config
from localtc.atc_core.airport import AirportGeometry, TaxiGraph
from localtc.atc_core.airport import gates as stands
from localtc.atc_core.engine import AtcEngine
from localtc.atc_core.phraseology import speech
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AirportData,
    OwnshipState,
    ParkingSpot,
    TrafficTarget,
)

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kphx"


@pytest.fixture(scope="module")
def kphx() -> AirportGeometry:
    airport = next(e.airport for e in Recording(FLIGHT).events() if isinstance(e, AirportData) and e.airport.icao == "KPHX")
    return AirportGeometry(airport)


def spot(name: str, kind: str) -> ParkingSpot:
    return ParkingSpot(index=1, name=name, kind=kind, lat=0.0, lon=0.0)


@pytest.mark.parametrize(("name", "kind", "display"), [
    ("GATE B 25", "gate_medium", "Gate B25"),
    ("PARKING 3", "ramp_ga_small", "Parking 3"),
    ("S PARKING 2", "ramp_ga_large", "Parking S2"),
    ("2", "gate_small", "Gate 2"),
])
def test_how_a_stand_is_named(name, kind, display):
    assert stands.parse(spot(name, kind)).display == display


def test_nobody_is_sent_to_the_fuel_pumps():
    assert stands.parse(spot("PARKING 1", "fuel")) is None
    assert stands.parse(spot("PARKING 2", "vehicle")) is None


def test_how_a_gate_is_said():
    assert speech.gate("Gate B25") == "gate bravo two five"


def test_an_airliner_gets_a_gate_and_a_heavy_a_heavy_gate(kphx):
    narrow = stands.assign(kphx, airline=True, aircraft_type="A320neo", traffic=(), seed="FFT2084")
    heavy = stands.assign(kphx, airline=True, aircraft_type="Boeing 777-300ER", traffic=(), seed="FFT2084")
    assert narrow.spot.kind.startswith("gate") and narrow.word == "gate"
    assert heavy.spot.kind == "gate_heavy"


def test_the_same_flight_gets_the_same_gate(kphx):
    picks = {stands.assign(kphx, airline=True, aircraft_type="A320", traffic=(), seed="FFT2084").index for _ in range(3)}
    assert len(picks) == 1


def test_an_occupied_gate_is_not_assigned(kphx):
    first = stands.assign(kphx, airline=True, aircraft_type="A320", traffic=(), seed="FFT2084")
    parked = TrafficTarget(object_id=1, lat=first.spot.lat, lon=first.spot.lon, alt_ft=1135.0, hdg_true=0.0,
                           gs_kt=0.0, on_ground=True)
    second = stands.assign(kphx, airline=True, aircraft_type="A320", traffic=(parked,), seed="FFT2084")
    assert second is not None and second.index != first.index


def test_the_taxi_route_ends_at_the_gate(kphx):
    gate = stands.assign(kphx, airline=True, aircraft_type="A320", traffic=(), seed="FFT2084")
    route = TaxiGraph(kphx).parking_route(33.4343, -112.0116, gate.index)
    assert route is not None and route.nodes[-1] == ("parking", gate.index)


def test_after_landing_at_phoenix_ground_names_the_gate():
    """The recorded pilot never asked for taxi in; ask on their behalf once the flight is off the runway."""
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    engine = AtcEngine(engine_config(cfg.flight, cfg.atc))
    for event in Recording(FLIGHT).events():
        if isinstance(event, SIM_EVENT_TYPES):
            engine.handle(event)
        if isinstance(event, OwnshipState) and event.t > 4520:
            break
    engine._scheduled.clear()
    engine._taxi_in(4520.0, engine.facility("ground"), engine.state.aircraft)
    call = engine._scheduled[-1]
    assert call.instruction_id == "ground.taxi_to_gate"
    assert call.slots["gate"].startswith("Gate ") and call.slots["taxi_route"]
    assert engine.state.assignments.gate == call.slots["gate"]


def test_general_aviation_still_taxis_to_parking():
    from helpers.airports import kbfi

    from localtc.atc_core.engine import EngineConfig

    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KBFI", seed=3))
    engine.handle(AirportData(t=0.0, airport=kbfi()))
    assert engine._gate(engine.geometry("KBFI")) is None


def test_the_live_map_pins_the_assigned_gate():
    from localtc.ui.zones import zones

    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    engine = AtcEngine(engine_config(cfg.flight, cfg.atc))
    for event in Recording(FLIGHT).events():
        if isinstance(event, SIM_EVENT_TYPES):
            engine.handle(event)
        if isinstance(event, OwnshipState) and event.t > 4520:
            break
    assert zones(engine, None, lambda icao: None, None)["gate"] is None
    engine._taxi_in(4520.0, engine.facility("ground"), engine.state.aircraft)
    pin = zones(engine, None, lambda icao: None, None)["gate"]
    assert pin["icao"] == "KPHX" and pin["name"] == engine.state.assignments.gate
