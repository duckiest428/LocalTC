"""The turnaround's timeline, cabin incidents, conditions, the dynamic routing's checks and the terrain's."""

from types import SimpleNamespace as N

from localtc.atc_core.atis.operations import read_notices  # noqa: F401 - (the module's import order)
from localtc.atc_core.conditions import Condition, ConditionBook
from localtc.atc_core.flow import FlowMixin
from localtc.atc_core.terrain import TerrainModel
from localtc.crew.cabin import CabinCrew, CabinSettings
from localtc.crew.cabin import Facts as CabinFacts
from localtc.crew.cabin_incidents import FREQUENCY, COOLDOWN_S, IncidentDirector, Inputs, decision_in, rates, slot_of
from localtc.sim_api import AtcTransmission, CabinIncident, CabinSpeech, FlightArrived, GroundStatus, IntercomHeard, OwnshipState, PhaseChanged, TerrainGrid
from localtc.sim_api.events import GroundServiceState
from localtc.turnaround_plan import ORDER, PROFILES, Facts, Turnaround, durations, profile_for, variation

STD_ZULU = 50000.0


def own(t, *, zulu=None, fuel=20000.0, temp=15.0, gs=0.0, ground=True, precip=0):
    return OwnshipState(t=t, lat=25.79, lon=-80.28, alt_msl_ft=20.0, alt_indicated_ft=20.0, alt_agl_ft=10.0, altimeter_inhg=29.92, hdg_mag=90.0,
                        hdg_true=90.0, ias_kt=gs, gs_kt=gs, vs_fpm=0, on_ground=ground, squawk="2000", xpdr_mode="alt", com1_mhz=121.8, com2_mhz=121.5, gear_down=True, flaps_index=0, engine_running=False, on_runway=False, zulu_s=zulu if zulu is not None else STD_ZULU - 7200 + t,
                        fuel_lb=fuel, temperature_c=temp, precip=precip, parking_brake=True)


def facts(**kw):
    base = {"callsign": "TAP224", "aircraft": "A339", "pax": 280, "block_fuel_lb": 120000.0, "sched_out_s": int(STD_ZULU), "seed": 7}
    return Facts(**{**base, **kw})


def status(t, gsx=True, **services):
    return GroundStatus(t=t, provider="gsx" if gsx else "", connected=gsx,
                        services=tuple(GroundServiceState(id=k, name=k, state=v) for k, v in services.items()))


# --- the timeline ------------------------------------------------------------------------------------------------------

def test_every_milestone_is_there_with_a_reason_for_its_time():
    tr = Turnaround(facts())
    tr.observe(own(10))
    tr.observe(status(11, Catering="available"))
    js = tr.to_json()
    assert [m["id"] for m in js["milestones"]] == list(ORDER)
    boarding = next(m for m in js["milestones"] if m["id"] == "boarding")
    assert "280 passengers" in boarding["why"] and boarding["latest"] and boarding["opens"] and boarding["eta"]
    assert "lb to load" in next(m for m in js["milestones"] if m["id"] == "fueling")["why"]


def test_the_timing_comes_from_the_flight():
    wide = durations(facts(), PROFILES["full_service"], 20000.0)
    narrow = durations(facts(aircraft="A320", pax=150, block_fuel_lb=20000.0, callsign="ACA1"), PROFILES["full_service"], 15000.0)
    assert wide["boarding"][0] != narrow["boarding"][0] and wide["fueling"][0] > narrow["fueling"][0]
    low = durations(facts(), PROFILES["low_cost"], 20000.0)
    assert low["boarding"][0] < wide["boarding"][0] and low["catering"][0] < wide["catering"][0]


def test_the_days_variation_is_bounded_and_always_the_same():
    values = [variation(s, "TAP224", m) for s in range(200) for m in ORDER]
    assert min(values) >= 0.92 and max(values) <= 1.08
    assert variation(3, "TAP224", "boarding") == variation(3, "TAP224", "boarding")


def test_an_airlines_profile_or_the_ones_asked_for():
    assert profile_for("auto", "RYR123", 189).id == "low_cost" and profile_for("auto", "FDX12", 0).id == "cargo"
    assert profile_for("auto", "BAW1", 300).id == "full_service" and profile_for("regional", "BAW1", 300).id == "regional"


def test_too_early_the_crew_arent_there_and_it_says_when():
    tr = Turnaround(facts())
    tr.observe(own(10))  # two hours before the push
    why = tr.hold("Catering", 10.0)
    assert why and "slot" in why
    assert tr.hold("Boarding", 10.5, ask=False)  # only looked at: it stays held
    assert tr.hold("Catering", 20.0) is None  # asked again: they find a way


def test_advisory_never_holds_a_service():
    tr = Turnaround(facts(), mode="advisory")
    tr.observe(own(10))
    assert tr.hold("Catering", 10.0) is None


def test_a_plan_far_from_the_clock_isnt_held_to():
    tr = Turnaround(facts())
    tr.observe(own(10, zulu=STD_ZULU - 20000))
    assert tr.status()[0] == "unheld" and tr.hold("Catering", 11.0) is None and tr.reminder(12.0) is None


def test_boarding_waits_on_what_it_depends_on():
    tr = Turnaround(facts())
    tr.observe(own(5400, zulu=STD_ZULU - 1800))  # half an hour out: the crew are here
    tr.observe(status(5401, Catering="performing", Cleaning="available", Boarding="available"))
    tr.observe(own(5402, zulu=STD_ZULU - 1798))
    assert "catering" in tr.ms["boarding"].blocked_by
    assert "wait for catering" in (tr.hold("Boarding", 5403.0) or "")


def test_a_running_service_is_forecast_from_its_real_rate():
    tr = Turnaround(facts())
    tr.observe(own(5000, zulu=STD_ZULU - 2200))
    tr.observe(GroundStatus(t=5001, provider="gsx", connected=True, services=(GroundServiceState(id="Boarding", name="b", state="performing", status="pax 0/280"),)))
    tr.observe(GroundStatus(t=5021, provider="gsx", connected=True, services=(GroundServiceState(id="Boarding", name="b", state="performing", status="pax 2/280"),)))
    tr.observe(own(5100, zulu=STD_ZULU - 2100))
    assert "of 280" not in tr.ms["boarding"].reason  # the first few aren't the pace
    tr.observe(GroundStatus(t=5301, provider="gsx", connected=True, services=(GroundServiceState(id="Boarding", name="b", state="performing", status="pax 100/280"),)))
    tr.observe(own(5301, zulu=STD_ZULU - 1899))
    ms = tr.ms["boarding"]
    assert "100 of 280" in ms.reason and abs(ms.eta - (5301 + 180 / (100 / 300))) < 2


def test_fuel_already_on_board_is_nothing_to_wait_for():
    tr = Turnaround(facts(block_fuel_lb=10000.0))
    tr.observe(own(10, fuel=20000.0))
    assert tr.ms["fueling"].state == "skipped" and "already on board" in tr.ms["fueling"].why


def test_the_copilot_reminds_and_with_the_reason():
    tr = Turnaround(facts())
    tr.observe(own(7000, zulu=STD_ZULU - 400))  # seven minutes to go, nothing done
    tr.observe(status(7001, Boarding="available", Catering="available", Cleaning="available", Refueling="available"))
    tr.observe(own(7002, zulu=STD_ZULU - 398))
    assert tr.reminder(7002.0) is None  # not the moment the flight's started
    tr.observe(own(7200, zulu=STD_ZULU - 200))
    key, words = tr.reminder(7200.0)
    assert key and "minutes ago" in words and "push about" in words
    assert tr.reminder(7201.0) is None  # not again straight away
    assert tr.status()[0] == "late" and tr.driver() is not None
    assert "behind" in tr.answer("flight")


def test_each_crew_group_answers_for_its_own():
    tr = Turnaround(facts())
    tr.observe(own(5400, zulu=STD_ZULU - 1800))
    assert tr.answer("ramp", "how long for the bags").startswith("Baggage and cargo")
    assert "Boarding" in tr.answer("cabin", "when's boarding")
    assert tr.answer("cabin").startswith(("Boarding", "Doors", "Crew"))


def test_the_clearance_and_the_push_complete_it():
    tr = Turnaround(facts())
    tr.observe(own(10))
    tr.observe(AtcTransmission(t=20, station="Miami Ground", frequency_mhz=121.8, text="push approved", instruction_id="ground.pushback"))
    assert tr.ms["clearance"].state == "done"
    tr.observe(PhaseChanged(t=30, previous="PUSHBACK", phase="TAXI_OUT"))
    assert tr.ms["ready"].state == "done" and tr.ms["deicing"].state == "skipped"


def test_de_icing_only_in_icing_conditions():
    tr = Turnaround(facts())
    tr.observe(own(10, temp=-2.0, precip=1))
    assert tr.deice_needed and tr.ms["deicing"].state == "pending"
    clear = Turnaround(facts())
    clear.observe(own(10))
    assert clear.ms["deicing"].state == "skipped"


def test_a_cabin_problem_holds_boarding_with_its_reason():
    tr = Turnaround(facts())
    tr.observe(own(5000, zulu=STD_ZULU - 2200))
    before = tr.ms["boarding"].minutes
    tr.observe(CabinIncident(t=5001, incident="x", kind="gate_assist", stage="decided", delay_s=240.0, why="a passenger needing assistance"))
    tr.observe(own(5002, zulu=STD_ZULU - 2198))
    assert abs(tr.ms["boarding"].minutes - before - 4.0) < 0.01 and "needing assistance" in tr.ms["boarding"].why


# --- incidents -----------------------------------------------------------------------------------------------------------

def cruise(**kw):
    return Inputs(**{"phase": "CRUISE", "planned_min": 420.0, "seats": 298, "pax": 280, "level_for_s": 4000.0, "to_go_nm": 2000.0, **kw})


def first_slots(d, slots=400):
    out = []
    for n in range(slots):
        i = cruise(level_for_s=900.0 + n * 1200.0)
        got = d.tick(i, 10000.0 + n * 5000.0)
        if got:
            out.append((n, got))
            break
    return out


def test_off_is_off():
    d = IncidentDirector("off", 1, "TAP224")
    assert not d.tick(cruise(), 5000.0) and not d.on


def test_the_same_flight_has_the_same_incident():
    a, b = first_slots(IncidentDirector("frequent", 11, "TAP224")), first_slots(IncidentDirector("frequent", 11, "TAP224"))
    assert a and a[0][0] == b[0][0] and a[0][1][0][0] == b[0][1][0][0]
    other = [first_slots(IncidentDirector("frequent", s, "TAP224")) for s in range(30)]
    assert len({str(o) for o in other}) > 3


def test_the_odds_come_from_the_flight():
    long_rough = rates(cruise(rough=True))["medical"][0]
    short = rates(cruise(planned_min=60.0, pax=70))["medical"][0]
    assert long_rough > short * 2
    assert "gate_assist" in rates(Inputs(phase="PARKED", boarding=True, pax=180)) and "medical" not in rates(Inputs(phase="PARKED", boarding=True))
    assert rates(cruise(late_min=25.0))["unruly"][0] > rates(cruise())["unruly"][0]


def test_rare_is_rarer_than_frequent():
    def count(freq):
        return sum(bool(first_slots(IncidentDirector(freq, s, "TAP224"), 20)) for s in range(300))
    assert count("rare") < count("normal") < count("frequent") and FREQUENCY["rare"] < FREQUENCY["normal"]


def test_never_at_a_critical_moment_nor_on_top_of_another():
    d = IncidentDirector("frequent", 2, "TAP224")
    for phase in ("TAKEOFF", "APPROACH", "LANDING", "TAXI_OUT"):
        assert slot_of(Inputs(phase=phase, level_for_s=5000.0), 0.0) is None
    assert not d.tick(cruise(busy=True), 5000.0)  # an emergency running
    got = first_slots(d)
    assert got
    assert d.current is not None
    before = d.current
    assert not any(s[1] is not None and s[1].stage == "reported" for s in d.tick(cruise(level_for_s=900000.0), 10000.0))
    assert d.current is before


def test_an_incident_waits_through_the_sterile_part_of_the_flight():
    d = IncidentDirector("frequent", 2, "TAP224")
    first_slots(d)
    assert d.current.reminded is False
    t = d.current.started + 300.0
    assert not d.tick(Inputs(phase="APPROACH"), t) and not d.current.reminded  # not chased on the approach
    assert d.tick(cruise(), t)[0][0].startswith("Captain, sorry to chase")


def test_the_captains_choice_is_understood():
    assert decision_in("carry on please") == "continue" and decision_in("get paramedics to meet us") == "assist"
    assert decision_in("we'll divert to the nearest airport") == "divert" and decision_in("declare an emergency") == "escalate"
    assert decision_in("what's the weather") is None


def test_every_incident_ends_with_a_recorded_outcome_and_a_cooldown():
    d = IncidentDirector("frequent", 2, "TAP224")
    first_slots(d)
    kind = d.current.kind
    said = d.decide("assist", d.current.started + 30.0, cruise())
    assert said[0][1].stage == "decided" and said[0][1].decision == "assist" and said[0][1].kind == kind
    done = d.arrived(50000.0)
    assert done[0][1].stage == "resolved" and done[0][1].outcome and d.current is None
    assert not d.tick(cruise(level_for_s=70000.0), 50000.0 + COOLDOWN_S - 1.0)


def test_unanswered_the_crew_carry_on():
    d = IncidentDirector("frequent", 2, "TAP224")
    first_slots(d)
    start = d.current.started
    d.tick(cruise(), start + 200.0)
    out = d.tick(cruise(), start + 400.0)
    assert out[0][0].startswith("Haven't heard") and d.current.decision == "continue"


def gate_cabin(frequency="frequent", seed=4):
    cabin = CabinCrew(CabinSettings(incidents=frequency), lambda: CabinFacts(callsign="TAP224", aircraft="A339", origin="KMIA", destination="LPPT"), seed=seed)
    return cabin


def test_the_cabin_crew_play_it_and_take_the_captains_word():
    for seed in range(200):
        cabin = gate_cabin(seed=seed)
        cabin.observe(PhaseChanged(t=1.0, previous=None, phase="CRUISE"))
        said = []
        cabin._level_since = 0.0
        said += cabin.observe(own(4000.0, ground=False, gs=450.0))
        if cabin.incidents.current is not None:
            break
    else:
        raise AssertionError("no seed made an incident")
    assert any(isinstance(o, CabinIncident) and o.stage == "reported" for o in said)
    assert cabin.claims("get paramedics to meet us", 4010.0)
    out = cabin.heard(IntercomHeard(t=4010.0, text="get paramedics to meet us", to="cabin"))
    assert any(isinstance(o, CabinSpeech) for o in out) and [o.decision for o in out if isinstance(o, CabinIncident)] == ["assist"]
    out = cabin.observe(FlightArrived(t=9000.0, airport="LPPT", gate="A1"))
    assert cabin.incidents.current is None and [o.stage for o in out if isinstance(o, CabinIncident)] == ["resolved"]


# --- conditions and routing ----------------------------------------------------------------------------------------------

def test_a_condition_says_what_came_of_it():
    book = ConditionBook()
    c = book.add(Condition("cross_after", 1.0, "ground.hold_short_landing"))
    book.end(c, "satisfied", 5.0, "landed", next_instruction="ground.cross_runway")
    assert c.to_json()["next_instruction"] == "ground.cross_runway" and c.to_json()["state"] == "satisfied"


GRID = TerrainGrid(t=0.0, lat0=39.0, lon0=-106.0, dlat=1 / 30, dlon=1 / 30, rows=60, cols=60,
                   ft=tuple(11000 if c > 30 else 500 for r in range(60) for c in range(60)))


def model():
    m = TerrainModel()
    m.add(GRID)
    return m


def test_terrain_samples_clears_and_finds_corridors():
    m = model()
    assert m.sample_ft(39.5, -105.8) == 500 and m.clearance_ft(39.5, -105.8, 3000.0) == 2500.0
    assert m.route_corridor_ft([(39.5, -105.8), (39.5, -104.9)]) == 13000  # into the mountains: 11,000 + 2,000
    assert m.route_corridor_ft([(39.5, -105.8), (39.5, -105.7)]) == 1500


def test_minimum_safe_altitude_by_phase_and_mountains():
    m = model()
    assert m.minimum_safe_ft(39.5, -105.8, phase="enroute") == 1500
    assert m.minimum_safe_ft(39.5, -104.8, phase="terminal", airport_nm=20.0) == 13000
    assert m.mountainous(39.5, -105.2) and not m.mountainous(39.5, -105.95)


def test_a_vector_into_rising_ground_is_turned_away():
    m = model()
    assert m.corridor_ft(39.5, -105.2, 90.0, 15.0) == 13000
    heading = m.safe_heading(39.5, -105.2, 90.0, 6000.0)
    assert heading is not None and m.corridor_ft(39.5, -105.2, heading, 15.0) <= 6000
    assert heading != 90.0
    assert m.safe_heading(39.5, -104.5, 90.0, 6000.0) is None  # nothing's safe from in among them


def test_a_route_change_has_to_be_valid():
    m = model()
    flow = FlowMixin()
    here = N(lat=39.5, lon=-105.2, alt_indicated_ft=6000.0, hdg_true=90.0)
    flow.terrain = m
    flow._route_points = lambda: [("HIGH", 39.5, -104.9)]
    flow._known_fixes = lambda: {"HIGH"}
    flow._hazards_on = lambda a, b, level: []
    flow.state = N(flight=N(destination="", origin=""), phase=None)
    flow.geometry = lambda icao: None
    flow.region = N(icao=False)
    assert not flow.validate(here, fix="NOWHERE").ok  # no such fix
    assert not flow.validate(here, fix="HIGH").ok and "terrain" in flow.validate(here, fix="HIGH").reason
    assert flow.validate(here, fix="HIGH", altitude_ft=14000).ok
    assert not flow.validate(here, heading_true=90.0).ok and flow.validate(here, heading_true=270.0).ok
    flow.terrain = TerrainModel()
    assert flow.validate(here, heading_true=90.0).ok  # terrain not known: not held against it
