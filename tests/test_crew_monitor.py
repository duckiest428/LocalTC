"""The copilot speaking first (crew.monitor): callouts from the sim, relays from ATC, reminders for what the captain
missed, its own side of the cockpit done by itself, never over ATC, as much as the verbosity asks for."""

from types import SimpleNamespace

import pytest

from localtc.atc_core.readback.interpreter import PendingReadback
from localtc.atc_core.session import Assignments, Clearance, CommsState
from localtc.config import PlanPerf
from localtc.crew.monitor import spoken
from localtc.crew.pm import PilotMonitoring
from localtc.crew.profiles import load_all
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AtcTransmission,
    CrewSpeech,
    IntercomHeard,
    OwnshipState,
    PhaseChanged,
    SendSimEvent,
)

PROFILES = load_all()


def own(t: float, **overrides) -> OwnshipState:
    fields = dict(t=t, lat=47.0, lon=-122.0, alt_msl_ft=400.0, alt_indicated_ft=400.0, alt_agl_ft=0.0,
                  altimeter_inhg=29.92, hdg_mag=90.0, hdg_true=90.0, ias_kt=0.0, gs_kt=0.0, vs_fpm=0.0,
                  on_ground=True, squawk="4553", xpdr_mode="alt", com1_mhz=124.35, com2_mhz=121.5, flaps_index=1,
                  gear_down=True, engine_running=True)
    fields.update(overrides)
    return OwnshipState(**fields)


def systems(t: float, **overrides) -> AircraftSystems:
    fields = dict(t=t, flaps_positions=4, gear_pct=100.0, engines_running=2, light_beacon=True, light_nav=True)
    fields.update(overrides)
    return AircraftSystems(**fields)


class FakeEngine:
    """What the copilot reads of ATC: the flight, the clearances, the assignments."""

    def __init__(self) -> None:
        self.state = SimpleNamespace(
            flight=SimpleNamespace(callsign=None, origin="KSEA", destination="KPDX", cruise_ft=35000, rules="IFR",
                                   aircraft_type="A20N"),
            assignments=Assignments(squawk="4553"), clearances={}, comms=CommsState(), pending=None, issued={},
            phase="PARKED")
        self.cfg = SimpleNamespace(sid="")
        self.facilities = []
        self.route = SimpleNamespace(descent_distance_nm=lambda *a: 100.0)

    def geometry(self, icao):
        return None

    def current_atis(self, icao, kind=None):
        return None

    def clear(self, kind: str) -> None:
        self.state.clearances[kind] = Clearance(kind=kind, instruction_id=kind, controller="tower", issued_t=0.0)


def crew(engine=None, **kw) -> PilotMonitoring:
    pm = PilotMonitoring(engine or FakeEngine(), profiles=PROFILES, **kw)
    pm.observe(AircraftIdentity(t=0.0, title="Airbus A320neo", atc_model="A20N"))
    return pm


def said(outputs) -> list[str]:
    return [o.text for o in outputs if isinstance(o, CrewSpeech)]


def fly(pm: PilotMonitoring, states) -> list:
    out = []
    for ev in states:
        out += pm.observe(ev)
    return out


def test_the_takeoff_callouts_come_from_the_plan_and_the_aircraft():
    pm = crew(perf=PlanPerf(v1=140, vr=145, v2=150), plan_source="simbrief")
    pm.observe(PhaseChanged(t=0.5, previous="RUNWAY_HOLD", phase="TAKEOFF"))
    pm.observe(systems(0.6))
    out = fly(pm, [own(1 + i, on_runway=True, ias_kt=ias, gs_kt=ias) for i, ias in enumerate(range(20, 160, 10))])
    words = said(out)
    assert words.index("One hundred knots.") < words.index("V1.") < words.index("Rotate.")  # Airbus: 100 knots
    assert "Eighty knots." not in words


def test_positive_rate_then_the_copilot_raises_the_gear_itself():
    pm = crew()
    pm.observe(PhaseChanged(t=0.5, previous="TAKEOFF", phase="DEPARTURE"))
    pm.observe(systems(0.6))
    out = fly(pm, [own(1, on_ground=True, ias_kt=150, gs_kt=150)]
              + [own(2 + i, on_ground=False, alt_agl_ft=40 + 60 * i, alt_indicated_ft=440 + 60 * i, vs_fpm=1800,
                     ias_kt=155, gs_kt=155) for i in range(14)])
    assert "Positive rate." in said(out)
    assert any(isinstance(o, SendSimEvent) and o.name == "GEAR_UP" for o in out)
    assert "Gear up." in said(out)


def test_the_pilots_own_gear_call_comes_first():
    pm = crew()
    pm.observe(PhaseChanged(t=0.5, previous="TAKEOFF", phase="DEPARTURE"))
    pm.observe(systems(0.6))
    fly(pm, [own(1, ias_kt=150, gs_kt=150), own(2, on_ground=False, alt_agl_ft=50, vs_fpm=1800, ias_kt=155)])
    pm.observe(IntercomHeard(t=2.5, text="gear up"))
    out = fly(pm, [own(3 + i, on_ground=False, alt_agl_ft=100 + 60 * i, vs_fpm=1800, ias_kt=155) for i in range(6)])
    assert [o for o in out if isinstance(o, SendSimEvent) and o.name == "GEAR_UP"] == []  # done once, by the pilot's call


def test_with_calls_only_the_copilot_touches_nothing():
    pm = crew(hands="calls")
    pm.observe(PhaseChanged(t=0.5, previous="TAKEOFF", phase="DEPARTURE"))
    pm.observe(systems(0.6))
    out = fly(pm, [own(1, ias_kt=150, gs_kt=150)]
              + [own(2 + i, on_ground=False, alt_agl_ft=40 + 80 * i, vs_fpm=1800, ias_kt=155) for i in range(10)])
    assert not [o for o in out if isinstance(o, SendSimEvent)]
    assert "Gear's still down." in said(out)


def test_never_over_atc_but_safety_cuts_in():
    pm = crew()
    pm.observe(systems(0.1))
    pm.observe(own(0.2))
    pm.observe(AtcTransmission(t=1.0, station="Seattle Tower", frequency_mhz=119.9, controller="tower",
                               text="Alaska 123, wind 160 at 8, runway 16L, cleared for takeoff, and a long tail of words."))
    pm.monitor._call("routine", 2, 1.0, "Something routine.")
    pm.monitor._call("safety", 3, 1.0, "Config!")
    assert said(pm.observe(own(1.5))) == ["Config!"]
    assert said(pm.observe(own(2.5))) == []  # ATC is still talking
    assert "Something routine." in said(fly(pm, [own(4 + i) for i in range(12)]))


@pytest.mark.parametrize("verbosity, heard", [("quiet", 0), ("standard", 1), ("chatty", 1)])
def test_verbosity(verbosity, heard):
    engine = FakeEngine()
    engine.clear("landing")
    pm = crew(engine, verbosity=verbosity)
    pm.observe(PhaseChanged(t=0.5, previous="ARRIVAL", phase="APPROACH"))
    pm.observe(systems(0.6, flaps_positions=4))
    stable = dict(on_ground=False, gear_down=True, flaps_index=4, vs_fpm=-750, ias_kt=140)
    out = fly(pm, [own(1 + i, alt_agl_ft=1100 - 20 * i, **stable) for i in range(12)])
    assert len([w for w in said(out) if w in ("One thousand, stable.", "Thousand, stable.")]) == heard


def test_not_configured_at_a_thousand_and_not_cleared_at_five_hundred():
    pm = crew(verbosity="quiet")  # safety calls come whatever the verbosity
    pm.observe(PhaseChanged(t=0.5, previous="ARRIVAL", phase="APPROACH"))
    pm.observe(systems(0.6, flaps_positions=4))
    descent = dict(on_ground=False, vs_fpm=-800, ias_kt=140)
    out = fly(pm, [own(1 + i, alt_agl_ft=1100 - 50 * i, gear_down=False, flaps_index=2, **descent) for i in range(14)])
    words = said(out)
    assert "Gear! We're not down!" in words
    assert any(w.startswith("One thousand, not stable: gear, flaps") for w in words)
    assert "We're not cleared to land!" in words


@pytest.fixture
def checklists_on(monkeypatch):
    """The checklists are off until there are real ones (monitor.CHECKLISTS); their machinery is still tested."""
    from localtc.crew import monitor

    monkeypatch.setattr(monitor, "CHECKLISTS", True)


def test_no_checklists_until_there_are_real_ones():
    pm = crew()
    pm.observe(PhaseChanged(t=0.5, previous="TAXI_OUT", phase="RUNWAY_HOLD"))
    pm.observe(systems(0.6))
    out = fly(pm, [own(1 + i) for i in range(40)])
    assert not [w for w in said(out) if "checklist" in w.lower()]  # none offered
    words = said(pm.observe(IntercomHeard(t=50, text="before takeoff checklist")))
    assert words == ["No checklists from me yet; I'll read them once we have the real ones for this aircraft."]


def test_the_before_takeoff_checklist_is_challenge_and_response(checklists_on):
    """The captain's items are asked and answered; a wrong answer holds the checklist till the aircraft shows it
    right; the copilot's own items it answers (and sets) itself."""
    pm = crew(perf=PlanPerf(takeoff_flaps="1+F"))
    pm.observe(PhaseChanged(t=0.5, previous="TAXI_OUT", phase="RUNWAY_HOLD"))
    pm.observe(systems(0.6, light_strobe=False, light_landing=False))
    pm.observe(own(1, flaps_index=0))
    out = pm.observe(IntercomHeard(t=2, text="before takeoff checklist"))
    assert " ".join(said(out)) == "Before takeoff checklist. Flaps?"
    assert not [o for o in out if isinstance(o, SendSimEvent) and o.name == "FLAPS_1"]  # the captain's side
    out = pm.observe(IntercomHeard(t=3, text="one plus F, set"))  # said, but not so yet
    assert said(out) == ["Flaps shows up, set them for takeoff, plan says 1 plus F. Holding the checklist till it's right."]
    out, lit = [], False
    for i in range(8):
        out += pm.observe(own(4 + i, flaps_index=1))
        out += pm.observe(systems(4.5 + i, light_strobe=lit, light_landing=lit))
    text = " ".join(said(out))
    assert text.startswith("Flaps, 1 plus F. Transponder?")
    out = pm.observe(IntercomHeard(t=13, text="TA RA"))
    lit = any(isinstance(o, SendSimEvent) and o.name == "STROBES_SET" for o in out)
    text = " ".join(said(out))
    assert text == "Strobes, on, set. Landing lights, on, set. Before takeoff checklist complete."
    assert lit and {o.name for o in out if isinstance(o, SendSimEvent)} >= {"STROBES_SET", "LANDING_LIGHTS_SET"}


def test_an_unanswered_item_is_asked_once_more(checklists_on):
    pm = crew()
    pm.observe(PhaseChanged(t=0.5, previous="PARKED", phase="PARKED"))
    pm.observe(systems(0.6))
    pm.observe(own(1, parking_brake=True))
    assert said(pm.observe(IntercomHeard(t=2, text="before start checklist")))[0].startswith("Before start checklist. Parking brake?")
    words = said(fly(pm, [own(3 + i, parking_brake=True) for i in range(30)]))
    assert words.count("Parking brake?") == 1


def test_a_handoff_leaves_the_pilots_radio_alone_and_reminds_only_when_late():
    engine = FakeEngine()
    pm = crew(engine)
    pm.observe(systems(0.1))
    pm.observe(own(0.2, on_ground=False, alt_indicated_ft=8000, alt_agl_ft=7500, ias_kt=250, gs_kt=250, flaps_index=0,
                   gear_down=False))
    engine.state.comms.expected = SimpleNamespace(station="Seattle Center", mhz=128.5, controller="center")
    pm.observe(AtcTransmission(t=1.0, station="Seattle Departure", frequency_mhz=119.2, controller="departure",
                               instruction_id="departure.handoff_center", text="Alaska 123, contact Seattle Center 128.5."))
    out = fly(pm, [own(2 + i, on_ground=False, alt_indicated_ft=8000, alt_agl_ft=7500, ias_kt=250, gs_kt=250,
                       flaps_index=0, gear_down=False) for i in range(8)])
    assert not [o for o in out if isinstance(o, SendSimEvent)] and not said(out)  # the radios are the pilot's
    out = fly(pm, [own(70 + i, on_ground=False, alt_indicated_ft=8000, alt_agl_ft=7500, ias_kt=250, gs_kt=250,
                       flaps_index=0, gear_down=False) for i in range(10)])
    assert "We should be with Seattle Center on 128.5 by now." in said(out)


def test_the_parking_brake_is_the_captains_the_copilot_only_says():
    engine = FakeEngine()
    pm = crew(engine)
    pm.monitor.f.landing_t = 10.0
    pm.observe(PhaseChanged(t=11, previous="LANDING", phase="TAXI_IN"))
    pm.observe(systems(12, engines_running=2, light_beacon=True))
    pm.observe(own(13, parking_brake=False))
    out = pm.observe(systems(20, engines_running=0, light_beacon=True))
    out += fly(pm, [own(21 + i, parking_brake=False) for i in range(30)])
    assert "Parking brake?" in said(out)
    assert not [o for o in out if isinstance(o, SendSimEvent) and o.name == "PARKING_BRAKE_SET"]
    assert any(isinstance(o, SendSimEvent) and o.name == "BEACON_LIGHTS_SET" for o in out)  # the copilot's side


def test_each_call_once():
    pm = crew()
    cruise = dict(on_ground=False, alt_indicated_ft=35000, alt_agl_ft=34000, ias_kt=270, gs_kt=450, flaps_index=0, gear_down=False)
    out = fly(pm, [PhaseChanged(t=0.5, previous="DEPARTURE", phase="CRUISE"), own(1, **cruise),
                   PhaseChanged(t=5.5, previous="DEPARTURE", phase="CRUISE")]
              + [own(6 + i, **cruise) for i in range(20)])
    assert said(out).count("Level at FL350.") + said(out).count("Top of climb, level FL350.") == 1


def test_the_fuel_is_checked_against_the_plan_once_the_engines_start():
    """Not in the greeting (an add-on's tanks are filled after the flight loads: 2,000 kilos "on board" at the
    gate), and only when it's off."""
    engine = FakeEngine()
    pm = crew(engine, perf=PlanPerf(block_fuel_lb=20000), plan_source="simbrief")
    pm.observe(systems(0.5, engines_running=0))
    out = fly(pm, [own(1 + i, fuel_lb=4500, engine_running=False) for i in range(26)])
    greeting = said(out)[0]
    assert "KSEA to KPDX" in greeting and "pounds" not in greeting and "fuel" not in greeting.lower()
    pm.observe(own(29, fuel_lb=17000))  # fuelled at the gate
    out = pm.observe(systems(30, engines_running=1))
    out += fly(pm, [own(31 + i, fuel_lb=17000) for i in range(30)])
    assert "Fuel's 17,000 pounds, 3,000 pounds under the plan's block." in said(out)
    pm2 = crew(FakeEngine(), perf=PlanPerf(block_fuel_lb=20000), plan_source="simbrief")
    pm2.observe(systems(0.5, engines_running=0))
    pm2.observe(own(1, fuel_lb=19800, engine_running=False))
    out = pm2.observe(systems(2, engines_running=1)) + fly(pm2, [own(3 + i, fuel_lb=19800) for i in range(40)])
    assert not [w for w in said(out) if "Fuel" in w]  # it matches: nothing said


def test_a_readback_left_waiting_is_atcs_business():
    engine = FakeEngine()
    pm = crew(engine)
    pm.observe(own(0.1, on_ground=False, alt_indicated_ft=12000, alt_agl_ft=11000))
    engine.state.pending = PendingReadback(instruction_id="common.climb", controller="center", expected={}, required=(),
                                           issued_t=1.0)
    pm.observe(AtcTransmission(t=1.0, station="Seattle Center", frequency_mhz=128.5, controller="center",
                               instruction_id="common.climb", text="Alaska 123, climb and maintain FL240."))
    out = fly(pm, [own(2 + i, on_ground=False, alt_indicated_ft=12000, alt_agl_ft=11000) for i in range(40)])
    assert not [w for w in said(out) if "readback" in w]  # ATC asks for it itself; the copilot doesn't nag


def test_briefing_and_status_on_request():
    engine = FakeEngine()
    engine.state.assignments = Assignments(squawk="4553", altitude_ft=5000, departure_runway="16L")
    pm = crew(engine, perf=PlanPerf(v1=140, vr=145, v2=150, takeoff_flaps="1+F"))
    pm.observe(own(1))
    text = " ".join(said(pm.observe(IntercomHeard(t=2, text="brief the departure"))))
    assert text.startswith("Departure briefing. KSEA runway 16L, initial 5,000, squawk 4553.")
    assert "V1 140, rotate 145, V2 150." in text and "Flaps 1 plus F." in text
    assert said(pm.observe(IntercomHeard(t=3, text="quiet please"))) == ["Copy, only what matters."]
    assert pm.monitor.verbosity == "quiet"


def test_the_words_as_said():
    assert spoken("Level at FL350.") == "Level at flight level three five zero."
    assert spoken("Squawk 4157 set.") == "Squawk four one five seven set."
    assert spoken("Seattle Center, 128.5, in standby.") == "Seattle Center, one two eight point five, in standby."
    assert spoken("Transition level, QNH 1013 set.") == "Transition level, Q N H one zero one three set."
    assert spoken("Departing KSEA.", ("KSEA",)) == "Departing kilo sierra echo alpha."
    assert spoken("Gear up.") == ""  # nothing to say differently


def test_a_whole_recorded_flight_with_the_copilot_listening():
    """San Diego to Phoenix as flown, the copilot on the radio too: what it says by itself, and never a nag."""
    from pathlib import Path

    from localtc.config import load_config, with_recorded
    from localtc.replay import Recording
    from localtc.scenario import Scenario, ScenarioMeta, run

    flight = Path(__file__).parent / "fixtures" / "real_ksan_kphx"
    cfg = with_recorded(load_config(), Recording(flight).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(flight), copilot="full"), flight=cfg.flight, atc=cfg.atc)

    class Crew:  # made with the engine the scenario builds
        pm = None

        def observe(self, ev):
            return self.pm.observe(ev)

    listening = Crew()
    import localtc.scenario as scenarios

    real = scenarios.AtcEngine

    def engine(*a, **kw):
        made = real(*a, **kw)
        listening.pm = PilotMonitoring(made, profiles=PROFILES, radio_mode=lambda: "full")
        return made

    scenarios.AtcEngine = engine
    try:
        result = run(scenario, flight, recording=flight, crew=listening)
    finally:
        scenarios.AtcEngine = real
    words = [o.text for o in result.outputs if isinstance(o, CrewSpeech)]
    # Replayed, nothing the copilot sends ever shows in the recording: after two, it stops reaching (said once).
    assert words.count("My switches aren't reaching this aircraft. I'll leave them to you and call.") == 1
    for expected in ("Positive rate.", "Ten thousand. Landing lights off?", "Level at FL350.", "Seventy knots."):
        assert expected in words or expected.replace("Level at", "Top of climb, level") in words, expected
    greeting = next(w for w in words if w.startswith(("Hi,", "Hey.", "Morning.")))  # once settled, not at once
    assert "San Diego" in greeting
    assert max(words.count(w) for w in set(words)) <= 2  # nothing said over and over
