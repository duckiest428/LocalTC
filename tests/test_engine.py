"""AtcEngine behaviors outside the scripted scenarios, the session snapshot, and the bus service."""

import asyncio
from pathlib import Path

import msgspec
from helpers.airports import kbfi, kpae

from localtc.airports import AirportCache
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.service import AtcService
from localtc.bus import EventBus, pump
from localtc.replay import ReplaySource
from localtc.scenario import run_scenario
from localtc.sim_api import (
    AircraftIdentity,
    AirportData,
    AtcAlert,
    AtcTransmission,
    OwnshipState,
    PhaseChanged,
    Transcript,
)

SCENARIOS = Path(__file__).parent / "scenarios"
FIXTURE = Path(__file__).parent / "fixtures" / "ifr_kpae_kbfi"


def parked_engine(com1: float = 127.25, **cfg) -> tuple[AtcEngine, OwnshipState]:
    engine = AtcEngine(EngineConfig(seed=3, **cfg))
    spot = kpae().parking[0]
    own = OwnshipState(
        t=0, lat=spot.lat, lon=spot.lon, alt_msl_ft=606, alt_indicated_ft=606, alt_agl_ft=0, altimeter_inhg=29.92,
        hdg_mag=324, hdg_true=340, ias_kt=0, gs_kt=0, vs_fpm=0, on_ground=True, squawk="1200", xpdr_mode="standby",
        com1_mhz=com1, com2_mhz=121.5, parking_brake=True, engine_running=True, wind_dir_true=150, wind_kt=3, magvar=16,
    )
    for event in (AirportData(t=0, airport=kpae()), AirportData(t=0, airport=kbfi()),
                  AircraftIdentity(t=0, atc_id="N172LT", atc_type="Cessna"), own):
        engine.handle(event)
    return engine, own


def say(engine: AtcEngine, own: OwnshipState, t: float, text: str) -> list:
    outputs = engine.handle(Transcript(t=t, text=text))
    for tick in range(1, 6):  # let scheduled replies go out
        outputs += engine.handle(msgspec.structs.replace(own, t=t + tick))
    return outputs


def transmissions(outputs) -> list[AtcTransmission]:
    return [o for o in outputs if isinstance(o, AtcTransmission)]


def test_nobody_answers_on_an_unknown_frequency():
    engine, own = parked_engine(com1=122.95)
    assert transmissions(say(engine, own, 5, "Paine Clearance, 172LT, IFR to Boeing, ready to copy")) == []


def test_no_destination_asks_for_it():
    engine, own = parked_engine()
    (reply,) = transmissions(say(engine, own, 5, "Paine Clearance, 172LT, IFR clearance, ready to copy"))
    assert reply.instruction_id == "clearance.say_destination"


def test_say_again_repeats_the_last_instruction():
    engine, own = parked_engine(destination="KBFI", cruise_ft=7000)
    (clearance,) = transmissions(say(engine, own, 5, "Paine Clearance, 172LT, IFR to Boeing Field, ready to copy"))
    assert "climb and maintain 5,000, expect 7,000" in clearance.text
    (repeat,) = transmissions(say(engine, own, 20, "say again for 2LT"))
    assert repeat.instruction_id == "clearance.ifr" and repeat.text.startswith("Cessna 2LT, cleared to Boeing Field")


def test_emergency_alert_and_reply():
    engine, own = parked_engine()
    outputs = say(engine, own, 5, "Mayday mayday, Skyhawk 172LT, engine fire")
    assert [o.kind for o in outputs if isinstance(o, AtcAlert)] == ["emergency"]
    assert transmissions(outputs)[0].instruction_id == "common.emergency"


def test_first_contact_uses_full_callsign_then_abbreviates():
    engine, own = parked_engine(destination="KBFI")
    first = transmissions(say(engine, own, 5, "Paine Clearance, 172LT, IFR to Boeing Field"))[0]
    second = transmissions(say(engine, own, 20, "say again, 2LT"))[0]
    assert first.text.startswith("N172LT,") and second.text.startswith("Cessna 2LT,")
    assert first.spoken.startswith("november one seven two lima tango")


def test_requests_destination_airport_data_when_missing():
    engine = AtcEngine(EngineConfig(destination="KSEA"))
    engine.handle(AirportData(t=0, airport=kpae()))
    _, own = parked_engine()
    engine.handle(own)
    engine.handle(msgspec.structs.replace(own, t=1))
    assert engine.airport_requests == ["KSEA"]


def test_snapshot_is_json_for_the_llm():
    engine = run_scenario(SCENARIOS / "wrong_readbacks.toml").engine
    snap = msgspec.json.decode(msgspec.json.encode(engine.snapshot()))
    assert snap["callsign"] == "N172LT" and snap["destination"] == "KBFI" and snap["phase"] == "DEPARTURE"
    assert snap["assignments"]["squawk"] and snap["assignments"]["departure_runway"] == "34L"
    assert {c["kind"]: c["readback"] for c in snap["clearances"]} == {"ifr": "correct", "taxi": "correct", "takeoff": "correct"}
    assert snap["recent_exchanges"][-1]["speaker"] in ("pilot", "atc")
    assert snap["tuned"]["station"] == "Paine Tower"


def test_service_on_the_bus_with_cached_destination(tmp_path):
    cache = AirportCache(tmp_path)
    cache.put(kbfi())

    async def main():
        source = ReplaySource(FIXTURE, speed=0, end_at=500, exclude_types=(AirportData,))
        await source.start()
        bus = EventBus()
        seen = bus.subscribe(PhaseChanged, AirportData)
        engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, seed=1))
        engine.handle(AirportData(t=0, airport=kpae()))  # origin known, destination only in the cache
        service = asyncio.create_task(AtcService(engine, bus, source, cache).run())
        await pump(source, bus)
        for _ in range(500):  # let the service drain its queue
            if engine.state.aircraft is not None and engine.state.aircraft.t >= 499:
                break
            await asyncio.sleep(0.01)
        bus.close()
        await service
        return [e async for e in seen], engine

    events, engine = asyncio.run(main())
    assert [e.airport.icao for e in events if isinstance(e, AirportData)] == ["KBFI"]
    assert [e.phase for e in events if isinstance(e, PhaseChanged)][:4] == ["PARKED", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF"]
    assert "KBFI" in engine.tracker.context_builder.airports
