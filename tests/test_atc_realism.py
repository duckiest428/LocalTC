"""What ATC does around the flight, as pilots found it on three long flights: the traffic it names and how, giving
way and going around only when there's something to it, the runway the sim's own traffic is using, the copilot
after a go-around, and the smaller things (a cut-off call, a request taken back, a gate asked for)."""

import math

import msgspec
import pytest
from helpers.airports import kbfi, kpae
from test_phase import own
from test_traffic_sequencing import approaching, at_runway, crossing_ahead, ground_engine, said, taxiing, tower_engine

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.facilities import Facility
from localtc.atc_core.phraseology import speech
from localtc.atc_core.readback import PendingReadback
from localtc.sim_api import AirportData, AtcTransmission, PhaseChanged, TrafficSnapshot, TrafficTarget, Transcript

# --- naming the other aircraft ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("sim", "display", "spoken"), [
    ("B738", "Boeing 737", "Boeing seven thirty-seven"),  # never "bravo seven three eight"
    ("B38M", "Boeing 737", "Boeing seven thirty-seven"),
    ("B77W", "Boeing 777", "Boeing seven seventy-seven"),
    ("A21N", "Airbus A321", "Airbus three twenty-one"),
    ("A333", "Airbus A330", "Airbus three thirty"),
    ("E170", "Embraer 170", "Embraer one seventy"),  # never "echo one seven zero"
    ("E75L", "Embraer 175", "Embraer one seventy-five"),
    ("CRJ9", "CRJ", "regional jet"),
    ("C560", "Citation", "Citation"),
    ("737", "Boeing 737", "Boeing seven thirty-seven"),
    ("Airbus", "Airbus", "Airbus"),
])
def test_aircraft_are_named_as_controllers_say_them(sim, display, spoken):
    assert speech.aircraft_type(sim) == (display, spoken)


# --- giving way on the ground -----------------------------------------------------------------------------------


def test_traffic_on_a_runway_is_taking_off_not_in_the_way():
    """Seattle's 737 lining up on 34R was "crossing right to left" in front of an aircraft taxiing out."""
    engine = ground_engine()
    runway = at_runway(engine, "14R", gs_kt=10.0)
    ours = taxiing(engine)
    # Taxiing at the runway's threshold, the 737 square across our way, moving.
    here = msgspec.structs.replace(ours, lat=runway.lat, lon=runway.lon - 0.0013, hdg_true=90.0)
    engine.handle(TrafficSnapshot(t=1.0, targets=(msgspec.structs.replace(runway, hdg_true=180.0),)))
    assert "ground.give_way" not in said(engine, engine.handle(here))


def test_one_give_way_for_each_aircraft():
    engine = ground_engine()
    target = crossing_ahead(47.5300, -122.30067, 180.0)
    engine.handle(TrafficSnapshot(t=1.0, targets=(target,)))
    assert "ground.give_way" in said(engine, engine.handle(taxiing(engine)))
    engine._scheduled.clear()
    engine._gave_way_t = -math.inf  # long after
    engine.handle(TrafficSnapshot(t=500.0, targets=(target,)))
    later = msgspec.structs.replace(taxiing(engine), t=501.0)
    assert "ground.give_way" not in said(engine, engine.handle(later))


def test_the_side_it_crosses_from_is_said_only_when_its_clear():
    # Square across, from the left: "crossing left to right".
    left = crossing_ahead(47.5309, -122.30067, 180.0)  # ahead and a little to the left, going south across our way
    assert AtcEngine._paths_meet(taxiing(ground_engine()), left) == "left to right"
    # Coming at a shallow angle: it crosses, but which way isn't something to say.
    shallow = crossing_ahead(47.5300, -122.30067, 135.0)
    assert AtcEngine._paths_meet(taxiing(ground_engine()), msgspec.structs.replace(shallow, gs_kt=4.0)) == ""


def test_nobody_is_given_way_to_while_turning():
    """Toronto, swinging from 220 to 190 onto the next taxiway: the nose sweeps across everything around it."""
    engine = ground_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(crossing_ahead(47.5300, -122.30067, 180.0),)))
    engine.handle(msgspec.structs.replace(taxiing(engine, heading=150.0), t=898.0))
    assert "ground.give_way" not in said(engine, engine.handle(taxiing(engine, heading=90.0)))


# --- the runway: who's on it, and which way everyone is going ---------------------------------------------------


def _on_14r(engine: AtcEngine, *, along_m: float, lateral_m: float = 0.0, gs_kt: float = 0.0, hdg: float | None = None):
    geometry = engine.geometry("KBFI")
    end = geometry.end("14R")
    ux, uy = math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true))
    lat, lon = geometry.frame.to_latlon(end.threshold[0] + ux * along_m + uy * lateral_m,
                                        end.threshold[1] + uy * along_m - ux * lateral_m)
    return TrafficTarget(object_id=9, atc_id="SWA9", atc_model="A321", lat=lat, lon=lon, alt_ft=25.0,
                         hdg_true=end.heading_true if hdg is None else hdg, gs_kt=gs_kt, on_ground=True)


def test_no_go_around_for_traffic_rolling_out_far_down_the_runway():
    """Las Vegas: an A321 2 km down 26L at 40 kt, slowing to turn off. Gone long before this one gets there."""
    engine = tower_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(_on_14r(engine, along_m=2000.0, gs_kt=35.0),)))
    out = engine.handle(approaching(engine, distance_nm=1.0))
    assert not {"tower.go_around_traffic", "tower.go_around_traffic_contact"} & set(said(engine, out))


def test_no_go_around_for_traffic_beside_the_runway():
    """Beside the pavement, at a holding point by the threshold, isn't on it."""
    engine = tower_engine()
    half = engine.geometry("KBFI").end("14R").runway.half_width
    engine.handle(TrafficSnapshot(t=1.0, targets=(_on_14r(engine, along_m=300.0, lateral_m=half + 3.0),)))
    out = engine.handle(approaching(engine, distance_nm=1.0))
    assert not {"tower.go_around_traffic", "tower.go_around_traffic_contact"} & set(said(engine, out))


def test_a_go_around_isnt_repeated_to_an_aircraft_that_landed():
    """Las Vegas: "go around" again, and "how do you read?", to a flight already rolling out."""
    engine = tower_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(at_runway(engine, "14R"),)))
    engine.handle(approaching(engine, distance_nm=1.0))
    assert engine._going_around
    engine.state.pending = PendingReadback("tower.go_around_traffic_contact", "tower", {"altitude": 2000}, ("altitude",))
    engine._on_phase_change(PhaseChanged(t=600.0, previous="LANDING", phase="TAXI_IN", reason="rollout complete"),
                            approaching(engine, distance_nm=0.0))
    assert engine.state.pending is None and not engine._going_around
    assert not [s for s in engine._scheduled if s.instruction_id.startswith("tower.go_around")]


def test_departing_traffic_low_over_the_runway_isnt_landing():
    """Seattle: "hold short runway 34R, traffic B737 landing" for a 737 that had just lifted off it."""
    engine = AtcEngine(EngineConfig(callsign="N172LT", seed=3))
    engine.handle(AirportData(t=0.0, airport=kbfi()))
    engine.state.flight.origin = "KBFI"
    lifted = msgspec.structs.replace(_on_14r(engine, along_m=1500.0, gs_kt=150.0), on_ground=False)
    engine.handle(TrafficSnapshot(t=1.0, targets=(msgspec.structs.replace(lifted, alt_ft=150.0),)))
    engine.handle(TrafficSnapshot(t=3.0, targets=(msgspec.structs.replace(lifted, alt_ft=250.0),)))
    reason = engine._departure_blocked("14R")
    assert reason is None or reason[0] != "arrival", reason


def test_the_runway_in_use_follows_the_traffic():
    """MSFS picks its AI's runways itself. Seen taking off and landing one way, the ATIS goes that way too."""
    engine = tower_engine()
    end = engine.geometry("KBFI").end("32L")
    for n, t in enumerate((10.0, 70.0)):
        target = msgspec.structs.replace(_on_14r(engine, along_m=800.0, gs_kt=120.0, hdg=end.heading_true), object_id=20 + n)
        engine.handle(TrafficSnapshot(t=t, targets=(target,)))
    assert abs(engine.atis.flows["KBFI"] - end.heading_true) < 1.0
    off = AtcEngine(EngineConfig(callsign="N172LT", destination="KBFI", seed=3, traffic_runways=False))
    off.handle(AirportData(t=0.0, airport=kbfi()))
    off.state.flight.destination = "KBFI"
    for n, t in enumerate((10.0, 70.0)):
        off.handle(TrafficSnapshot(t=t, targets=(msgspec.structs.replace(_on_14r(off, along_m=800.0, gs_kt=120.0,
                                                                               hdg=end.heading_true), object_id=20 + n),)))
    assert off.atis.flows == {}


# --- frequencies --------------------------------------------------------------------------------------------------


def test_the_next_centre_is_never_on_this_centres_frequency():
    """Denver Center handed the flight to Los Angeles Center on Denver's own 135.45: the pilot "contacted" LA on the
    same frequency, Denver answered, and said "contact Los Angeles Center 135.45" round and round."""
    engine = AtcEngine(EngineConfig(callsign="ACA795", seed=3))
    engine._sector = Facility("center", "Denver Center", 135.45)
    candidates = iter([Facility("center", "Los Angeles Center", 135.45), Facility("center", "Los Angeles Center", 135.8)])
    engine._sector_candidate_on = lambda own, taken: next(candidates)  # type: ignore[method-assign]
    assert engine._sector_candidate(None).mhz == 135.8  # type: ignore[arg-type]


# --- what the pilot says --------------------------------------------------------------------------------------------


def _parked() -> tuple[AtcEngine, object]:
    engine = AtcEngine(EngineConfig(seed=3, destination="KBFI", cruise_ft=12000, callsign="N172LT"))
    spot = kpae().parking[0]
    here = own(0.0, lat=spot.lat, lon=spot.lon, alt_msl_ft=606, alt_indicated_ft=606, alt_agl_ft=0, hdg_true=340,
               hdg_mag=324, on_ground=True, gs_kt=0, ias_kt=0, com1_mhz=121.8)
    for event in (AirportData(t=0, airport=kpae()), AirportData(t=0, airport=kbfi()), here):
        engine.handle(event)
    return engine, here


def _say(engine: AtcEngine, here, t: float, text: str) -> list[AtcTransmission]:
    out = engine.handle(Transcript(t=t, text=text))
    for tick in range(1, 6):
        out += engine.handle(msgspec.structs.replace(here, t=t + tick))
    return [o for o in out if isinstance(o, AtcTransmission)]


def test_a_call_cut_off_gets_say_again():
    """ "That's all we're asking but sh-": the model answered half a call with the runway in use."""
    engine, here = _parked()
    [reply] = _say(engine, here, 5, "Paine Ground, N172LT, that's all we're asking but sh-")
    assert reply.instruction_id == "common.say_again"


def test_disregard_on_its_own_is_roger():
    engine, here = _parked()
    [reply] = _say(engine, here, 5, "Sorry, disregard this transmission, this current one.")
    assert reply.instruction_id == "common.roger"


def test_a_request_taken_back_takes_the_altitude_back():
    """ "Actually, we'd like to maintain that altitude, disregard the request for the descent": ATC carried on with the
    descent it had given, and told the flight to read it back three times."""
    engine = AtcEngine(EngineConfig(callsign="ACA795", seed=3))
    center = Facility("center", "Los Angeles Center", 135.8)
    engine.state.assignments.altitude_ft = 36000
    engine._granted = (100.0, 36000, 36000)
    engine.state.pending = PendingReadback("common.descend", "center", {"altitude": 34000}, ("altitude",), issued_t=100.0)
    engine.state.assignments.altitude_ft = 34000
    words = {"actually", "sorry", "we'd", "like", "to", "maintain", "that", "altitude", "disregard", "request", "for",
             "the", "descent"}
    engine._cancel_request("Actually, sorry, we'd like to maintain that altitude. Disregard request for the descent.",
                           words, center, 130.0)
    assert engine.state.pending is None and engine.state.assignments.altitude_ft == 36000
    assert [(s.instruction_id, s.slots["altitude"]) for s in engine._scheduled] == [("common.maintain", 36000)]


def test_saying_no_to_an_instruction_withdraws_it():
    """Phoenix: "push back and start up" taken for a taxi request; "No, no. Negative, we'd like to push back" got the
    pushback, and a minute later the taxi again ("did you copy?")."""
    engine, here = _parked()
    engine.state.phase = "PARKED"
    engine.state.pending = PendingReadback("ground.taxi_out_at", "ground", {"runway": "34L"}, ("runway",), issued_t=2.0)
    _say(engine, here, 5, "No, no, no. Negative, we'd like to push back and start up from our gate, N172LT.")
    assert engine.state.pending is None or engine.state.pending.instruction_id != "ground.taxi_out_at"
    assert not [s for s in engine._scheduled if s.instruction_id == "ground.taxi_out_at"]


def test_a_tail_asked_for_after_a_correction_is_the_one_given():
    """ "Tail left. Actually, sorry, can we get a tail right?": "unable", then "tail left"."""
    from localtc.atc_core.readback.intents import tail_side
    from localtc.atc_core.readback.normalize import normalize

    assert tail_side(normalize("Tail left. Actually, sorry. Can we get a tail right?")) == "right"
    assert tail_side(normalize("tail left, since tailing right makes no sense from our gate")) == "left"


def test_the_pilots_gate_when_the_scenery_has_it_and_parking_when_it_doesnt():
    from localtc.atc_core.airport import gates as stands
    from localtc.atc_core.airport.geometry import AirportGeometry
    from localtc.atc_core.readback.normalize import normalize

    assert stands.requested(normalize("we'd like to taxi to the gate Echo 9")) == "E9"
    assert stands.requested(normalize("request taxi to gate 46")) == "46"
    assert stands.requested(normalize("request taxi to the gate")) is None
    geometry = AirportGeometry(kbfi())
    some = stands.gates(geometry)
    named = next((g for g in some if g.word == "gate"), None)
    if named is not None:
        assert stands.named(geometry, named.label) == named
    assert stands.named(geometry, "Z99") is None


# --- stand by, the initial altitude, the ATIS -----------------------------------------------------------------------


def test_the_initial_altitude_isnt_always_5000():
    altitudes = set()
    for origin, sid in (("KPAE", None), ("KPAE", "PAINE2"), ("KPAE", "SEA9"), ("KPAE", "NORTH1")):
        engine = AtcEngine(EngineConfig(seed=3, sid=sid))
        engine.handle(AirportData(t=0, airport=kpae()))
        engine.state.flight.origin = origin
        altitudes.add(engine._initial_altitude())
    assert len(altitudes) > 1 and all(4000 <= a <= 10000 for a in altitudes)


def test_the_atis_names_the_instrument_approach_besides_the_visuals():
    from localtc.atc_core.airport.geometry import AirportGeometry
    from localtc.atc_core.atis.board import AtisBoard
    from localtc.atc_core.atis.observation import Weather
    from localtc.atc_core.values import Wind

    board = AtisBoard()
    info = board.update("KBFI", AirportGeometry(kbfi()), Weather(wind=Wind(140, 8), wind_dir_true=160.0, visibility_sm=10.0,
                                                                 altimeter_inhg=30.01), 63180.0, "Boeing Field")
    assert info is not None
    if info.operations.instrument:  # KBFI's 14R has its ILS
        assert "and visual approaches in use" in info.text and "Visual approaches in use" not in info.text


# --- the copilot ---------------------------------------------------------------------------------------------------


def test_the_copilot_doesnt_read_back_what_nobody_is_waiting_for():
    """LAX: "Descend and maintain FL320" read back half an hour late, on approach's frequency, long after it had been
    replaced; "cleared to land" read back to approach after the go-around."""
    from localtc.copilot import Copilot, Say

    engine = AtcEngine(EngineConfig(callsign="N172LT", seed=3))
    copilot = Copilot(engine, mode="assist", delay_s=(1.0, 1.0))
    center = Facility("center", "Los Angeles Center", 135.8)
    copilot._push("say", 10.0, text="Descend and maintain flight level three two zero, N172LT.",
                  answers="common.descend", heard_on=center)
    engine.state.pending = None  # read back by the pilot meanwhile
    assert not [a for a in copilot.due(100.0) if isinstance(a, Say)]


def test_the_check_in_after_a_go_around_gets_an_answer():
    """Back with approach after going around ("1,000 climbing 2,200"): no answer at all, until a traffic call."""
    engine = tower_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(at_runway(engine, "14R"),)))
    final = approaching(engine, distance_nm=1.0)
    engine.handle(final)
    approach = engine.facility("approach")
    assert approach is not None and engine._going_around
    engine._scheduled.clear()
    engine._go_around_checkin(700.0, approach, final)
    assert [s.instruction_id for s in engine._scheduled] == ["approach.go_around_checkin"]


def test_short_final_still_with_approach_is_sent_around():
    """Vectors not flown, the approach flown alone, never on tower's frequency: the flight landed without a word.
    On short final with no landing clearance, approach sends it around, once."""
    engine = tower_engine()
    approach = engine.facility("approach")
    assert approach is not None
    final = msgspec.structs.replace(approaching(engine, distance_nm=1.0), com1_mhz=approach.mhz, alt_agl_ft=300,
                                    alt_msl_ft=320, alt_indicated_ft=320)
    out = []
    for dt in range(0, 30, 2):  # long enough on short final for the phase to say landing
        out += engine.handle(msgspec.structs.replace(final, t=final.t + dt))
    assert engine.state.phase == "LANDING"
    assert said(engine, out).count("approach.go_around_no_clearance") == 1
