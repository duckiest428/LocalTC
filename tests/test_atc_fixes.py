"""Fixes found flying LIMC to LFPG: the descent with a level to stop at (ICAO), the STAR's floor from its published
restrictions, the arrival said as an arrival, a level reported answered, the right controller on a shared frequency,
and a direct only to a fix that exists."""

from types import SimpleNamespace

import msgspec
from test_engine import parked_engine

from localtc.atc_core import region as regions
from localtc.atc_core.engine import _known_fix
from localtc.atc_core.facilities import Facility
from localtc.atc_core.phraseology import speech
from localtc.sim_api import ArrivalData, ArrivalLeg


def test_a_misheard_fix_is_the_routes_fix_it_sounds_like_and_an_unknown_one_none():
    known = {"NOBKE", "AOSTA", "MC521", "URELO"}
    assert _known_fix("Knob Itarrow", known) == "NOBKE"
    assert _known_fix("nobke", known) == "NOBKE"
    assert _known_fix("NOBKE Itarrow 1128", known) == "NOBKE"
    assert _known_fix("Kilimanjaro", known) is None


def test_the_fixes_a_flight_knows_are_its_routes_without_the_plans_markers():
    engine, own = parked_engine()
    engine.cfg.route = (SimpleNamespace(ident="NOBKE"), SimpleNamespace(ident="AOSTA"))
    assert engine._known_fixes() == {"NOBKE", "AOSTA"}


def test_the_stars_floor_is_its_lowest_published_level_on_the_landing_runways_way_in():
    engine, own = parked_engine(star="TINI9W", destination="LFPG")
    legs = (ArrivalLeg(fix="FF302", altitude="below", alt1_ft=28000),
            ArrivalLeg(fix="URELO", altitude="below", alt1_ft=12000),
            ArrivalLeg(fix="OKIPA", altitude="between", alt1_ft=11000, alt2_ft=7000),
            ArrivalLeg(fix="LORNI", altitude="at", alt1_ft=4000, transition="RW27R"))  # another runway's
    engine.state.flight.destination = "LFPG"
    engine.handle(ArrivalData(t=1, airport="LFPG", name="TINI9W", legs=legs))
    engine.state.assignments.arrival_runway = "09R"
    assert engine._star_floor() == 11000


def test_europe_says_flight_levels_above_its_own_transition_altitude():
    with regions.speaking(regions.region_for("LFFF")):
        assert speech.altitude_display(11000) == "FL110"
        assert speech.altitude_display(4000) == "4,000 feet"
    with regions.speaking(regions.region_for("KZSE")):
        assert speech.altitude_display(11000) == "11,000"


def test_a_star_said_again_is_an_arrival_not_a_departure():
    engine, own = parked_engine()
    said = engine._fragments(["procedure"], {"procedure": "TINI9W", "approach": "ILS RWY 09R"})
    assert said.display == "via the TINI9W arrival"
    assert engine._fragments(["procedure"], {"procedure": "AOS8Z"}).display == "via the AOS8Z departure"


def test_the_icao_descent_templates_carry_the_level_and_its_readback():
    engine, own = parked_engine()
    library = engine.libraries["icao"]
    for iid in ("center.descend_star", "approach.cleared_star_descend"):
        assert "altitude" in library.get(iid).readback.required


def test_a_level_reported_to_radar_is_answered_never_left_in_silence():
    engine, own = parked_engine()
    radar = Facility("approach", "De Gaulle Approach", 118.155, "LFPG")
    engine._checked_in.add(radar.station)
    up = msgspec.structs.replace(own, on_ground=False, alt_indicated_ft=15000, alt_agl_ft=14600)
    engine.state.aircraft = up
    engine.state.assignments.altitude_ft = 4000
    report = SimpleNamespace(intent="acknowledge", values={"altitude": "15000"})
    assert engine._level_report(report, radar, 10.0, up)
    assert engine._scheduled[-1].instruction_id == "common.descend"  # the 4,000 said again
    engine._scheduled.clear()
    engine.state.assignments.altitude_ft = 15000
    assert engine._level_report(report, radar, 20.0, up)
    assert engine._scheduled[-1].instruction_id == "common.roger"
    engine._scheduled.clear()
    thanks = SimpleNamespace(intent="acknowledge", values={})
    assert not engine._level_report(thanks, radar, 30.0, up)  # a thank-you: nothing to answer


def test_on_a_shared_frequency_the_controller_that_just_sent_the_flight_on_answers():
    engine, own = parked_engine()
    clearance = Facility("clearance", "Malpensa Clearance", 120.9, engine.state.flight.origin)
    tower = Facility("tower", "Malpensa Planning Tower", 120.9, engine.state.flight.origin)
    engine.facilities = [clearance, tower, *engine.facilities]
    assert engine._facility_for(120.9) is clearance  # parked: the airport's own order (its clearance first here)
    up = msgspec.structs.replace(own, on_ground=False, alt_indicated_ft=1500, alt_agl_ft=900)
    engine.state.aircraft = up
    engine.state.phase = "DEPARTURE"
    assert engine._facility_for(120.9) is tower  # in the air: the tower
    engine._last_handoff = (5.0, tower)
    assert engine._facility_for(120.9) is tower


def test_numbered_pieces_of_one_taxiway_in_a_row_are_said_as_that_taxiway():
    from localtc.atc_core.airport.taxi_route import _simplified

    assert _simplified(["G1", "G2", "G3"]) == ["G"]
    assert _simplified(["G", "G1", "G2", "B"]) == ["G", "B"]
    assert _simplified(["H1", "H2", "GE", "G1", "G2"]) == ["H", "GE", "G"]
    assert _simplified(["A", "A1"]) == ["A", "A1"]  # turning off A onto A1: said
    assert _simplified(["C3", "C", "C1"]) == ["C3", "C", "C1"]  # off the stand on C3, along C, onto C1
    assert _simplified(["B", "E", "C", "H", "GE"]) == ["B", "E", "C", "H", "GE"]


def test_the_descent_given_ahead_of_the_top_of_descent_is_when_ready():
    engine, own = parked_engine()
    library = engine.libraries["icao"]
    assert "when ready" in library.get("center.descend_star_pd").text[0]
    assert library.get("center.descend_star_pd").readback.required == library.get("center.descend_star").readback.required


def test_the_ils_clearance_in_europe_says_the_tower_to_call_when_established():
    engine, own = parked_engine()
    library = engine.libraries["icao"]
    assert library.get("approach.cleared").text[0].endswith("when established.")
    assert "when established" in library.get("approach.cleared_star_descend_tower").text[0]
    assert "frequency" in library.get("approach.cleared_star_descend_tower").readback.required


def test_an_emergency_is_cancelled_by_voice_and_never_taken_for_another():
    from test_engine import say, transmissions

    from localtc.atc_core.engine import CANCEL_EMERGENCY

    for said in ("Negative, negative. Cancel the emergency.", "cancelling mayday", "We're no longer an emergency",
                 "cancel pan pan"):
        assert CANCEL_EMERGENCY.search(said), said
    for said in ("Copy information.", "Mayday mayday, engine fire", "emergency equipment standing by?"):
        assert not CANCEL_EMERGENCY.search(said), said
    engine, own = parked_engine()
    say(engine, own, 5, "Mayday mayday, Skyhawk 172LT, engine fire")
    assert "emergency" in engine.state.flags
    (cancelled,) = transmissions(say(engine, own, 20, "Negative, negative, cancel the emergency, 172LT"))
    assert cancelled.instruction_id == "common.emergency_cancelled" and "emergency" not in engine.state.flags
