"""Regressions from a support report: a C680 from Des Moines to Joplin (2026-10-09), the copilot reading back (assist).

The pilot's notes: the same squawk as two flights before; the ATIS's wind and runway behind the sim's; "requesting
engine startup" answered "read back the altitude"; taxiway P called A; no direct or heading after takeoff and a
long level-off at 15,000; Joplin's weather "not available"; no real vectors, no approach clearance and no handover to
the tower from the centre (Joplin has no approach controller); "vacate right Charlie1vDelta", read back letter by
letter; the first call to ground after landing answered "copy that"; the taxi in not to the GA ramp.
"""

import gzip
import json
from pathlib import Path

import msgspec
import pytest

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport.geometry import taxiway_name
from localtc.atc_core.airport.real_gates import GateData, name_taxiways
from localtc.atc_core.airport.taxi_route import TaxiGraph
from localtc.atc_core.atis import AtisBoard
from localtc.atc_core.atis.observation import Weather
from localtc.atc_core.engine import FREQUENCY_CHANGE
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.values import Wind
from localtc.config import load_config, with_recorded
from localtc.metar_data import parse
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.sim_api import Airport

FLIGHT = Path(__file__).parent / "fixtures" / "real_kdsm_kjln"
STAND = (41.529637, -93.657705)  # the GA stand at Des Moines it left from
# The METARs the user quoted: Joplin "12003KT", Des Moines "11009KT".
REPORTS = [
    {"type": "weather_report", "t": 300.0, "icao": "KJLN", "raw": "METAR KJLN 090853Z 12003KT 10SM CLR 18/15 A3001",
     "observed": "0853Z", "wind_dir_true": 120.0, "wind_kt": 3.0, "visibility_sm": 10.0, "altimeter_inhg": 30.01,
     "temperature_c": 18.0, "dewpoint_c": 15.0},
    {"type": "weather_report", "t": 300.0, "icao": "KDSM", "raw": "METAR KDSM 090854Z 11009KT 4SM -RA 18/16 A3004",
     "observed": "0854Z", "wind_dir_true": 110.0, "wind_kt": 9.0, "visibility_sm": 4.0, "altimeter_inhg": 30.04,
     "temperature_c": 18.0, "dewpoint_c": 16.0},
]


def gates(icao: str) -> GateData | None:
    path = FLIGHT / f"gates_{icao}.json"
    return GateData.from_json(json.loads(path.read_text())) if path.exists() else None


def replay(recording: Path, *, pinned: bool = False) -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    # The destination's runway as a flight now has it (recorded before, it replays with the wind where it was).
    atc = msgspec.structs.replace(cfg.atc, destination_weather=True, enforce_fpln_runways=pinned, atis_source="hybrid")
    scenario = Scenario(scenario=ScenarioMeta(recording=str(recording)), flight=cfg.flight, atc=atc)
    return run(scenario, recording, recording=recording, recorded_pilot=True, copilot="assist", gate_source=gates).lines


@pytest.fixture(scope="module")
def flight() -> list[str]:
    return replay(FLIGHT)


@pytest.fixture(scope="module")
def runway_05() -> list[str]:
    """As flown: off runway 05 (the flight plan's runway enforced), so the departure follows the recording."""
    return replay(FLIGHT, pinned=True)


@pytest.fixture(scope="module")
def with_metars(tmp_path_factory) -> list[str]:
    """The flight with the airports' METARs coming in, as the app now fetches them."""
    folder = tmp_path_factory.mktemp("kdsm_kjln_metar")
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt") as fin, gzip.open(folder / "session.jsonl.gz", "wt") as fout:
        added = False
        for line in fin:
            if not added and json.loads(line).get("t", 0.0) > 300.0:
                fout.writelines(json.dumps(r) + "\n" for r in REPORTS)
                added = True
            fout.write(line)
    return replay(folder)


def airport(icao: str) -> Airport:
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt") as f:
        for line in f:
            row = json.loads(line)
            if row["type"] == "airport_data" and row["airport"]["icao"] == icao:
                return msgspec.convert(row["airport"], Airport)
    raise LookupError(icao)


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def atc(lines: list[str], start: float = 0.0, end: float = 1e9) -> list[str]:
    return [line for line in lines if " ATC " in line and start <= at(line) <= end]


# --- the clearance --------------------------------------------------------------------------------------------------


def squawk_engine(*, per_flight: bool = True):
    from localtc.app import engine_config
    from localtc.atc_core.engine import AtcEngine

    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    return AtcEngine(engine_config(cfg.flight, msgspec.structs.replace(cfg.atc, squawk_per_flight=per_flight)))


def test_the_squawk_isnt_the_same_every_flight():
    """ "The squawk is repeating itself. I got the same squawk two flights ago": it came from the callsign alone."""
    own = next(e for e in Recording(FLIGHT).events() if type(e).__name__ == "OwnshipState")
    codes = set()
    for minute in (540, 600, 660, 720):
        engine = squawk_engine()
        engine.state.aircraft = msgspec.structs.replace(own, zulu_s=minute * 60.0)
        first = engine._squawk()
        engine.state.aircraft = msgspec.structs.replace(own, zulu_s=minute * 60.0 + 600)
        assert engine._squawk() == first  # asked again later in the flight: the same code
        codes.add(first)
    assert len(codes) > 1, codes


def test_a_recording_from_before_replays_with_its_code(flight):
    """Its pilot read back the code it was given: replayed, it gets that one again."""
    assert Recording(FLIGHT).header.config["atc"].get("squawk_per_flight") is None
    clearance = next(line for line in flight if "squawk" in line and " ATC " in line)
    assert "squawk 2612" in clearance, clearance


# --- the ground -----------------------------------------------------------------------------------------------------


def test_the_runway_changes_when_the_wind_swings_across_it():
    """The wind went from 070 to 110 at 16: runway 05 had 14 kt across it and 13 nearly into wind, and 05 stayed."""
    geo = AirportGeometry(airport("KDSM"))
    board = AtisBoard()
    before = board.update("KDSM", geo, Weather(wind=Wind(70, 17), wind_dir_true=70.0), 9 * 3600.0, "Des Moines", 0.0)
    assert before is not None and before.runway == "05"
    after = board.update("KDSM", geo, Weather(wind=Wind(110, 16), wind_dir_true=110.0), 9 * 3600.0 + 900, "Des Moines",
                         900.0)
    assert after is not None and after.runway == "13" and after.letter != before.letter


def test_a_crosswind_under_the_caution_keeps_the_runway():
    geo = AirportGeometry(airport("KDSM"))
    board = AtisBoard()
    board.update("KDSM", geo, Weather(wind=Wind(70, 17), wind_dir_true=70.0), 9 * 3600.0, "Des Moines", 0.0)
    assert board.update("KDSM", geo, Weather(wind=Wind(90, 12), wind_dir_true=90.0), 9 * 3600.0 + 900, "Des Moines",
                        900.0) is None or board.current["KDSM"].runway == "05"


def test_the_taxi_goes_to_the_runway_the_wind_favours(flight):
    taxi = next(line for line in flight if "Des Moines Ground" in line and "taxi to runway" in line)
    assert "runway 13" in taxi, taxi


def test_engine_startup_without_a_push():
    """ "Ground, NBV requesting engine startup", from a GA stand: "read back the altitude"."""
    heard = GrammarInterpreter().interpret("Ground NBV requesting engine startup.", None, InterpretContext(phase="PARKED"))
    assert heard.intent == "request_pushback" and heard.values.get("start_only")
    pushing = GrammarInterpreter().interpret("NBV request push and start.", None, InterpretContext(phase="PARKED"))
    assert pushing.intent == "request_pushback" and not pushing.values.get("start_only")


def test_start_up_is_approved(flight):
    answer = atc(flight, 742.0, 760.0)
    assert answer and "start up approved" in answer[0] and "push" not in answer[0], answer


def test_the_scenerys_taxiway_names_are_corrected_by_the_real_ones():
    """Des Moines's scenery calls taxiway P "A" and leaves most paths unnamed: "taxi via A" from a ramp on P."""
    geo = AirportGeometry(airport("KDSM"))
    named = name_taxiways(geo.airport, gates("KDSM"), geo.xy)
    assert named is not None
    renamed = AirportGeometry(named)
    route = TaxiGraph(renamed).departure_route(*STAND, renamed.end("05"))
    assert route is not None and "P" in route.taxiways, route.taxiways
    assert sum(1 for p in named.taxi_paths if p.name == "P") > 4


def test_the_scenerys_old_taxiway_names_follow_the_real_ones():
    """Des Moines renamed its taxiways; the scenery has the old names (its "A" is P now, its "B" D). Each renamed path
    takes the name of the taxiway it lies along, and the rest keep theirs."""
    geo = AirportGeometry(airport("KDSM"))
    named = name_taxiways(geo.airport, gates("KDSM"), geo.xy)
    changes = {(a.name, b.name) for a, b in zip(geo.airport.taxi_paths, named.taxi_paths) if a.name and a.name != b.name}
    assert changes and all(new.startswith({"A": "P", "B": "D"}[old]) for old, new in changes), changes
    assert sum(1 for a, b in zip(geo.airport.taxi_paths, named.taxi_paths) if a.name and a.name == b.name) > 80


def test_taxiways_spelled_out_by_the_scenery_are_letters():
    assert [taxiway_name(n) for n in ("Charlie", "Delta3", "Kelo", "Lim", "Charlie1vDelta", "Echo1", "A", "B12")] == \
        ["C", "D3", "K", "L", "C1", "E1", "A", "B12"]
    assert taxiway_name("Inner") == "Inner"  # not a spelled letter: as it is
    names = {p.name for p in AirportGeometry(airport("KJLN")).airport.taxi_paths}
    assert "Charlie" not in names and "C" in names and "Charlie1vDelta" not in names


# --- the departure --------------------------------------------------------------------------------------------------


def test_departure_sends_a_runway_heading_departure_direct(runway_05):
    """ "Does not clear me to a waypoint or a heading": radar contact, and nothing else."""
    contact = next(line for line in runway_05 if "Des Moines Departure" in line and "radar contact" in line)
    assert "proceed direct ANX" in contact, contact


def test_the_climb_goes_on_past_departures_last_step(runway_05):
    """ "Reached 15,000, did not get higher climb": level at 15,000 for four minutes, departure waiting for the centre
    and the centre for the handoff."""
    handoff = next(line for line in runway_05 if "Des Moines Departure" in line and "Center" in line and "contact" in line)
    assert at(handoff) < 1790, handoff  # before the level-off at 15,000 (1,795 s), still climbing
    climbs = [line for line in atc(runway_05, 1500, at(handoff)) if "Des Moines Departure" in line and "climb" in line]
    assert len(climbs) == 1 and "17,000" in climbs[0], climbs  # departure's whole climb in one go


# --- the arrival ----------------------------------------------------------------------------------------------------


def test_the_destination_runway_isnt_chosen_from_the_wind_up_high(flight):
    """With nothing known of Joplin's weather, "expect RNAV runway 31": the wind at FL300. Now the calm-wind runway."""
    descent = next(line for line in flight if "Kansas City Center" in line and "expect" in line and "approach" in line)
    assert "runway 13" in descent, descent


def test_the_destinations_weather_from_its_metar(with_metars):
    """ "What is the weather at the airport?" in the cruise was given Des Moines's; at the destination, "not
    available"."""
    answers = atc(with_metars, 2924.0, 2975.0)
    assert len(answers) == 3, answers
    assert all("Joplin Regional wind 120 at 3" in a and "altimeter 30.01" in a for a in answers), answers


def test_the_expected_approach_from_the_metar(with_metars):
    descent = next(line for line in with_metars if "Kansas City Center" in line and "expect" in line and "approach" in line)
    assert "runway 13" in descent, descent


def test_no_weather_for_a_far_airport_is_not_the_wind_up_here(flight):
    answers = atc(flight, 2924.0, 2975.0)
    assert answers and all("wind 300" not in a for a in answers), answers


def test_the_centre_works_the_approach_where_there_is_no_approach_controller(flight):
    """Joplin has no approach: Kansas City Center gave one heading, then "check heading" as the pilot joined the final
    by themselves, and the pilot switched to tower unasked."""
    cleared = next(line for line in flight if "Kansas City Center" in line and "cleared ILS runway 13 approach" in line)
    assert "until established" in cleared, cleared
    tower = next(line for line in flight if "Kansas City Center" in line and "contact Joplin Tower" in line)
    assert at(cleared) < at(tower) < 4161, tower  # before the pilot tuned it themselves
    assert not [line for line in atc(flight, 3990.0, 4130.0) if "check heading" in line]


def test_asking_for_the_tower_is_a_frequency_change():
    assert FREQUENCY_CHANGE.search("NBV on the ILS requesting radio to the tower.").group(1) == "tower"
    assert FREQUENCY_CHANGE.search("request frequency change to tower").group(1) == "tower"
    assert FREQUENCY_CHANGE.search("NBV requesting vectors for the ILS.") is None


# --- after landing --------------------------------------------------------------------------------------------------


def test_the_exit_is_said_as_a_taxiway(flight):
    vacate = next(line for line in flight if "vacate" in line and " ATC " in line)
    assert "vacate right C1," in vacate, vacate
    readback = next(line for line in flight if " PILOT " in line and "Vacate" in line)
    assert "charlie one" in readback and "hotel alpha romeo" not in readback, readback


def test_no_runway_crossing_left_over_from_the_departure(flight):
    """Just off Joplin's 13, ground cleared the flight across "runway 13": Des Moines's 13/31, crossed on the way
    out."""
    assert not [line for line in atc(flight, 4350.0, 4400.0) if "cross runway 13" in line]


def test_the_first_call_to_ground_gets_the_taxi(flight):
    """ "Ground, good evening, NBV on Charlie": "copy that"."""
    answer = atc(flight, 4398.0, 4410.0)
    assert answer and "taxi to the general aviation ramp via C, D, B" in answer[0], answer


def test_asked_again_the_taxi_is_repeated_without_a_new_readback(flight):
    after = atc(flight, 4418.0, 4545.0)
    assert after and "taxi to the general aviation ramp" in after[0], after
    assert not [line for line in after if "how do you read" in line or "say again" in line], after


def test_a_ga_flight_goes_to_the_ga_ramp():
    geo = AirportGeometry(airport("KJLN"))
    graph = TaxiGraph(geo)
    route = graph.parking_route(37.1588, -94.4974, kind="ramp_ga")
    spot = next(p for p in geo.airport.parking if ("parking", p.index) == route.nodes[-1])
    assert spot.kind.startswith("ramp_ga"), spot


# --- the app's METAR ------------------------------------------------------------------------------------------------


def test_a_metar_row_is_read():
    report = parse({"icaoId": "KJLN", "wdir": 190, "wspd": 7, "visib": "10+", "altim": 1019.4, "temp": 27.8,
                    "dewp": 15, "rawOb": "METAR KJLN 091553Z 19007KT 10SM CLR 28/15 A3010"})
    assert (report.icao, report.wind_dir_true, report.wind_kt, report.visibility_sm, report.altimeter_inhg,
            report.observed) == ("KJLN", 190.0, 7.0, 10.0, 30.1, "1553Z")
    variable = parse({"icaoId": "KJLN", "wdir": "VRB", "wspd": 3, "altim": 1013.2, "rawOb": ""})
    assert variable.wind_dir_true is None and variable.altimeter_inhg == 29.92
