"""Real MSFS 2024 data: airports dumped from the sim and a recorded KPDX session.

Fixtures: tests/fixtures/airports_real/*.json (localtc debug airport) and
tests/fixtures/real_kpdx (a live recording; the pilot's call went unanswered
because the engine treated the COM TRANSMIT simvar as "mic keyed").
"""

import json
from pathlib import Path

import msgspec
import pytest

from localtc.airports import load_airport
from localtc.atc_core.airport import AirportGeometry, TaxiGraph, select_runway
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.phase.context import ContextBuilder
from localtc.atc_core.phraseology import speech
from localtc.atc_core.facilities import airport_facilities, center_facility
from localtc.replay import Recording
from localtc.sim_api import SIM_EVENT_TYPES, AircraftIdentity, AirportData, AtcTransmission, OwnshipState, PttPressed, PttReleased, Transcript

FIXTURES = Path(__file__).parent / "fixtures"
AIRPORTS = FIXTURES / "airports_real"
RECORDING = FIXTURES / "real_kpdx"


@pytest.fixture(scope="module")
def kpdx():
    return load_airport(AIRPORTS / "KPDX.json")


@pytest.fixture(scope="module")
def kbfi():
    return load_airport(AIRPORTS / "KBFI.json")


@pytest.fixture(scope="module")
def first_ownship() -> OwnshipState:
    return next(e for e in Recording(RECORDING).events() if isinstance(e, OwnshipState))


def test_magvar_is_east_positive(kpdx, kbfi, tmp_path):
    assert (kpdx.magvar, kbfi.magvar) == (16.0, 15.0)  # ~16°E in the Pacific Northwest
    stale = json.loads((AIRPORTS / "KPDX.json").read_text(encoding="utf-8")) | {"magvar": 344.0}  # cached before the fix
    (tmp_path / "KPDX.json").write_text(json.dumps(stale), encoding="utf-8")
    assert load_airport(tmp_path / "KPDX.json").magvar == 16.0


def test_real_frequencies_map_to_the_right_controllers(kpdx, kbfi):
    portland = {f.controller: f for f in airport_facilities(kpdx, role="departure")}
    assert [(f.station, f.mhz) for f in portland.values()] == [
        ("Portland Clearance", 120.125), ("Portland Ground", 121.9), ("Portland Tower", 118.7), ("Portland Departure", 127.85)
    ]
    assert portland["ground"].matches(132.275) and portland["tower"].matches(123.775)  # the second frequency each works
    boeing = {f.controller: f.station for f in airport_facilities(kbfi, role="arrival")}
    assert boeing == {"clearance": "Boeing Clearance", "ground": "Boeing Ground", "tower": "Boeing Tower",
                      "approach": "Seattle Approach"}


def test_taxi_routing_on_the_real_kpdx_layout(kpdx, first_ownship):
    geometry = AirportGeometry(kpdx)
    assert len(geometry.hold_shorts) == 61
    assert {e.ident for e in geometry.ends} == {"03", "21", "10L", "28R", "10R", "28L"}

    end = select_runway(geometry, first_ownship.wind_dir_true, first_ownship.wind_kt)
    assert end.ident == "10R"  # calm wind: PDX uses the 10s
    route = TaxiGraph(geometry).departure_route(first_ownship.lat, first_ownship.lon, end)
    assert route.taxiways == ("C3", "C", "C1", "B1") and route.hold_short == "10R"
    assert 1000 < route.length_m < 3000
    assert all(name.isalnum() for name in route.taxiways)


def test_arrival_runway_and_taxi_in_at_real_kbfi(kbfi):
    geometry = AirportGeometry(kbfi)
    assert select_runway(geometry, 0, 0).ident == "14R"  # calm: the ILS runway
    assert select_runway(geometry, 320, 12).ident == "32L"
    center = geometry.frame.to_latlon(*geometry.runways[0].center)
    route = TaxiGraph(geometry).parking_route(*center)
    assert route is not None and route.nodes[-1][0] == "parking"


def engine_with_real_airports(**cfg) -> AtcEngine:
    engine = AtcEngine(EngineConfig(seed=5, **cfg))
    for name in ("KPDX", "KBFI"):
        engine.handle(AirportData(t=0.0, airport=load_airport(AIRPORTS / f"{name}.json")))
    return engine


def test_live_recording_gets_an_answer(first_ownship):
    """Regression: COM TRANSMIT is true for the whole flight, which used to mute ATC completely."""
    assert first_ownship.com1_tx is True and first_ownship.com1_mhz == 124.85
    engine = engine_with_real_airports(destination="KBFI", cruise_ft=7000)
    engine.cfg.unscripted = False  # just the answer; the "how do you read?" that follows is tested elsewhere
    replies: list[AtcTransmission] = []
    said = False
    for event in Recording(RECORDING).events():
        if not isinstance(event, SIM_EVENT_TYPES):
            continue
        replies += [o for o in engine.handle(event) if isinstance(o, AtcTransmission)]
        if not said and event.t > 77:  # the call the pilot actually typed, on Portland Ground
            said = True
            text = "Portland Ground, N738B, IFR to Boeing Field, ready to copy"
            replies += [o for o in engine.handle(Transcript(t=event.t, text=text)) if isinstance(o, AtcTransmission)]
    assert [(r.station, r.instruction_id) for r in replies] == [("Portland Ground", "common.contact")]
    assert "contact Portland Clearance 120.125" in replies[0].text
    assert engine.state.flight.origin == "KPDX"


def test_push_to_talk_holds_atc_off_until_released(first_ownship):
    engine = engine_with_real_airports(destination="KBFI")
    own = msgspec.structs.replace(first_ownship, com1_mhz=120.125)  # Portland Clearance
    engine.handle(own)
    engine.handle(PttPressed(t=own.t + 1))
    engine.handle(Transcript(t=own.t + 2, text="Portland Clearance, N738B, IFR to Boeing Field, ready to copy"))
    engine.handle(PttPressed(t=own.t + 3))  # keyed again before ATC could answer
    quiet = [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + 8)) if isinstance(o, AtcTransmission)]
    assert quiet == []
    spoken = [o for o in engine.handle(PttReleased(t=own.t + 9)) if isinstance(o, AtcTransmission)]
    assert [o.instruction_id for o in spoken] == ["clearance.ifr"]  # goes out as soon as the mic is free


def test_stuck_push_to_talk_does_not_mute_atc_forever(first_ownship):
    engine = engine_with_real_airports(destination="KBFI")
    own = msgspec.structs.replace(first_ownship, com1_mhz=120.125)
    engine.handle(own)
    engine.handle(Transcript(t=own.t + 1, text="Portland Clearance, N738B, IFR to Boeing Field, ready to copy"))
    engine.handle(PttPressed(t=own.t + 2))  # release never arrives
    assert not [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + 20)) if isinstance(o, AtcTransmission)]
    late = [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + 40)) if isinstance(o, AtcTransmission)]
    assert [o.instruction_id for o in late] == ["clearance.ifr"]


# --- Canada: CYUL -> CYQB, the second live flight -------------------------------------------------

CANADA = FIXTURES / "real_cyul"


@pytest.fixture(scope="module")
def cyul():
    return load_airport(AIRPORTS / "CYUL.json")


@pytest.fixture(scope="module")
def cyqb():
    return load_airport(AIRPORTS / "CYQB.json")


def test_25khz_channel_names_reach_the_controller(cyul):
    """Regression: the sim names CYUL departure 120.42, the radio tunes 120.425 - it answered nobody."""
    departure = next(f for f in airport_facilities(cyul, role="departure") if f.controller == "departure")
    assert departure.mhz == 120.42 and departure.matches(120.425)
    assert not departure.matches(120.43)  # an adjacent 8.33 kHz channel is still a different controller
    tower = next(f for f in airport_facilities(cyul, role="departure") if f.controller == "tower")
    assert tower.matches(119.3) and not tower.matches(119.305)


def test_canadian_facility_names(cyul, cyqb):
    assert [f.station for f in airport_facilities(cyul, role="departure")] == [
        "Montreal Clearance", "Montreal Ground", "Montreal Tower", "Montreal Departure"
    ]
    assert [f.station for f in airport_facilities(cyqb, role="arrival")] == [
        "Quebec Ground", "Quebec Tower", "Quebec Ground"  # no clearance delivery: ground issues clearances
    ]
    center = center_facility([cyqb], "Seattle", 125.1)
    assert (center.station, center.mhz) == ("Montreal Center", 135.025)  # the airport's own, not the US default


def test_untranslated_sim_names_stay_out_of_the_radio(cyul):
    """Regression: MSFS 2024 returns "ATCCOM.ATC_NAME AIRBUS.0.text", which ATC read on the air."""
    identity = next(e for e in Recording(CANADA).events() if isinstance(e, AircraftIdentity))
    assert identity.atc_type == "ATCCOM.ATC_NAME AIRBUS.0.text"
    engine = AtcEngine(EngineConfig(seed=5, destination="CYQB", cruise_ft=12000, callsign="DP69"))
    engine.handle(AirportData(t=0.0, airport=cyul))
    engine.handle(identity)
    callsign = engine.state.flight.callsign
    assert engine.state.flight.aircraft_type == "A330"
    assert speech.callsign_display(callsign.short) == "DP69"  # a 4-character callsign is not abbreviated to "P69"
    assert speech.callsign(callsign.short) == "delta papa six niner"


def test_heliports_do_not_become_the_nearest_airport(cyul):
    """The 60 s nearest-airport poll picks up hospital helipads; they have no runways and no geometry."""
    builder = ContextBuilder(destination="CYQB")
    builder.add_airport(cyul)
    helipad = next(e.airport for e in Recording(CANADA).events()
                   if isinstance(e, AirportData) and e.airport.icao == "CSZ8")  # a hospital helipad, 0 runways
    builder.add_airport(helipad)
    own = next(e for e in Recording(CANADA).events() if isinstance(e, OwnshipState))
    assert builder.build(own).airport.icao == "CYUL"


def test_holding_short_alone_asks_for_takeoff(cyul):
    """The pilot said "Tower, DP69 holding short 06L" and got "say again"."""
    engine = AtcEngine(EngineConfig(seed=5, destination="CYQB", cruise_ft=12000, callsign="DP69"))
    engine.handle(AirportData(t=0.0, airport=cyul))
    own = next(e for e in Recording(CANADA).events() if isinstance(e, OwnshipState))
    engine.handle(msgspec.structs.replace(own, com1_mhz=119.3))
    replies = [o for o in engine.handle(Transcript(t=own.t + 1, text="Tower, DP69 holding short 06L"))
               if isinstance(o, AtcTransmission)]
    replies += [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + 8, com1_mhz=119.3))
                if isinstance(o, AtcTransmission)]
    assert [o.instruction_id for o in replies] == ["tower.takeoff"]


def test_clear_for_takeoff_is_an_incomplete_readback_not_nonsense():
    """"Clear for takeoff, DP69" used to match nothing at all and fall through to "say again"."""
    from localtc.atc_core.readback.extract import ELEMENTS
    from localtc.atc_core.readback.normalize import normalize

    for text in ("clear for takeoff DP69", "cleared for take off", "clear takeoff"):
        assert ELEMENTS["cleared_for_takeoff"](normalize(text)) == [True]
    assert ELEMENTS["cleared_to_land"](normalize("clear to land, DP69")) == [True]
