"""Unscripted moments: requests off the standard flow, and things ATC starts on its own."""

import math
from pathlib import Path

import msgspec

from localtc.airports import load_airport_dir
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.replay import Recording
from localtc.scenario import PilotRule, When, load_scenario, run
from localtc.sim_api import (
    AirportData,
    AtcTransmission,
    OwnshipState,
    TrafficSnapshot,
    TrafficTarget,
    Transcript,
)

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
HAPPY = HERE / "scenarios" / "ifr_happy_path.toml"


def happy_path_with(*rules: PilotRule, drop: str | None = None) -> list[str]:
    scenario = load_scenario(HAPPY)
    if drop is not None:
        scenario.pilot = [r for r in scenario.pilot if drop not in r.say]
    scenario.pilot += list(rules)
    return run(scenario, HAPPY.parent).lines


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def test_going_around_gets_runway_heading_an_altitude_and_approach():
    lines = happy_path_with(PilotRule(when=When(phase="LANDING", after_s=3), say="Boeing Tower, 2LT, going around"),
                            PilotRule(on="tower.go_around", say="{readback}"))
    go = next(line for line in atc(lines) if "fly runway heading, climb and maintain" in line)
    assert "Boeing Tower" in go and "contact Seattle Approach 119.2" in go
    assert "READBACK  tower.go_around correct" in "\n".join(lines)


def test_going_around_without_a_word_still_gets_instructions():
    engine, own = cruising_engine()
    engine.state.phase = "LANDING"
    tower = engine.facility("tower")
    engine.handle(msgspec.structs.replace(own, t=own.t + 1, com1_mhz=tower.mhz))
    from localtc.sim_api import PhaseChanged

    engine._on_phase_change(PhaseChanged(t=own.t + 2, previous="LANDING", phase="DEPARTURE", reason="go-around"), own)
    out = [o for dt in range(3, 10) for o in engine.handle(msgspec.structs.replace(own, t=own.t + dt, com1_mhz=tower.mhz))
           if isinstance(o, AtcTransmission)]
    assert [o.instruction_id for o in out] == ["tower.go_around"]


def test_return_to_the_departure_airport():
    lines = happy_path_with(PilotRule(when=When(phase="CRUISE", after_s=40),
                                      say="Seattle Center, 2LT, we'd like to return to Paine Field"),
                            PilotRule(on="common.return", say="{readback}"))
    back = next(line for line in atc(lines) if "cleared direct" in line)
    assert "Paine Field airport" in back and "expect ILS RWY 34L approach" in back and "Paine altimeter" in back


def cruising_engine() -> tuple[AtcEngine, OwnshipState]:
    # Put down anywhere along the flight (radio range would decide who can hear it, and that isn't what's tested).
    engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, callsign="N172LT", seed=7, radio_range=False))
    for airport in load_airport_dir(FIXTURES / "airports"):
        engine.handle(AirportData(t=0.0, airport=airport))
    events = [e for e in Recording(FIXTURES / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState)]
    engine.handle(events[0])  # parked at Paine: the origin
    own = next(e for e in events if not e.on_ground and e.alt_indicated_ft > 4900 and e.t > 900)
    engine.state.phase = "CRUISE"
    engine.state.assignments.altitude_ft = 5000
    # Twenty miles from Boeing Field at 5,000 ft is past the top of descent, so centre would clear the
    # descent before anything else. These tests are about what comes after; that part is done.
    engine.state.flags.add("descend")
    return engine, msgspec.structs.replace(own, com1_mhz=125.1)  # Seattle Center


def transmissions(engine: AtcEngine, events) -> list[AtcTransmission]:
    return [o for e in events for o in engine.handle(e) if isinstance(o, AtcTransmission)]


def test_traffic_advisory_for_converging_traffic():
    engine, own = cruising_engine()
    engine.handle(own)
    heading = math.radians(own.hdg_true)

    def ahead(nm: float) -> TrafficTarget:  # a 737 straight ahead, coming the other way, 500 ft above
        return TrafficTarget(object_id=7, atc_model="ATCCOM.AC_MODEL B738.0.text", lat=own.lat + nm / 60 * math.cos(heading),
                             lon=own.lon + nm / 60 * math.sin(heading) / math.cos(math.radians(own.lat)),
                             alt_ft=own.alt_msl_ft + 500, hdg_true=(own.hdg_true + 180) % 360, gs_kt=250, on_ground=False)

    t = own.t + 20
    out = transmissions(engine, [TrafficSnapshot(t=t, targets=(ahead(4.0),)), msgspec.structs.replace(own, t=t),
                                 TrafficSnapshot(t=t + 3, targets=(ahead(3.6),)), msgspec.structs.replace(own, t=t + 3)])
    call = next(o for o in out if o.instruction_id == "common.traffic")
    assert "12 o'clock, 4 miles, opposite direction" in call.text and "B738" in call.text
    # Not again for the same airplane a few seconds later.
    again = transmissions(engine, [TrafficSnapshot(t=t + 30, targets=(ahead(2.0),)), msgspec.structs.replace(own, t=t + 30)])
    assert not [o for o in again if o.instruction_id == "common.traffic"]


def test_altitude_check_after_drifting_off_the_assigned_altitude():
    engine, own = cruising_engine()
    level = [msgspec.structs.replace(own, t=own.t + i, alt_indicated_ft=5000.0) for i in range(3)]
    drifting = [msgspec.structs.replace(own, t=own.t + 20 + i, alt_indicated_ft=5450.0, alt_msl_ft=5450.0) for i in range(20)]
    out = transmissions(engine, level + drifting)
    checks = [o.text for o in out if o.instruction_id == "common.check_altitude"]
    assert len(checks) == 1 and checks[0].endswith("check altitude, maintain 5,000.")


def test_quiet_after_switching_the_new_controller_calls_first():
    scenario = load_scenario(HAPPY)
    scenario.pilot = [r for r in scenario.pilot if "climbing 5,000" not in r.say]
    scenario.pilot.append(PilotRule(on="tower.handoff_departure", tune="handoff", delay_s=8, say=""))
    lines = run(scenario, HAPPY.parent).lines
    radar = next(line for line in atc(lines) if "radar contact" in line)
    assert "Seattle Departure" in radar


def test_an_empty_transcript_is_not_a_check_in():
    engine, own = cruising_engine()
    engine.handle(Transcript(t=own.t + 1, text=""))
    assert engine.state.comms.last_pilot_t is None


def test_a_level_offered_enroute_becomes_an_instruction_when_taken():
    """ "advise if you can accept FL370" is an offer, not a clearance: nothing is outstanding until the
    pilot takes it, and then it is assigned and read back like any other altitude."""
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation

    engine = AtcEngine(EngineConfig(callsign="DAL42", seed=3))
    centre = Facility(controller="center", station="Vancouver Center", mhz=133.5)
    engine._offered_level = (100.0, 37000)
    engine._on_request(Interpretation(kind="request", intent="acknowledge",
                                      text="affirmative, we can accept FL370"), centre, 130.0)
    assert [item.instruction_id for item in engine._scheduled] == ["common.climb"]
    assert engine._scheduled[0].slots["altitude"] == 37000
    assert engine._offered_level is None


def test_an_offer_not_taken_expires():
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.atc_core.facilities import Facility

    engine = AtcEngine(EngineConfig(callsign="DAL42", seed=3))
    centre = Facility(controller="center", station="Vancouver Center", mhz=133.5)
    engine._offered_level = (100.0, 37000)
    assert not engine._accepts_higher(100_000.0, centre)


def test_a_level_offered_enroute_can_be_turned_down():
    """ "Negative, we'd like to stay at our cruising level" left the offer open and got "say again"."""
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.atc_core.facilities import Facility

    engine = AtcEngine(EngineConfig(callsign="ACA216", seed=3))
    centre = Facility(controller="center", station="Edmonton Center", mhz=124.525)
    engine._offered_level = (100.0, 37000)
    assert engine._answer_offer("Negative. We'd like to stay on our cruising.", centre, 130.0)
    assert [item.instruction_id for item in engine._scheduled] == ["common.roger"]
    assert engine._offered_level is None


from test_phase import (
    own,  # noqa: E402  (a builder for OwnshipState, shared with the phase tests)
)


def test_an_emergency_gets_priority_and_the_shortest_way_down():
    """Declaring an emergency used to get an acknowledgement and nothing else: the flight carried on
    being sequenced, chased for readbacks and asked to check its altitude like any other."""
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation

    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KBFI", seed=3))
    engine.state.flight.destination = "KBFI"
    engine.state.aircraft = own(100.0, on_ground=False, alt_agl_ft=8000, alt_msl_ft=8000, alt_indicated_ft=8000)
    centre = Facility(controller="center", station="Seattle Center", mhz=125.1)
    engine._emergency(Interpretation(kind="request", intent="emergency", text="mayday, engine failure",
                                     values={"emergency": "engine failure"}), centre, 100.0)
    said = [item.instruction_id for item in engine._scheduled]
    assert "common.emergency_priority" in said, said
    assert "emergency" in engine.state.flags


def test_an_emergency_stops_the_altitude_nagging():
    from localtc.atc_core.engine import AtcEngine, EngineConfig

    engine = AtcEngine(EngineConfig(callsign="N172LT", seed=3))
    engine.state.phase = "CRUISE"
    engine.state.assignments.altitude_ft = 5000
    engine.state.flags.add("reached:5000")
    drifting = own(200.0, on_ground=False, alt_agl_ft=6000, alt_msl_ft=6000, alt_indicated_ft=6000)
    assert engine._altitude_check(drifting) is False or engine._deviation_since is not None
    engine.state.flags.add("emergency")
    assert engine._altitude_check(drifting) is False
