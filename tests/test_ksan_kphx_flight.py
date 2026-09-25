"""Regressions from the KSAN -> KPHX flight (2026-09-20), an A320neo flown by voice with the copilot on.

The language model timed out on nearly every call (llama3.2:3b on a CPU next to the sim), so this flight
is the grammar on its own, and it showed: a check-in that got three "say again"s, never climbed past
5,000 ft as a result, and was told "check altitude, maintain 5,000" for the rest of the way to Phoenix.
It also took off with an E170 on a one-mile final, crossed its own runway to reach the hold line, and
was cleared for the approach on a downwind leg pointing away from the airport.
"""

import gzip
import json
from pathlib import Path

import msgspec
import pytest
from helpers.airports import kbfi

from localtc.atc_core.airport import AirportGeometry, TaxiGraph
from localtc.atc_core.engine import (
    JOIN_FINAL_NM,
    JOIN_HEADING_DEG,
    JOIN_LATERAL_NM,
    AtcEngine,
    EngineConfig,
)
from localtc.atc_core.readback import (
    GrammarInterpreter,
    InterpretContext,
    PendingReadback,
)
from localtc.atc_core.readback.extract import taxi_routes
from localtc.atc_core.readback.intents import match_intents
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import clean_sim_name
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.sim_api import Airport, AirportData, TrafficTarget
from localtc.stt.vocabulary import fixup

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kphx"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


@pytest.fixture(scope="module")
def airports() -> dict[str, Airport]:
    found = {}
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("type") == "airport_data":
                found[row["airport"]["icao"]] = msgspec.convert(row["airport"], Airport)
    return found


def after(lines: list[str], text: str, count: int = 1) -> list[str]:
    start = next(i for i, line in enumerate(lines) if text in line)
    return [line for line in lines[start + 1 :] if " ATC " in line][:count]


def intents(text: str) -> list[str]:
    return [m.intent for m in match_intents(normalize(text))]


# --- the check-in, and everything that went wrong because of it ------------------------------------------


def test_a_full_stop_between_callsign_and_altitude_is_not_a_decimal_point():
    """Whisper wrote "Frontier 2084. 1300 feet": read as the number 2084.1300, it took callsign and altitude."""
    assert [t.text for t in normalize("Frontier 2084. 1300 feet")] == ["frontier", "2084", "1300", "feet"]
    assert [t.text for t in normalize("contact tower 121. 7")] == ["contact", "tower", "121.7"]  # still a frequency


@pytest.mark.parametrize("text", [
    "Socal Departure Frontier 2084. 1300 feet climbing 5000 feet.",
    "Frontier 24 climbing 5000 feet.",
    "Frontier 2084, passing 2,000 for 5,000",
])
def test_check_ins_with_feet_in_them(text):
    """ "5000 feet" counted as an instructed altitude, and a call with one can't be a check-in."""
    assert "checkin" in intents(text)


def test_the_check_in_gets_radar_contact_and_the_climb(replay):
    answer = after(replay, "1300 feet climbing 5000 feet")[0]
    # Up to the top of departure's airspace: the centres take it on up to FL350.
    assert "radar contact, climb and maintain 17,000" in answer, answer


def test_an_unreadable_first_call_to_a_new_controller_is_the_check_in(replay):
    """Whatever speech-to-text makes of the first call on a new frequency, it's the check-in: the controller
    has the flight on radar from the handoff. "A from Frontier 2084" was the first word Seattle heard."""
    answer = after(replay, "A from Frontier 2084")[0]
    # "Radar contact" or "roger": a flight handed over between radar controllers is identified already.
    assert ("radar contact" in answer or "roger" in answer) and "say again" not in answer, answer


def test_asking_for_a_climb_without_a_number():
    assert intents("Frontier 2084. Request climb.") == ["request_altitude"]
    assert "request_altitude" not in intents("Good day, my incredible people up there. We'd like the departure.")


def test_accepting_an_offered_level_after_a_false_start(replay):
    """ "No, no, no. We'll be able to accept flight level 370": the "no" was a false start, not a decline."""
    assert "climb and maintain FL370" in after(replay, "We'll be able to accept flight level 370")[0]


# --- what speech-to-text did, and the words around it --------------------------------------------------


@pytest.mark.parametrize(("heard", "meant"), [
    ("Tailrite Frontier 2084.", "tail right Frontier 2084."),
    ("Push back and start up approved till right for 208.", "Push back and start up approved tail right for 208."),
    ("A from Frontier 2084.", "affirm Frontier 2084."),
    ("Taxi runway 27, holding port Charley 1", "Taxi runway 27, holding point Charley 1"),
    ("Push back on my discussion and tail right", "Push back at my discretion and tail right"),
])
def test_mishearings_from_this_flight(heard, meant):
    assert fixup(heard) == meant


@pytest.mark.parametrize("text", [
    "tail right Frontier 2084.",
    "Push back at my discretion and tail right, Frontier 2084.",
    "Push back and start up approved tail right for 208.",
    "Hold position in Frontier 2084.",
])
def test_acknowledgements_are_not_new_requests(text):
    """Reading back a pushback approval was taken for asking again, and ATC approved it three times."""
    assert intents(text) == ["acknowledge"]


def test_reporting_the_holding_point_to_tower_is_asking_to_go():
    assert "ready_for_departure" in intents("Tower, Frontier 2084, Holding Point, Charlie Free. Right to the bar.")


def test_phrases_written_with_the_word_the_can_match():
    """normalize() drops "the", so every intent phrase containing it had never matched anything."""
    assert "ready_for_departure" in intents("We would like to get the departure")
    assert "clear_of_runway" in intents("clear of the runway")


def test_charley_is_charlie():
    assert taxi_routes(normalize("via Bravo, Bravo 6, Charley 6, Charley, Charley 1")) == [("B", "B6", "C6", "C", "C1")]


def test_a_taxi_route_read_back_without_via():
    assert taxi_routes(normalize("Taxi runway 27, B, B6, C6, C, C1."), expected=("B",)) == [("B", "B6", "C6", "C", "C1")]
    assert taxi_routes(normalize("runway 25 left, alpha, bravo 2"), expected=("A",)) == [("A", "B2")]  # not L, A, B2
    assert taxi_routes(normalize("Taxi runway 27, B, B6")) == []  # without a route to compare, "via" is needed


def test_the_sims_model_code_in_a_traffic_call():
    assert clean_sim_name("$$:E170") == "E170"


# --- the runway ----------------------------------------------------------------------------------------


def test_the_taxi_stays_on_this_side_of_the_runway(airports, replay):
    """C1 is a few metres nearer 27's threshold than B1, and choosing it sent the flight from the south-side
    gates across 09/27 at B6/C6 -- with no crossing clearance, so it was then blamed for an incursion."""
    taxi = after(replay, "Request Taxi")[0]
    assert "runway 27 at B1" in taxi and "via B, B1" in taxi


def test_a_route_over_the_departure_runway_is_a_crossing(airports):
    geo = AirportGeometry(airports["KSAN"])
    graph = TaxiGraph(geo)
    start = graph.nearest_node(32.7312, -117.1928, kinds=("point", "parking"))  # the south-side gate
    routes = [graph.route(start, {("point", h.point.index)}, hold_short="27")
              for h in geo.hold_shorts if h.runway is geo.end("27").runway]
    to_c1 = next(r for r in routes if r is not None and r.hold_point == "C1")  # the one the old choice took
    assert "09/27" in to_c1.crossings, to_c1


def test_holding_short_for_landing_traffic(replay):
    """An MD80 still on the runway and an E170 on a five-mile final: tower cleared the takeoff anyway."""
    answer = after(replay, "Holding Point, Charlie Free")[0]
    assert "hold short runway 27" in answer and "E170" in answer and "final" in answer, answer


def test_cleared_for_takeoff_is_the_wrong_readback_of_hold_short(replay):
    """The one readback that must be corrected: told to hold short, read back as cleared for takeoff."""
    assert "negative, hold short runway 27" in after(replay, "Clear for takeoff runway 27")[0]


def departure_engine() -> AtcEngine:
    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KPAE", seed=3))
    engine.handle(AirportData(t=0.0, airport=kbfi()))
    engine.state.flight.origin = "KBFI"
    return engine


def on_final(engine: AtcEngine, runway: str, miles: float, object_id: int = 7) -> TrafficTarget:
    geo = engine.geometry("KBFI")
    end = geo.end(runway)
    ux, uy = (end.threshold[0] - end.runway.center[0], end.threshold[1] - end.runway.center[1])
    scale = miles * 1852.0 / (ux * ux + uy * uy) ** 0.5
    lat, lon = geo.frame.to_latlon(end.threshold[0] + ux * scale, end.threshold[1] + uy * scale)
    return TrafficTarget(object_id=object_id, atc_id="SWA1", atc_model="B737", lat=lat, lon=lon,
                         alt_ft=miles * 300 + 20, hdg_true=end.heading_true, gs_kt=140.0, on_ground=False)


def on_runway(engine: AtcEngine, runway: str, object_id: int = 8) -> TrafficTarget:
    geo = engine.geometry("KBFI")
    lat, lon = geo.frame.to_latlon(*geo.end(runway).runway.center)
    return TrafficTarget(object_id=object_id, atc_id="SWA2", atc_model="B737", lat=lat, lon=lon, alt_ft=20.0,
                         hdg_true=geo.end(runway).heading_true, gs_kt=15.0, on_ground=True)


def released(engine: AtcEngine, traffic: tuple[TrafficTarget, ...]) -> str:
    from localtc.sim_api import TrafficSnapshot

    engine._scheduled.clear()
    engine.handle(TrafficSnapshot(t=1.0, targets=traffic))
    engine._release(1.0, engine.facility("tower"), "14R", answering=False)
    return engine._scheduled[-1].instruction_id if engine._scheduled else ""


def test_an_empty_runway_is_a_takeoff_clearance():
    assert released(departure_engine(), ()) == "tower.takeoff"


def test_landing_traffic_close_in_holds_the_departure_short():
    assert released(departure_engine(), (on_final(departure_engine(), "14R", 3.0),)) == "tower.hold_short_traffic"


def test_an_arrival_further_out_does_not():
    engine = departure_engine()
    assert released(engine, (on_final(engine, "14R", 8.0),)) == "tower.takeoff"


def test_an_occupied_runway_lines_the_departure_up_behind():
    engine = departure_engine()
    assert released(engine, (on_runway(engine, "14R"),)) == "tower.luaw"


def test_occupied_with_an_arrival_coming_holds_short_rather_than_lining_up():
    """Five miles is enough to go ahead of an arrival on an empty runway, not when it first has to be vacated:
    lining up there would leave the departure sitting on the runway as the arrival arrived."""
    engine = departure_engine()
    assert released(engine, (on_runway(engine, "14R"), on_final(engine, "14R", 5.0))) == "tower.hold_short_traffic"


def test_once_the_arrival_has_landed_and_cleared_the_departure_goes():
    engine = departure_engine()
    assert released(engine, (on_final(engine, "14R", 3.0),)) == "tower.hold_short_traffic"
    assert released(engine, ()) == "tower.takeoff"


# --- the arrival ---------------------------------------------------------------------------------------


def joining_final_at(airports) -> float:
    geo = AirportGeometry(airports["KPHX"])
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("type") != "ownship_state" or row["t"] < 3500:
                continue
            final = geo.final_approach(row["lat"], row["lon"], row["hdg_true"], max_distance_nm=JOIN_FINAL_NM,
                                       max_lateral_m=JOIN_LATERAL_NM * 1852.0, heading_tolerance=JOIN_HEADING_DEG)
            if final is not None and final.end.ident == "26" and final.distance_nm >= 1.0:
                return row["t"]
    raise AssertionError("never joined the final for 26")


def test_the_approach_is_cleared_joining_final_not_on_the_downwind(airports):
    """Cleared at t=3837: 18 nm out, heading 086 away from the runway, 9,900 ft up. The last fix before the
    straight line in is where it belongs, and the aircraft got there at about t=4250."""
    assert 4200 < joining_final_at(airports) < 4300


def test_tower_says_continue_until_the_aircraft_is_on_final(replay):
    """Cleared to land on first contact, nowhere near final and with no look at the runway."""
    landing = next(line for line in replay if "cleared to land" in line and "Phoenix Tower" in line)
    sequenced = next(line for line in replay if "Phoenix Tower" in line and " ATC " in line)
    assert float(landing.split("]")[0].strip("[ ")) > float(sequenced.split("]")[0].strip("[ ")), (sequenced, landing)


# --- the copilot ---------------------------------------------------------------------------------------


def test_the_copilot_repeats_only_its_own_calls():
    """ATC's "say again" to the pilot's own voice call had the copilot repeat "maintain five thousand"."""
    from localtc.copilot import Copilot
    from localtc.sim_api import AtcTransmission, Transcript

    engine = AtcEngine(EngineConfig(callsign="N172LT", seed=1))
    copilot = Copilot(engine, mode="assist")
    copilot._last_said = "Maintain five thousand, two lima tango"
    copilot.observe(Transcript(t=10.0, text="Request climb", source="voice"))
    copilot.observe(AtcTransmission(t=12.0, station="Seattle Center", frequency_mhz=125.1, text="N172LT, say again",
                                    instruction_id="common.say_again"))
    assert not copilot._queue
    copilot.observe(Transcript(t=20.0, text="Maintain five thousand, two lima tango", source="copilot"))
    copilot.observe(AtcTransmission(t=22.0, station="Seattle Center", frequency_mhz=125.1, text="N172LT, say again",
                                    instruction_id="common.say_again"))
    assert copilot._queue


# --- the flight plan: when the climb ends and the descent begins -------------------------------------------


@pytest.fixture(scope="module")
def planned() -> list[str]:
    """The flight again with its SimBrief plan's fixes, as the app now passes them to ATC."""
    from localtc.config import RouteFix

    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    plan = json.loads((FLIGHT / "flightplan.json").read_text(encoding="utf-8"))
    cfg.flight.fixes = [RouteFix(ident=f["ident"], lat=f["lat"], lon=f["lon"], alt_ft=f["alt_ft"], stage=f["stage"])
                        for f in plan["fixes"]]
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def test_the_clearance_expects_the_cruise_when_the_plan_reaches_it(planned):
    """Always "one zero minutes" before. The plan's top of climb says about twenty, and the flight took 20.6."""
    clearance = next(line for line in planned if "cleared to Phoenix" in line)
    assert "expect FL350 two zero minutes after departure" in clearance, clearance


def test_the_descent_is_cleared_before_the_aircraft_starts_down(planned):
    """The FMS began the descent at t=3000, 106 nm out, and ATC only answered it. With the plan's top of
    descent ATC clears it first: descend via the filed arrival, when the crew is ready."""
    descent = next(line for line in planned if "descend via the HYDRR1 arrival" in line)
    assert float(descent.split("]")[0].strip("[ ")) < 2990, descent


@pytest.mark.parametrize("said", [
    "Descend via the hydra one arrival, Frontier 2084",
    "Descending via Hydra 1, Frontier 2084",
    "descend via, Frontier 2084",
])
def test_descend_via_read_back_however_the_star_is_spelled(said):
    pending = PendingReadback(instruction_id="center.descend_via", controller="center",
                              expected={"descend_via": True, "procedure": "HYDRR1"},
                              required=("descend_via",), optional=("procedure",))
    heard = GrammarInterpreter().interpret(said, pending, InterpretContext(phase="CRUISE"))
    assert heard.status == "correct", (heard.status, heard.mismatched)


def test_procedure_names_match_by_sound_and_number_exactly():
    from localtc.atc_core.readback.extract import procedure_matches

    assert procedure_matches("HYDRA1", "HYDRR1") and procedure_matches("ZOO4", "ZZOOO4") and procedure_matches("HOGS1", "HOGGZ1")
    assert not procedure_matches("HYDRA2", "HYDRR1")  # the number is not a matter of spelling
    assert not procedure_matches("EAGUL5", "HYDRR5")


def test_minutes_to_cruise_from_the_plan_its_distance_or_the_altitude():
    from localtc.atc_core.route import Route, RouteFix

    timed = Route((RouteFix("A", 32.7, -117.3, stage="CLB", time_s=300), RouteFix("TOC", 32.8, -115.3, stage="CLB", time_s=1080)))
    assert timed.minutes_to_cruise(35000) == 18
    untimed = Route((RouteFix("A", 32.7, -117.3, stage="CLB"), RouteFix("TOC", 32.75, -115.29, stage="CLB")))
    assert 18 <= untimed.minutes_to_cruise(35000) <= 22  # ~100 nm at a jet's climb groundspeed
    assert Route().minutes_to_cruise(35000) == 19  # FL350 at 1,800 fpm
    assert Route().minutes_to_cruise(5000) == 8 and Route().minutes_to_cruise(3000) == 5  # never under five


def test_simbrief_fix_times_reach_the_flight_config():
    from localtc.config import FlightConfig
    from localtc.flightplan import apply_plan, parse_simbrief

    data = {"fetch": {"status": "Success"}, "origin": {"icao_code": "KSAN"}, "destination": {"icao_code": "KPHX"},
            "general": {"initial_altitude": "35000"}, "atc": {"callsign": "FFT2084"},
            "navlog": {"fix": [{"ident": "TOC", "pos_lat": "32.75", "pos_long": "-115.29", "altitude_feet": "35000",
                                "stage": "CLB", "time_total": "1234"}]}}
    flight = FlightConfig()
    apply_plan(parse_simbrief(data), flight)
    assert flight.fixes[0].ident == "TOC" and flight.fixes[0].time_s == 1234
