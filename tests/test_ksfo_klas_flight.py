"""Regressions from San Francisco to Las Vegas (2026-10-09, an A321 as Frontier 4158, the copilot on the intercom), with
the pilot's notes: the runway to cross named by its far end, and its crossing cleared from far away; most controllers
women; "radar contact, nice and easy now"; turbulence called for the aircraft pitching; an aircraft on the runway ahead
on the takeoff roll; departure's climbs 3,000 ft at a time; the departure frequency listed as approach; "heading 3500";
a garbled readback answered "read back the altitude"; a request for 26L read as a go-around; a second go-around for an
aircraft well down the runway; and the copilot's "say again?" to "time to start up our engines". And the ATIS source.
"""

import gzip
import json
from pathlib import Path

import msgspec
import pytest

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.atis.observation import Weather
from localtc.atc_core.engine import AtcEngine, _runways_cross
from localtc.atc_core.llm.phrase import PhraseError, check_reply
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.readback.extract import approaches, headings, opposite_end, values_equal
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Wind
from localtc.config import load_config, with_recorded
from localtc.crew.watches import Turbulence
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.sim_api import Airport, AirportData, AtisReport, OwnshipState, WeatherReport
from localtc.tts.persona import persona_for

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksfo_klas"
LAS_ATIS = ("LAS ATIS INFO W 1756Z. 00000KT 10SM SCT120 BKN180 29/03 A2987 (TWO NINER EIGHT SEVEN). VISUAL APPROACHES "
            "IN USE. LANDING RWYS 26L AND 19R. DEPG RWYS 26R, 19R AND 19L. ...ADVS YOU HAVE INFO W.")


@pytest.fixture(scope="module")
def flight() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def atc(lines: list[str], start: float = 0.0, end: float = 1e9) -> list[str]:
    return [line for line in lines if " ATC " in line and start <= at(line) <= end]


def airport(icao: str) -> Airport:
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt") as f:
        for line in f:
            row = json.loads(line)
            if row["type"] == "airport_data" and row["airport"]["icao"] == icao:
                return msgspec.convert(row["airport"], Airport)
    raise LookupError(icao)


def ownship(t: float) -> OwnshipState:
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt") as f:
        for line in f:
            row = json.loads(line)
            if row["type"] == "ownship_state" and row["t"] >= t:
                return msgspec.convert(row, OwnshipState)
    raise LookupError(t)


# --- the ground -----------------------------------------------------------------------------------------------------


def test_the_runway_to_cross_is_named_by_the_end_on_this_side(flight):
    """ "Hold short of 19R is correct, but on this side it's 01L." """
    taxi = next(line for line in flight if "San Francisco Ground" in line and "taxi via" in line)
    assert "hold short runway 01L" in taxi, taxi


def test_a_readback_naming_the_other_end_is_the_same_runway():
    assert opposite_end("01L") == "19R" and opposite_end("26") == "08" and opposite_end("36C") == "18C"
    assert values_equal("hold_short", "19R", "01L") and not values_equal("hold_short", "19L", "01L")


def test_the_crossing_comes_close_to_the_runway(flight):
    """ "When they called me to cross I wasn't even near it": 166 m from the hold line, still leaving the gate area."""
    crossing = next(line for line in flight if "San Francisco Ground" in line and "cross runway" in line)
    assert at(crossing) > 2540, crossing


def test_the_departure_frequency_is_listed_as_departure():
    """ "On departure it listed the departure frequency as the approach": the sim has NORCAL 120.35 as approach."""
    from localtc.app import engine_config
    from localtc.ui.controller import airport_summary

    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    engine = AtcEngine(engine_config(cfg.flight, cfg.atc))
    engine.state.flight.origin, engine.state.flight.destination = "KSFO", "KLAS"
    sfo = airport("KSFO")
    engine.handle(AirportData(t=0.0, airport=sfo))
    rows = {r["label"]: r["mhz"] for r in airport_summary(sfo, engine)["frequencies"]}
    assert rows["DEP"] == 120.35 and rows["APP"] != 120.35


# --- the takeoff ----------------------------------------------------------------------------------------------------


def test_runways_that_cross_and_parallels_that_dont():
    sfo = AirportGeometry(airport("KSFO"))
    by_name = {r.name: r for r in sfo.runways}
    crossing = [(a, b) for a in by_name for b in by_name if a < b and _runways_cross(by_name[a], by_name[b])]
    assert crossing and all({a[:2], b[:2]} != {a[:2]} for a, b in crossing)  # never a runway and its parallel
    assert not any("01L" in a and "01R" in b or "01R" in a and "01L" in b for a, b in crossing)


def test_no_takeoff_with_an_arrival_landing_across_the_runway(flight):
    """ "There was a plane on the runway ahead of me during the takeoff": 737s landing on 28R rolled out across 01R."""
    first = atc(flight, 2660.0, 2680.0)
    assert first and "hold short of runway 01R" in first[0] and "final" in first[0], first


def test_the_takeoff_roll_is_stopped_for_an_aircraft_about_to_cross_it(flight):
    assert any("stop immediately" in line for line in atc(flight, 2800.0, 2840.0))


def test_nothing_about_the_runway_once_airborne(flight):
    airborne = next(at(line) for line in flight if "TAKEOFF -> DEPARTURE" in line)
    assert not [line for line in atc(flight, airborne, airborne + 120) if "cleared for takeoff" in line or "hold short" in line]


# --- the climb ------------------------------------------------------------------------------------------------------


def test_departures_climbs_are_big_steps(flight):
    """ "The steps are too small, would really congest the ATC": 5,000, 8,000, 11,000, 15,000, 17,000."""
    climbs = {line.split("climb and maintain ")[1] for line in atc(flight, 2850.0, 3400.0)
              if "Norcal Departure" in line and "climb and maintain" in line}  # (one said again, unanswered)
    assert 1 <= len(climbs) <= 2, climbs


def test_no_nice_and_easy():
    from localtc.atc_core.personality import TRAITS

    assert not any("nice and easy" in habit for t in TRAITS.values() for habit in t.habits)


# --- the radio ------------------------------------------------------------------------------------------------------


def test_heading_3500_is_350():
    assert headings(normalize("Left heading 3500, vectors to final")) == [350]


def test_a_garbled_approach_readback_is_still_the_approach():
    found = approaches(normalize("Expect ILS young Yankee for the runway 26L from 241 to 58."))
    assert [(a.kind, a.suffix, a.runway) for a in found] == [("ILS", "Y", "26L")]


def test_the_model_cant_ask_for_a_readback_of_something_not_given():
    """The model's "read back the altitude" to a readback of the approach."""
    facts = {"waiting for the pilot to read back": "approach ILS Y RWY 26L"}
    with pytest.raises(PhraseError):
        check_reply('{"reply": "read back the altitude"}', facts, ("Frontier 4158",), beyond_facts=True)
    assert check_reply('{"reply": "read back the approach"}', facts, ("Frontier 4158",), beyond_facts=True)


def test_a_request_for_the_other_runway_isnt_a_go_around():
    heard = GrammarInterpreter().interpret(
        "Can we just do to the traffic on the ground? We're probably going to do another go around and we don't have "
        "enough fuel on board, so can we get an I-less for 26 left?", None, InterpretContext(phase="APPROACH"))
    assert heard.intent == "request_runway" and heard.values == {"runway": "26L", "approach": "ILS"}
    assert GrammarInterpreter().interpret("Going around, Frontier 4158", None, InterpretContext(phase="LANDING")).intent \
        == "going_around"


def test_no_airport_airport(flight):
    clearance = next(line for line in flight if "cleared to" in line and " ATC " in line)
    assert "Airport airport" not in clearance, clearance


# --- the landing ----------------------------------------------------------------------------------------------------


def test_no_go_around_for_landed_traffic_far_enough_down_the_runway(flight):
    """A 737 1,500 m down 26L at 40 kt with this flight 0.86 nm out: by the threshold it was 1,800 m on."""
    landing = next(at(line) for line in flight if "-> LANDING (short final runway 26L)" in line)
    assert not [line for line in atc(flight, landing - 5, landing + 80) if "go around" in line]


def test_a_departure_lined_up_on_the_threshold_still_means_going_around(flight):
    assert any("go around, traffic on the runway" in line for line in atc(flight, 7000.0, 7030.0))


# --- voices ---------------------------------------------------------------------------------------------------------


def test_controllers_are_mostly_men_as_in_the_job():
    """ "Most ATCs seem to be girls, not realistic: base it on statistics per region." About a fifth are women."""
    stations = [f"{city} {role}" for city in ("Seattle", "Boston", "Denver", "Phoenix", "Dallas", "Miami", "Chicago",
                                              "Atlanta", "Houston", "Portland", "Reno", "Tampa", "Austin", "Omaha",
                                              "Tulsa", "Fresno", "Boise", "Albany", "Mobile", "Toledo")
                for role in ("Tower", "Ground", "Approach", "Clearance", "Center")]
    women = sum(persona_for(s, "atc", locale="en-US").sex == "F" for s in stations) / len(stations)
    assert 0.08 <= women <= 0.32, women


# --- the copilot ----------------------------------------------------------------------------------------------------


def test_pitching_isnt_turbulence_in_this_flight():
    turb, said = Turbulence(), []
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt") as f:
        for line in f:
            row = json.loads(line)
            if row["type"] == "aircraft_systems" and row.get("g_force") is not None and row["t"] < 3100:
                if level := turb.add(row["t"], row["g_force"]):
                    said.append((round(row["t"]), level))
    assert not said, said  # "Getting bumpy, moderate turbulence" at 2,921 s: the aircraft levelling off


def test_a_remark_whose_model_reply_is_turned_away_gets_copy():
    from test_crew import FakeBackend, started
    from test_crew_monitor import said

    from localtc.crew.model import CrewModel
    from localtc.sim_api import IntercomHeard

    pm = started()
    pm.model = CrewModel(FakeBackend('{"kind": "command", "action": "light", "value": "on", "target": "landing", '
                                     '"reply": "Landing lights on."}'), mode="llm")
    assert said(pm.observe(IntercomHeard(t=2.0, text="Time to start up our engines."))) == ["Copy."]


# --- the ATIS source ------------------------------------------------------------------------------------------------


def atis_engine(mode: str) -> tuple[AtcEngine, OwnshipState]:
    from localtc.app import engine_config

    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    engine = AtcEngine(engine_config(cfg.flight, msgspec.structs.replace(cfg.atc, atis_source=mode)))
    engine.state.flight.origin, engine.state.flight.destination = "KSFO", "KLAS"
    for icao in ("KSFO", "KLAS"):
        engine.handle(AirportData(t=0.0, airport=airport(icao)))
    own = ownship(4500.0)  # the cruise, far from Las Vegas
    engine.state.aircraft = own
    engine.weather.update(ownship(10.0), engine.tracker.context_builder.airports)  # San Francisco's sampled
    return engine, own


def las(engine: AtcEngine, own: OwnshipState):
    engine._atis_now("KLAS", engine.geometry("KLAS"), own)
    return engine.current_atis("KLAS")


def test_real_atis_its_letter_runway_and_source():
    engine, own = atis_engine("real")
    engine.handle(AtisReport(t=own.t, icao="KLAS", letter="W", text=LAS_ATIS, zulu="1756"))
    info = las(engine, own)
    assert info.letter == "W" and info.runway == "26L" and info.source[0] == "real ATIS"
    assert engine.atis_source_text(info, own).startswith("real ATIS 1756Z, ")


def test_simulator_only_ignores_the_real_atis_and_metar():
    engine, own = atis_engine("sim")
    engine.handle(AtisReport(t=own.t, icao="KLAS", letter="W", text=LAS_ATIS, zulu="1756"))
    engine.handle(WeatherReport(t=own.t, icao="KLAS", wind_dir_true=190.0, wind_kt=7.0, altimeter_inhg=30.1))
    info = las(engine, own)
    assert info is None or (info.letter != "W" and info.source[0] == "simulator")


def test_hybrid_takes_the_metar_where_there_is_no_real_atis():
    engine, own = atis_engine("hybrid")
    engine.handle(WeatherReport(t=own.t, icao="KLAS", raw="METAR KLAS 091756Z 19007KT", observed="1756Z",
                                wind_dir_true=190.0, wind_kt=7.0, visibility_sm=10.0, altimeter_inhg=30.1))
    info = las(engine, own)
    assert info.source[0] == "METAR" and info.weather.wind.speed_kt == 7


def test_real_never_overrides_fresher_sim_conditions():
    """At the airport, the sim's wind (15 knots from 150) is the one flown in: the real ATIS (calm) isn't given over it,
    and the ATIS says the two differ."""
    engine, own = atis_engine("hybrid")
    engine.handle(AtisReport(t=own.t, icao="KLAS", letter="W", text=LAS_ATIS, zulu="1756"))
    engine.weather.samples["KLAS"] = Weather(wind=Wind(140, 15), wind_dir_true=150.0, t=own.t - 60, altimeter_inhg=29.87)
    info = las(engine, own)
    assert info.source[0].startswith("simulator") and "differs" in info.source[0] and info.letter != "W"
    assert info.weather.wind.speed_kt == 15


def test_a_real_runway_with_a_tailwind_in_the_sims_wind_isnt_used():
    engine, own = atis_engine("real")
    engine.handle(AtisReport(t=own.t, icao="KLAS", letter="W", text=LAS_ATIS.replace("00000KT", "08015KT"), zulu="1756"))
    info = las(engine, own)
    assert info.letter == "W" and info.runway != "26L"  # 15 knots from 080 is a tailwind on 26L
