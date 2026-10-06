"""What else the copilot watches for (crew.watches): turbulence, wind shear, ice, the weather ahead, step climbs, the
arrival's restrictions, the field in sight, the autobrake and reversers, the taxi route, a diversion, ATC's instruction
repeated. And the arrivals as the sim sends them (sim_bridge.arrivals)."""

import struct
from types import SimpleNamespace

from test_crew_monitor import FakeEngine, crew, fly, own, said, systems

from localtc.atc_core.diversion import Diversion
from localtc.atc_core.readback.interpreter import PendingReadback
from localtc.crew.watches import Turbulence, WindShear, _instruction
from localtc.sim_api import (
    ArrivalData,
    ArrivalLeg,
    AtcAlert,
    AtcTransmission,
    IntercomHeard,
    PhaseChanged,
    RequestArrival,
    Transcript,
)
from localtc.sim_bridge.arrivals import LEG, T_AIRPORT, T_APPROACH_LEG, T_ARRIVAL, T_ENROUTE_TRANSITION, ArrivalAssembler
from localtc.sim_bridge.protocol import FacilityData


def cruise(pm, t=0.5, phase="CRUISE"):
    pm.observe(PhaseChanged(t=t, previous="DEPARTURE", phase=phase))


def air(t, **kw):
    fields = dict(on_ground=False, alt_agl_ft=30000, alt_indicated_ft=35000, alt_msl_ft=35000, ias_kt=270, gs_kt=450,
                  flaps_index=0, gear_down=False)
    fields.update(kw)
    return own(t, **fields)


# --- turbulence, wind shear, ice --------------------------------------------------------------------------------------

def test_turbulence_comes_in_at_once_and_goes_only_after_a_calm_minute():
    turb = Turbulence()
    levels = [turb.add(t, 1.0) for t in range(10)]
    assert set(levels) == {None}
    bumps = [turb.add(10 + i, 1.0 + (0.4 if i % 2 else -0.4)) for i in range(4)]
    assert "moderate" in bumps or "severe" in bumps
    calm = [turb.add(14 + i, 1.0) for i in range(80)]
    assert calm.index("smooth") >= 55  # not the moment it's calm


def test_a_steady_turn_is_not_turbulence():
    turb = Turbulence()
    assert {turb.add(t, 1.15) for t in range(40)} == {None}


def test_moderate_turbulence_is_said_once_and_smooth_again_later():
    pm = crew()
    cruise(pm)
    out = []
    for i in range(40):
        g = 1.0 + (0.25 if i % 2 else -0.25) if 5 <= i < 15 else 1.0
        out += pm.observe(systems(i + 0.1, g_force=g))
        out += pm.observe(air(i + 0.2))
    for i in range(40, 140):
        out += pm.observe(air(i + 0.2))
    words = said(out)
    assert sum("turbulence" in w for w in words) == 1
    assert any(w in ("Smooth again.", "That's smoothed out.") for w in words)


def test_wind_shear_on_approach_but_not_thrust():
    shear = WindShear()
    hits = [shear.add(t, ias, 140, -700, 800, True, False) for t, ias in enumerate([150, 150, 148, 140, 132])]
    assert hits[-1] is True
    steady = WindShear()  # the thrust: airspeed and groundspeed up together
    assert not any(steady.add(t, 150 + 5 * t, 140 + 5 * t, -700, 800, True, False) for t in range(6))
    high = WindShear()  # well above 1,500 feet: not wind shear territory
    assert not any(high.add(t, ias, 140, -700, 5000, True, False) for t, ias in enumerate([150, 150, 140, 130]))


def test_picking_up_ice():
    pm = crew()
    cruise(pm)
    out = fly(pm, [systems(1.0, ice_pct=12.0)] + [air(1.1 + 2 * i) for i in range(5)])
    assert "Picking up ice. Anti-ice on?" in said(out)


# --- step climbs -------------------------------------------------------------------------------------------------------

def planned_steps(engine):
    fix = lambda ident, lat, alt: SimpleNamespace(ident=ident, lat=lat, lon=-122.0, alt_ft=alt, stage="CRZ")  # noqa: E731
    engine.route = SimpleNamespace(descent_distance_nm=lambda *a: 100.0,
                                   fixes=(fix("AAA", 46.0, 35000), fix("BBB", 47.1, 37000), fix("CCC", 49.0, 37000)))
    engine.state.comms.tuned = SimpleNamespace(station="Seattle Center", mhz=125.1)
    engine.state.flight.callsign = "ASA123"
    return engine


def test_a_step_climb_from_the_plan_is_offered_and_asked_for_on_yes():
    engine = planned_steps(FakeEngine())
    pm = crew(engine, radio_mode=lambda: "assist")
    cruise(pm)
    out = fly(pm, [air(1.0 + 2 * i, lat=46.9 + 0.001 * i) for i in range(5)])
    assert "Step climb point ahead, suggest FL370. Want me to ask?" in said(out)
    reply = pm.observe(IntercomHeard(t=11.0, text="yes"))
    assert said(reply) and said(reply)[0].startswith("Your radios: ask Seattle Center")


def test_working_the_radio_the_copilot_asks_atc_itself():
    engine = planned_steps(FakeEngine())
    pm = crew(engine, radio_mode=lambda: "full")
    cruise(pm)
    out = fly(pm, [air(1.0, lat=46.9), air(5.0, lat=46.91)])
    radio = [o.text for o in out if isinstance(o, Transcript) and o.source == "copilot"]
    assert radio == ["Seattle Center, " + radio[0].split(", ")[1] + ", request climb FL370"]
    assert "Step climb point ahead, asking for FL370." in said(out)


def test_no_step_climb_once_up_there_or_after_unable():
    engine = planned_steps(FakeEngine())
    pm = crew(engine)
    cruise(pm)
    assert not any("Step" in w for w in said(fly(pm, [air(1.0, lat=46.9, alt_indicated_ft=37000), air(2.0, lat=46.91, alt_indicated_ft=37000)])))
    pm2 = crew(planned_steps(FakeEngine()))
    cruise(pm2)
    pm2.observe(AtcTransmission(t=0.8, station="Seattle Center", frequency_mhz=125.1, text="Alaska 123, unable higher.",
                                instruction_id="center.unable_altitude"))
    assert not any("Step" in w for w in said(fly(pm2, [air(10.0, lat=46.9), air(11.0, lat=46.91)])))


def test_without_steps_in_the_plan_the_weight_burned_suggests_one():
    pm = crew()
    cruise(pm)
    pm.observe(systems(0.6))
    out = fly(pm, [air(1.0, gross_weight_lb=150000), air(1.5, gross_weight_lb=150000)]
              + [air(2.0 + 2 * i, gross_weight_lb=141000) for i in range(4)])
    assert any(w.startswith("We're light enough to go higher, suggest FL370") for w in said(out))


# --- the arrival's restrictions ----------------------------------------------------------------------------------------

def arriving(engine):
    engine.cfg.star = "CEPIN2"
    engine._via_floor = 6000
    engine.state.flight.destination = "KPDX"
    return engine


def test_the_arrival_is_asked_for_within_range():
    engine = arriving(FakeEngine())
    engine.geometry = lambda icao: SimpleNamespace(airport=SimpleNamespace(lat=47.5, lon=-122.0, elev_ft=30))
    pm = crew(engine)
    cruise(pm)
    out = fly(pm, [air(1.0), air(2.0)])
    assert RequestArrival(icao="KPDX", name="CEPIN2") in out


def test_a_missed_restriction_is_said_under_descend_via_only():
    def run(heading):
        engine = arriving(FakeEngine())
        engine.state.assignments.heading = heading
        pm = crew(engine)
        cruise(pm, phase="ARRIVAL")
        pm.observe(ArrivalData(t=0.6, airport="KPDX", name="CEPIN2", legs=(
            ArrivalLeg(fix="CEPIN", lat=47.0, lon=-122.0, altitude="below", alt1_ft=12000),
            ArrivalLeg(fix="OTHER", lat=40.0, lon=-120.0, altitude="at", alt1_ft=9000, transition="FAR"))))
        assert [r.fix for r in pm.monitor.restrictions] == ["CEPIN"]  # a transition not on the route isn't flown
        return said(fly(pm, [air(1 + i, lat=46.95 + 0.01 * i, alt_indicated_ft=15000, vs_fpm=-500) for i in range(12)]))

    assert "Missed the at or below 12,000 at CEPIN." in run(None)
    assert not any("Missed" in w for w in run(270))  # vectors cancel it


def test_a_heads_up_when_too_high_for_the_next_one():
    engine = arriving(FakeEngine())
    pm = crew(engine)
    cruise(pm, phase="ARRIVAL")
    pm.observe(ArrivalData(t=0.6, airport="KPDX", name="CEPIN2", legs=(
        ArrivalLeg(fix="CEPIN", lat=47.2, lon=-122.0, altitude="at", alt1_ft=10000),)))
    words = said(fly(pm, [air(1.0, lat=47.0, alt_indicated_ft=24000), air(2.0, lat=47.001, alt_indicated_ft=24000)]))
    assert "We're high for the 10,000 at CEPIN." in words


# --- field in sight, minimums --------------------------------------------------------------------------------------------

def test_field_in_sight_on_the_way_in_to_a_visual():
    engine = FakeEngine()
    engine.state.assignments.approach = "VISUAL RWY 28"
    engine.state.comms.tuned = SimpleNamespace(station="Portland Approach", mhz=124.35)
    engine.state.flight.callsign = "ASA123"
    engine.geometry = lambda icao: SimpleNamespace(airport=SimpleNamespace(lat=47.05, lon=-122.0, elev_ft=30))
    pm = crew(engine, radio_mode=lambda: "full")
    cruise(pm, phase="APPROACH")
    words = said(fly(pm, [air(1.0, alt_agl_ft=3000, alt_indicated_ft=3000, ias_kt=200, gs_kt=200, visibility_m=20000),
                          air(2.0, alt_agl_ft=2950, alt_indicated_ft=2950, ias_kt=200, gs_kt=200, visibility_m=20000)]))
    assert any(w in ("Field in sight?", "Do you have the field?") for w in words)
    reply = pm.observe(IntercomHeard(t=3.0, text="yes"))
    assert [o.text for o in reply if isinstance(o, Transcript)][0].endswith("field in sight")


def test_no_field_in_sight_in_cloud():
    engine = FakeEngine()
    engine.state.assignments.approach = "VISUAL RWY 28"
    engine.geometry = lambda icao: SimpleNamespace(airport=SimpleNamespace(lat=47.05, lon=-122.0, elev_ft=30))
    pm = crew(engine)
    cruise(pm, phase="APPROACH")
    words = said(fly(pm, [air(1.0, alt_agl_ft=3000, in_cloud=True), air(2.0, alt_agl_ft=2950, in_cloud=True)]))
    assert not any("field" in w.lower() for w in words)


# --- the autobrake and the reversers -------------------------------------------------------------------------------------

def test_autobrake_confirmed_before_landing_and_the_reversers_after():
    pm = crew()
    cruise(pm, phase="APPROACH")
    pm.observe(systems(0.6, autobrake=2, jet=True))
    words = said(fly(pm, [air(1.0 + 2 * i, alt_agl_ft=2600 - 150 * i, alt_indicated_ft=2600 - 150 * i, ias_kt=160, gs_kt=160,
                              vs_fpm=-700, flaps_index=3, gear_down=True) for i in range(10)]))
    assert "Autobrake set to medium." in words
    pm.observe(PhaseChanged(t=3.0, previous="APPROACH", phase="LANDING"))
    pm.monitor.f.said.update({"no_landing_clearance:1": 0, "1000:1": 0, "greeting": 0})
    pm.monitor.f.takeoff_t = 0.0
    landed = dict(flaps_index=3, gear_down=True)
    out = fly(pm, [air(4.0, alt_agl_ft=10, alt_indicated_ft=10, ias_kt=140, gs_kt=140, vs_fpm=-150, **landed),
                   own(5.0, ias_kt=135, gs_kt=135, **landed),
                   systems(7.5, autobrake=2, jet=True, reverser_pct=80, autobrake_active=True, spoilers_pct=80)]
              + [own(8.0 + i, ias_kt=120 - 2 * i, gs_kt=120 - 2 * i, **landed) for i in range(20)])
    words = said(out)
    assert "Reversers." in words and "Autobrake engaged." in words


def test_no_reversers_said_for_a_jet_that_didnt_deploy_them():
    pm = crew()
    cruise(pm, phase="LANDING")
    pm.observe(systems(0.6, jet=True, spoilers_pct=80))
    pm.monitor.f.said.update({"no_landing_clearance:0": 0, "1000:0": 0, "greeting": 0})
    pm.monitor.f.takeoff_t = 0.0
    words = said(fly(pm, [air(1.0, alt_agl_ft=10, alt_indicated_ft=10, ias_kt=140, gs_kt=140, gear_down=True, flaps_index=3)]
                     + [own(2.0 + i, ias_kt=135 - 3 * i, gs_kt=135 - 3 * i, flaps_index=3) for i in range(10)]))
    assert "No reversers." in words


# --- the taxi route ---------------------------------------------------------------------------------------------------

class TaxiGeo:
    """A flat airport: x east, y north in metres from (47, -122)."""

    def __init__(self) -> None:
        self.airport = SimpleNamespace(taxi_points=(), taxi_paths=(), lat=47.0, lon=-122.0, elev_ft=0)
        self.runways = ()

    def nearest_hold_short(self, lat, lon):
        return None

    def xy(self, lat, lon):
        return ((lon + 122.0) * 75900.0, (lat - 47.0) * 111320.0)


def test_off_the_taxi_route_is_said_once_joined():
    engine = FakeEngine()
    engine.state.flight.origin = "KSEA"
    engine.state.assignments.taxi_route = ("B",)
    engine._taxi_path = ("KSEA", [(0.0, 0.0), (0.0, 800.0)])
    engine.geometry = lambda icao: TaxiGeo()
    pm = crew(engine)
    pm.observe(PhaseChanged(t=0.5, previous="PUSHBACK", phase="TAXI_OUT"))
    on_route = [own(1 + i, lat=47.0 + i * 0.0002, gs_kt=12, hdg_true=0) for i in range(5)]
    off = [own(6 + i, lat=47.001 + i * 0.0002, lon=-122.0 + 80 / 75900, gs_kt=12, hdg_true=0) for i in range(10)]
    words = said(fly(pm, on_route + off))
    assert "We're off the taxi route, cleared via B." in words
    assert sum("taxi route" in w for w in words) == 1


def test_not_off_the_route_before_joining_it():
    engine = FakeEngine()
    engine.state.assignments.taxi_route = ("B",)
    engine._taxi_path = ("KSEA", [(0.0, 0.0), (0.0, 800.0)])
    engine.geometry = lambda icao: TaxiGeo()
    pm = crew(engine)
    pm.observe(PhaseChanged(t=0.5, previous="PUSHBACK", phase="TAXI_OUT"))
    off = [own(1 + i, lat=47.001, lon=-122.0 + 100 / 75900, gs_kt=8, hdg_true=0) for i in range(12)]  # on the apron
    assert not any("taxi" in w or "turn" in w for w in said(fly(pm, off)))


# --- a diversion ------------------------------------------------------------------------------------------------------

def test_an_emergency_brings_the_nearest_suitable_airport():
    engine = FakeEngine()
    engine.state.flight.destination = "KPDX"
    engine._diversion_options = lambda own: [
        Diversion(icao="KDEN", name="Denver", distance_nm=62.0, bearing="northeast", runway="16R", runway_ft=12000,
                  ils=True, towered=True, score=50.0),
        Diversion(icao="KCOS", name="Colorado Springs", distance_nm=80.0, bearing="south", runway="17L",
                  runway_ft=11000, ils=True, towered=True, score=70.0)]
    pm = crew(engine)
    cruise(pm)
    pm.observe(AtcAlert(t=0.7, kind="emergency", detail="engine fire"))
    words = said(fly(pm, [air(20.0 + i) for i in range(30)]))
    offer = next(w for w in words if w.startswith("Nearest suitable"))
    assert offer.startswith("Nearest suitable is Denver, 62 miles northeast, runway 16R, 12,000 feet, ILS")
    assert "Colorado Springs" in offer and offer.endswith("Want me to ask for it?")


def test_no_suitable_airport_is_said_plainly():
    engine = FakeEngine()
    engine._diversion_options = lambda own: []
    pm = crew(engine)
    cruise(pm)
    pm.observe(AtcAlert(t=0.7, kind="emergency", detail="medical"))
    words = said(fly(pm, [air(1.0 + 2 * i) for i in range(15)]))
    assert "No suitable airport within 150 miles that I know of." in words


# --- ATC's instruction repeated ---------------------------------------------------------------------------------------

def test_the_instruction_is_repeated_before_the_readback_in_chatty():
    assert _instruction("Japanair 5, descend and maintain 8,000.") == "Descend and maintain 8,000."
    assert _instruction("Alaska 123, contact Seattle Center 125.1, good day.") == "Contact Seattle Center 125.1."

    def run(pilot_first: bool, verbosity="chatty", repeat=True):
        engine = FakeEngine()
        pm = crew(engine, verbosity=verbosity, repeat_atc=repeat)
        cruise(pm)
        pm.observe(systems(0.6))
        pm.observe(air(0.7, alt_indicated_ft=15000))
        engine.state.pending = PendingReadback(instruction_id="center.descend", controller="center",
                                               expected={"altitude": 8000}, required=("altitude",))
        out = pm.observe(AtcTransmission(t=1.0, station="Seattle Center", frequency_mhz=125.1, text="Japanair 5, descend and maintain 8,000.",
                                         instruction_id="center.descend"))
        if pilot_first:
            out += pm.observe(Transcript(t=3.0, text="descend and maintain 8,000, Japanair 5", source="voice"))
        return said(out + fly(pm, [air(1.5 + 0.5 * i, alt_indicated_ft=15000) for i in range(10)]))

    assert "Descend and maintain 8,000." in run(False)
    assert "Descend and maintain 8,000." not in run(True)
    assert "Descend and maintain 8,000." not in run(False, verbosity="standard")
    assert "Descend and maintain 8,000." not in run(False, repeat=False)


# --- the arrivals from the sim -----------------------------------------------------------------------------------------

def msg(kind, unique, parent, payload):
    return FacilityData(request_id=100, unique_id=unique, parent_id=parent, type=kind, item_index=0, list_size=0,
                        payload=payload, payload_bool8=b"", item_index_bool8=0, list_size_bool8=0)


def test_the_named_arrival_and_its_restrictions_from_the_sims_replies():
    a = ArrivalAssembler("KPDX", "CEPIN2")
    leg = lambda fix, desc, alt1_m, alt2_m=0.0, speed=0.0: LEG.pack(1, fix.encode(), 45.5, -122.6, desc, alt1_m, alt2_m, speed)  # noqa: E731
    a.add(msg(T_AIRPORT, 1, 0, b"KPDX\0\0\0\0"))
    a.add(msg(T_ARRIVAL, 2, 1, b"OTHER1\0\0"))
    a.add(msg(T_APPROACH_LEG, 3, 2, leg("NOPE", 1, 3000.0)))
    a.add(msg(T_ARRIVAL, 4, 1, b"CEPIN2\0\0"))
    a.add(msg(T_APPROACH_LEG, 5, 4, leg("CEPIN", 3, 12000 / 3.28084, speed=250.0)))
    a.add(msg(T_APPROACH_LEG, 6, 4, leg("UBG", 4, 11000 / 3.28084, 9000 / 3.28084)))
    a.add(msg(T_ENROUTE_TRANSITION, 7, 4, b"HAWKZ\0\0\0"))
    a.add(msg(T_APPROACH_LEG, 8, 7, leg("HAWKZ", 2, 16000 / 3.28084)))
    data = a.build(1.0)
    assert [(l.fix, l.altitude, l.alt1_ft, l.alt2_ft, l.speed_kt, l.transition) for l in data.legs] == [
        ("CEPIN", "below", 12000, 0, 250, ""), ("UBG", "between", 11000, 9000, 0, ""), ("HAWKZ", "above", 16000, 0, 0, "HAWKZ")]
    assert struct.calcsize("<i8sddifff") == LEG.size


def test_the_copilots_model_may_answer_beyond_its_facts_when_allowed():
    from localtc.atc_core.llm import LlmReply
    from localtc.crew.model import CrewModel

    class Backend:
        model = "m"

        def __init__(self):
            self.requests = []

        def complete(self, request, *, timeout_s):
            self.requests.append(request)
            return LlmReply('{"kind": "reply", "reply": "The APU takes about 90 seconds to start."}', 5.0)

    strict = Backend()
    reading, _ = CrewModel(strict).ask(0.0, "how long does the apu take", {"fuel": "5000 kg"})
    assert reading is None  # 90 isn't in the facts
    assert "Use ONLY the facts" in strict.requests[0].system
    free = Backend()
    reading, _ = CrewModel(free, beyond_facts=True).ask(0.0, "how long does the apu take", {"fuel": "5000 kg"})
    assert reading.reply.startswith("The APU") and "you may answer from what a first officer knows" in free.requests[0].system
