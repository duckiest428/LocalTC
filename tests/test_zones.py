"""The Live Map's ATC layer: it has to show what the engine uses, before a flight and during one."""

import json
from pathlib import Path

import msgspec

from localtc.app import engine_config
from localtc.atc_core.engine import AtcEngine
from localtc.config import load_config, with_recorded
from localtc.flightplan import FlightPlan
from localtc.replay import Recording
from localtc.sim_api import SIM_EVENT_TYPES, Airport, AirportData, OwnshipState
from localtc.ui.zones import zones

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kphx"


def plan() -> FlightPlan:
    return msgspec.json.decode((FLIGHT / "flightplan.json").read_bytes(), type=FlightPlan)


def recorded_airports() -> dict[str, Airport]:
    return {e.airport.icao: e.airport for e in Recording(FLIGHT).events() if isinstance(e, AirportData)}


def test_before_the_flight_the_plan_shows_who_it_will_meet():
    found = recorded_airports()
    z = zones(None, plan(), found.get, None)
    on_route = {c["name"] for c in z["centers"] if c["route"]}
    assert on_route == {"Los Angeles Center", "Albuquerque Center"}
    assert [(t["name"], t["role"]) for t in z["terminals"]] == [("SoCal Approach", "departure"), ("Phoenix Approach", "arrival")]
    assert [a["icao"] for a in z["airports"]] == ["KSAN", "KPHX"]
    assert {s["controller"] for s in z["airports"][0]["stations"]} >= {"clearance", "ground", "tower", "departure"}
    assert z["final"]["runway"] == "26" and len(z["final"]["ring"]) == 4


def test_the_final_box_lies_on_the_approach_side_of_the_runway():
    """Runway 26 is landed on heading west: the stretch where approach clears it is east of the field."""
    z = zones(None, plan(), recorded_airports().get, None)
    phx = next(a for a in z["airports"] if a["icao"] == "KPHX")
    assert all(lon > phx["lon"] for _, lon in z["final"]["ring"])


def test_during_the_flight_it_shows_who_the_pilot_is_talking_to():
    """Run the flight to the Arizona line: Albuquerque's airspace, and the centre working the flight."""
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    engine = AtcEngine(engine_config(cfg.flight, cfg.atc))
    for event in Recording(FLIGHT).events():
        if isinstance(event, SIM_EVENT_TYPES):
            engine.handle(event)
        if isinstance(event, OwnshipState) and event.t > 3100:
            break
    z = zones(engine, None, lambda icao: g.airport if (g := engine.geometry(icao)) else None, (31.0, -116.0, 35.0, -110.0))
    assert z["center"] == "Albuquerque Center"
    here = next(c for c in z["centers"] if c["active"])
    assert here["name"] == "Albuquerque Center" and here["route"]
    json.dumps(z)  # it goes to the page as JSON
