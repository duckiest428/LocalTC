"""The flight stops itself at the gate: ATC sees the aircraft parked at a gate at the destination after landing, and
the session ends ([session] auto_stop_at_gate)."""

import asyncio

import msgspec
import pytest
from test_llm import AIRPORTS, CYUL

from localtc.airports import load_airport
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.readback import PendingReadback
from localtc.bus import EventBus
from localtc.config import Config
from localtc.replay import Recording
from localtc.sim_api import AirportData, AtcAlert, FlightArrived, OwnshipState

GATE_AT_CYUL = next(e for e in Recording(CYUL).events() if isinstance(e, OwnshipState))  # the recording starts at a gate


def engine_to(destination: str, *, landed: bool) -> AtcEngine:
    engine = AtcEngine(EngineConfig(seed=5, destination=destination, callsign="DP69"))
    for icao in ("CYUL", "CYQB"):
        engine.handle(AirportData(t=0.0, airport=load_airport(AIRPORTS / f"{icao}.json")))
    engine._landed = landed  # as if it had come in from a flight: TAXI_IN after LANDING
    return engine


def arrivals(engine: AtcEngine, own: OwnshipState, seconds: float = 20.0) -> list[FlightArrived]:
    out = []
    for dt in range(int(seconds) + 1):
        out += engine.handle(msgspec.structs.replace(own, t=own.t + dt))
    return [o for o in out if isinstance(o, FlightArrived)]


def test_parked_at_a_gate_at_the_destination_after_landing_is_the_end_of_the_flight():
    engine = engine_to("CYUL", landed=True)
    [arrived] = arrivals(engine, GATE_AT_CYUL)
    assert arrived.airport == "CYUL" and arrived.gate.startswith(("Gate ", "Parking ", "parking"))
    assert arrived.t - GATE_AT_CYUL.t >= 5  # stopped there a few seconds, not just passing
    assert arrivals(engine, msgspec.structs.replace(GATE_AT_CYUL, t=GATE_AT_CYUL.t + 60)) == []  # once


def test_not_before_the_flight_and_not_at_another_airport():
    assert arrivals(engine_to("CYUL", landed=False), GATE_AT_CYUL) == []  # parked before departing
    assert arrivals(engine_to("CYQB", landed=True), GATE_AT_CYUL) == []  # a gate, but not at the destination


def test_not_while_rolling_on_a_runway_or_still_taxiing():
    engine = engine_to("CYUL", landed=True)
    assert arrivals(engine, msgspec.structs.replace(GATE_AT_CYUL, gs_kt=6.0)) == []  # still moving
    runway = next(iter(engine.geometry("CYUL").airport.runways))
    on_runway = msgspec.structs.replace(GATE_AT_CYUL, lat=runway.lat, lon=runway.lon, on_runway=True)
    assert arrivals(engine_to("CYUL", landed=True), on_runway) == []  # stopped, but on a runway
    engine = engine_to("CYUL", landed=True)
    engine.state.pending = PendingReadback("ground.taxi_in", "ground", {}, ("taxi_route",))  # a taxi instruction going
    assert arrivals(engine, GATE_AT_CYUL) == []


def test_the_gate_radius_is_the_pilots():
    engine = engine_to("CYUL", landed=True)
    spot = min(engine.geometry("CYUL").airport.parking,
               key=lambda p: (p.lat - GATE_AT_CYUL.lat) ** 2 + (p.lon - GATE_AT_CYUL.lon) ** 2)
    off = msgspec.structs.replace(GATE_AT_CYUL, lat=spot.lat + 0.0006)  # about 65 m north of it (the spot is 23 m)
    assert engine._gate_at(engine.geometry("CYUL"), off) is None
    engine.cfg.gate_radius_m = 200.0
    assert engine._gate_at(engine.geometry("CYUL"), off) is not None


def test_the_session_stops_once_on_arrival():
    from localtc.app import _stop_at_gate

    async def run() -> bool:
        bus = EventBus()
        arrived = asyncio.Event()
        task = asyncio.create_task(_stop_at_gate(bus.subscribe(FlightArrived, AtcAlert), arrived, 0.0))
        await asyncio.sleep(0)
        bus.publish(AtcAlert(t=1.0, kind="taxi_without_clearance", detail=""))
        await asyncio.sleep(0.01)
        early = arrived.is_set()
        bus.publish(FlightArrived(t=2.0, airport="CYUL", gate="Gate 12"))
        await asyncio.wait_for(task, 1.0)
        bus.close()
        return not early and arrived.is_set()

    assert asyncio.run(run())


@pytest.mark.parametrize(("setting", "default"), [("auto_stop_at_gate", True), ("auto_stop_in_replay", False)])
def test_on_by_default_but_not_for_replays(setting, default):
    assert getattr(Config().session, setting) is default
