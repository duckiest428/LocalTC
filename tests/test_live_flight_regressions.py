"""Regressions from the first live flight (KPDX, MSFS 2024 12.2) and the first real airport dump (KPAE)."""

import asyncio
import io

import msgspec
from helpers.airports import kpae
from helpers.sim_fakes import airport_messages

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.facilities import airport_facilities
from localtc.atc_core.phase import PhaseTracker
from localtc.bus import EventBus
from localtc.sim_api import (
    Airport,
    AirportData,
    AtcAlert,
    Frequency,
    OwnshipState,
    RadioTuned,
    SimLifecycle,
    StreamClock,
    Transcript,
)
from localtc.sim_bridge import facilities as fac
from localtc.sim_bridge.protocol import FacilityData, parse_message


def own(t: float, **kw) -> OwnshipState:
    spot = kpae().parking[0]
    fields = dict(
        lat=spot.lat, lon=spot.lon, alt_msl_ft=606.0, alt_indicated_ft=606.0, alt_agl_ft=0.0, altimeter_inhg=29.92,
        hdg_mag=324.0, hdg_true=340.0, ias_kt=0.0, gs_kt=0.0, vs_fpm=0.0, on_ground=True, squawk="1200",
        xpdr_mode="alt", com1_mhz=121.8, com2_mhz=121.5, engine_running=True,
    )
    fields.update(kw)
    return OwnshipState(t=t, **fields)


def test_facility_magvar_344_means_16_east():
    raw = airport_messages(msgspec.structs.replace(kpae(), magvar=344.0), 7)[0]  # the sim's own encoding
    assembler = fac.AirportAssembler("KPAE")
    msg = parse_message(raw)
    assert isinstance(msg, FacilityData)
    assembler.add(msg)
    assert assembler.build().magvar == 16.0


def test_real_kpae_frequency_names_become_radio_callsigns():
    airport = Airport(icao="KPAE", name="Snohomish County (Paine Fld)", lat=47.9, lon=-122.28, elev_ft=584, frequencies=(
        Frequency(kind="approach", mhz=125.6, name="SEATTLE-TACOMA INTL"),
        Frequency(kind="approach", mhz=128.5, name="SEATTLE-TACOMA INTL"),
        Frequency(kind="tower", mhz=120.2, name="PAINE"), Frequency(kind="tower", mhz=132.95, name="PAINE"),
        Frequency(kind="clearance", mhz=127.175, name="PAINE"), Frequency(kind="ground", mhz=121.8, name="PAINE"),
    ))
    by_controller = {f.controller: f for f in airport_facilities(airport, role="departure")}
    assert by_controller["departure"].station == "Seattle Departure"
    assert by_controller["tower"].station == "Paine Tower" and by_controller["tower"].matches(132.95)
    assert by_controller["clearance"].station == "Paine Clearance"
    arrival = {f.controller: f.station for f in airport_facilities(airport, role="arrival")}
    assert arrival["approach"] == "Seattle Approach"


def test_pause_menu_values_do_not_change_the_phase():
    """Live log: DEPARTURE, paused, then "DEPARTURE -> LANDING (on the ground)" two seconds later."""
    tracker = PhaseTracker()
    airborne = dict(on_ground=False, alt_agl_ft=3000, vs_fpm=800, gs_kt=150)
    tracker.handle(own(0, **airborne))
    assert tracker.phase == "DEPARTURE"
    tracker.handle(SimLifecycle(t=1, kind="paused", detail="full"))
    for t in range(2, 30):
        assert tracker.handle(own(t, on_ground=True, lat=45.5, lon=-122.6)) is None  # garbage while paused
    tracker.handle(SimLifecycle(t=30, kind="unpaused"))
    assert tracker.handle(own(31, **airborne)) is None
    assert tracker.phase == "DEPARTURE"


def test_starting_on_a_runway_is_not_an_incursion():
    engine = AtcEngine(EngineConfig())
    engine.handle(AirportData(t=0, airport=kpae()))
    runway_center = kpae().runways[0]
    outputs = []
    for t in range(3):
        outputs += engine.handle(own(t, lat=runway_center.lat, lon=runway_center.lon, on_runway=True, hdg_true=340))
    assert not [o for o in outputs if isinstance(o, AtcAlert)]
    assert engine.state.phase == "RUNWAY_HOLD"


def test_tuning_feedback_and_unanswered_calls():
    engine = AtcEngine(EngineConfig())
    engine.handle(AirportData(t=0, airport=kpae()))
    tuned = [o for o in engine.handle(own(0, com1_mhz=121.8)) if isinstance(o, RadioTuned)]
    assert [(t.frequency_mhz, t.station) for t in tuned] == [(121.8, "Paine Ground")]
    assert not [o for o in engine.handle(own(1, com1_mhz=121.8)) if isinstance(o, RadioTuned)]  # only on change
    retuned = [o for o in engine.handle(own(2, com1_mhz=118.7)) if isinstance(o, RadioTuned)]
    assert [(t.frequency_mhz, t.station) for t in retuned] == [(118.7, None)]
    alerts = [o for o in engine.handle(Transcript(t=3, text="Portland Ground, N738B, ready to taxi")) if isinstance(o, AtcAlert)]
    assert [a.kind for a in alerts] == ["no_atc_on_frequency"]


def test_typed_input_publishes_transcripts():
    from localtc.app import _typed_transmissions

    class Source:
        clock = StreamClock()

    async def main():
        bus = EventBus()
        sub = bus.subscribe(Transcript)
        await _typed_transmissions(bus, Source(), stdin=io.StringIO("Paine Ground, N172LT, ready to taxi\n\n  \n"))
        bus.close()
        return [e.text async for e in sub]

    assert asyncio.run(main()) == ["Paine Ground, N172LT, ready to taxi"]
