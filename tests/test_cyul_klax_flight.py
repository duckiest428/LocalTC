"""Regressions from the Montreal to Los Angeles flight (2026-09-24), a CS300 flown by voice as Air Canada 779 and
cut short in the cruise over Chicago, with the notes the pilot marked on the way, and what came of them:
standard pressure up in the flight levels, handoffs that wanted the frequency read back, a check-in loop, a
takeoff clearance with a 777 over the threshold, the ride asked after twice, ATC talking with the sim paused.
Also: the stepped climb, callsigns, radio range, the other traffic on the frequency and the model's patience.
"""

import random
import re
from pathlib import Path

import msgspec
import pytest

from localtc.atc_core import chatter, personality, radio_range
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.llm import LlmInterpreter
from localtc.atc_core.llm.backend import LlmReply
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback.callsign_check import judge
from localtc.atc_core.readback.intents import match_intents
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign, Wind
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AtcAlert,
    AtcTransmission,
    OwnshipState,
    RadioChatter,
    Transcript,
)
from localtc.tts.voices import delivery_for

FLIGHT = Path(__file__).parent / "fixtures" / "real_cyul_klax"


def scenario(**meta) -> Scenario:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    return Scenario(scenario=ScenarioMeta(recording=str(FLIGHT), **meta), flight=cfg.flight, atc=cfg.atc)


@pytest.fixture(scope="module")
def recorded():
    """The flight as flown: the pilot's own calls, judged by today's ATC."""
    return run(scenario(), FLIGHT, recording=FLIGHT, recorded_pilot=True)


@pytest.fixture(scope="module")
def copilot():
    """The copilot on the radio, going wherever ATC sends it."""
    return run(scenario(copilot="full"), FLIGHT, recording=FLIGHT)


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def said_after(lines: list[str], words: str, n: int = 1) -> list[str]:
    start = next(i for i, line in enumerate(lines) if words in line)
    return [line for line in lines[start + 1:] if " ATC " in line][:n]


# --- standard pressure -----------------------------------------------------------------------------------------

def test_standard_pressure_in_the_flight_levels_is_not_an_altitude_deviation(recorded):
    """The CS300's avionics on STD at FL360 while the sim's altimeter setting stayed 30.42: "36,447 ft"."""
    alerts = [o for o in recorded.outputs if isinstance(o, AtcAlert) and o.kind == "altitude_deviation"]
    assert alerts == []
    assert not [line for line in atc(recorded.lines) if "check altitude" in line]


@pytest.mark.parametrize("text", ["we are on standard", "Air Canada 779, keep in mind we're on STD", "set to QNE",
                                  "altimeter two niner niner two, we're at 36,000"])
def test_standard_is_something_a_pilot_says(text):
    assert "report_standard" in {m.intent for m in match_intents(normalize(text))}


# --- handoffs, check-ins -----------------------------------------------------------------------------------------

def test_a_handoff_taken_without_the_frequency_is_taken(recorded):
    """ "Montreal Centre. Have a good night. Air Canada 779." got "say again"."""
    answer = said_after(recorded.lines, "Montreal Centre. Have a good night")
    assert not answer or "say again" not in answer[0] or at(answer[0]) > 3340


def test_the_check_in_while_climbing_says_continue_climb(recorded):
    """ "Radar contact, FL360" to a flight passing FL185 isn't an instruction; now it's the next step of the climb
    (or "continue climb" when it's already on its way to what it has)."""
    answer = said_after(recorded.lines, "Flight level 185. Climbing 360")[0]
    assert ("continue climb" in answer or "climb and maintain" in answer) and "radar contact, FL360" not in answer


def test_saying_the_altitude_again_is_not_another_check_in(recorded):
    """ "Commenting flight level 360" after the check-in: acknowledged, not the check-in again and again."""
    toronto = [line for line in atc(recorded.lines) if "Toronto Centre 135.55" in line and 4170 < at(line) < 4260]
    assert len(toronto) == 1, toronto


def test_greetings_only_with_the_first_word(recorded):
    """Tower held the aircraft short first; "good afternoon" with the takeoff clearance came late."""
    tower = [line for line in atc(recorded.lines) if "Montreal Tower" in line]
    assert tower and not any("good afternoon" in line or "good evening" in line for line in tower[1:])


def test_the_ride_is_asked_after_once_a_flight(copilot):
    rides = [line for line in atc(copilot.lines) if "ride" in line]
    assert len(rides) <= 1, rides


# --- the departure ----------------------------------------------------------------------------------------------

def test_no_takeoff_clearance_with_the_777_over_the_threshold(recorded):
    """Lufthansa 474 at 150 ft over 06L at 2725 s: neither on final nor on the ground yet."""
    cleared = [line for line in atc(recorded.lines) if "cleared for takeoff" in line and at(line) < 2760]
    assert cleared == []


def test_no_caution_for_an_aircraft_parked_at_its_gate(recorded):
    """ "MD80 stopped ahead of you" just off the push: it was at its gate."""
    assert not [line for line in atc(recorded.lines) if "stopped ahead" in line or "stopped on the taxiway" in line]


def test_the_taxi_to_06l_goes_by_c_not_the_sims_second_a4(recorded):
    """MSFS names the connector to 06L "A4" too (A4 is 06R's): the shipped correction unnames it and holds 06L on C."""
    taxi = said_after(recorded.lines, "Request taxi")[0]
    assert "06L" in taxi and "via G, C" in taxi and "A4" not in taxi


def test_a_numbered_taxiway_in_two_places_isnt_named_without_a_correction():
    from localtc.atc_core.airport.taxi_route import TaxiGraph
    from localtc.atc_core.phase.context import ContextBuilder
    from localtc.sim_api import AirportData

    events = list(Recording(FLIGHT).events())
    airport = next(e.airport for e in events if isinstance(e, AirportData) and e.airport.icao == "CYUL")
    own = next(e for e in events if isinstance(e, OwnshipState))
    builder = ContextBuilder()
    builder.fixes = {}  # the sim's data as it is: A4 at 06R, and "A4" on the way to 06L
    geo = builder.add_airport(airport)
    route = TaxiGraph(geo).departure_route(own.lat, own.lon, geo.end("06L"))
    assert "A4" not in route.taxiways
    assert not [p for p in geo.airport.taxi_paths if p.name == "A4"]  # neither is said: nobody knows which is real
    assert [p for p in geo.airport.taxi_paths if p.name == "B"]  # lettered taxiways in pieces keep their names


def test_the_pilots_own_corrections_win(tmp_path):
    from localtc.atc_core.airport import fixes

    mine = tmp_path / "airport_fixes.toml"
    mine.write_text('[CYUL]\nhold = { "06L" = "G" }\n[KSEA]\nrename = [{ taxiway = "A", near = "16L", name = "Alpha" }]\n')
    loaded = fixes.load(mine)
    assert loaded["CYUL"].holds == {"06L": "G"} and loaded["CYUL"].renames  # the shipped rename stays
    assert loaded["KSEA"].renames[0].name == "Alpha"


def test_the_climb_goes_up_in_steps(copilot):
    """Departure to the top of its airspace, then the centre's step, then the cruise."""
    ups = []
    for line in atc(copilot.lines):
        if (m := re.search(r"climb and maintain (FL(\d{3})|(\d{1,2}),(\d{3}))", line)) is not None:
            feet = int(m.group(2)) * 100 if m.group(2) else int(m.group(3) + m.group(4))
            if feet > 5000:
                ups.append(feet)
    assert ups[0] == 17000 and 23000 <= ups[1] <= 28000 and ups[-1] == 36000, ups


# --- the sim paused ---------------------------------------------------------------------------------------------

def test_nothing_from_atc_while_the_sim_is_paused(copilot):
    """ "Advise if you can accept FL380" came in the pause from 7992 to 8640 s."""
    paused = [line for line in atc(copilot.lines) if 7995 < at(line) < 8640]
    assert paused == []


# --- callsigns ---------------------------------------------------------------------------------------------------

AC779 = Callsign("ACA779", telephony="Air Canada", flight_number="779")


@pytest.mark.parametrize(("text", "verdict"), [
    ("Air Canada's a 779. Request pushback at startup.", "ours"),  # speech-to-text, as heard
    ("Roger that at Canada 779.", "ours"),
    ("Air Canada 79, request taxi", "ours"),  # a digit lost
    ("Taxi runway 6L, A4.", "absent"),  # no callsign on a readback: nothing to say about it
    ("Air Canada 797, request taxi", "close"),  # two digits swapped
    ("Air Canada 452, request taxi", "other"),
    ("Westjet 452, request taxi", "other"),
    ("Taxi via delta 5, Air Canada 779", "ours"),  # "Delta 5" is a taxiway, not Delta Air Lines
])
def test_whose_callsign(text, verdict):
    assert judge(normalize(text), AC779) == verdict


def test_another_aircrafts_call_is_not_answered_as_ours():
    engine, own = parked_at_montreal()
    ground = engine.facility("ground")
    engine.handle(msgspec.structs.replace(own, t=own.t + 1, com1_mhz=ground.mhz))
    out = engine.handle(Transcript(t=own.t + 2, text="Montreal Ground, Westjet 452, request taxi", source="typed"))
    out += engine.handle(msgspec.structs.replace(own, t=own.t + 6, com1_mhz=ground.mhz))
    said = [o for o in out if isinstance(o, AtcTransmission)]
    assert [o.instruction_id for o in said] == ["common.station_say_again"]
    assert "say again your callsign" in said[0].text


# --- radio range ------------------------------------------------------------------------------------------------

def test_radio_horizon_and_service_volumes():
    assert radio_range.horizon_nm(3000, 80) == pytest.approx(78.4, abs=0.5)  # 1.23 (sqrt 3000 + sqrt 80)
    ground = radio_range.reach("ground", 4.0, 0.0)
    assert ground.in_range and not radio_range.reach("ground", 8.0, 0.0).in_range  # ground: a few miles
    tower_far = radio_range.reach("tower", 20.0, 0.0)  # on the ground at another airport 20 nm away
    assert not tower_far.in_range
    assert radio_range.reach("tower", 25.0, 3000.0, size=1).in_range  # up at 3,000: 15 nm protected, 30 usable
    assert not radio_range.reach("approach", 150.0, 35000.0, size=2).in_range
    assert radio_range.reach("center", 900.0, 0.0) is None  # centres: their own airspace, remote sites all over it
    fading = radio_range.reach("tower", 24.0, 5000.0)
    assert 0.3 <= fading.readability < 1.0


def test_calling_a_tower_out_of_range_gets_silence_and_a_word_why():
    engine, own = parked_at_montreal()
    tower = engine.facility("tower")
    far = msgspec.structs.replace(own, t=own.t + 1, lat=own.lat + 1.0, com1_mhz=tower.mhz)  # 60 nm north, on the ground
    engine.handle(far)
    out = engine.handle(Transcript(t=far.t + 1, text="Montreal Tower, Air Canada 779, request departure", source="typed"))
    out += engine.handle(msgspec.structs.replace(far, t=far.t + 8))
    assert not [o for o in out if isinstance(o, AtcTransmission)]
    alert = next(o for o in out if isinstance(o, AtcAlert))
    assert alert.kind == "out_of_range" and "Montreal Tower" in alert.detail


# --- other traffic on the frequency ------------------------------------------------------------------------------

def test_chatter_is_other_aircraft_in_the_airports_own_words():
    c = chatter.Chatter(TemplateLibrary.load(), seed=3)
    scene = chatter.Scene("tower", "Montreal Tower", runway="06L", wind=Wind(60, 6), exclude="ACA779")
    for _ in range(20):
        lines = c.exchange(scene)
        assert lines and lines[0].speaker == "atc" and "06L" in lines[0].text
        assert "779" not in lines[0].callsign or "Air Canada" not in lines[0].callsign
        if len(lines) > 1:
            assert lines[1].speaker == "pilot" and "six left" in lines[1].spoken


def test_chatter_fills_the_quiet_and_never_the_pilots_own_exchanges():
    result = run(scenario(copilot="full"), FLIGHT, recording=FLIGHT, speech_s_per_char=0.06, chatter=True)
    heard = [o for o in result.outputs if isinstance(o, RadioChatter)]
    assert len(heard) > 40  # a busy airport and three hours of frequencies
    assert {o.speaker for o in heard} == {"atc", "pilot"}
    # Nothing it says is taken as the pilot's, or waited on: the flight goes as it did.
    assert not [o for o in result.outputs if isinstance(o, AtcAlert) and o.kind == "readback_unresolved"]


# --- how each controller sounds ----------------------------------------------------------------------------------

def test_each_station_keeps_its_own_wording():
    picks = {personality.wording("Montreal Ground", "ground.taxi_out", 2, None) for _ in range(5)}
    assert len(picks) == 1  # the same controller, the same habit
    stations = [f"{city} Tower" for city in ("Montreal", "Toronto", "Denver", "Seattle", "Phoenix", "Boston")]
    assert len({personality.wording(s, "tower.takeoff", 3, None) for s in stations}) > 1  # but not everybody's
    rng = random.Random(1)
    usual = personality.wording("Montreal Ground", "ground.taxi_out", 2, None)
    drifted = [personality.wording("Montreal Ground", "ground.taxi_out", 2, rng) for _ in range(200)]
    assert 0.8 < drifted.count(usual) / 200 < 1.0  # now and then another way of saying it


def test_the_voices_have_a_manner():
    tower, center, atis = delivery_for("Montreal Tower", "tower"), delivery_for("Montreal Centre", "center"), \
        delivery_for("Montreal ATIS", "atis")
    assert tower.pace > center.pace  # tower quick, centre measured
    assert atis.noise_scale < 0.3 and atis.noise_w < 0.3  # the recording
    assert delivery_for("Montreal Tower", "tower") == tower  # the same every time


# --- the model gets its time --------------------------------------------------------------------------------

class SlowThenFast:
    """A model that misses the first deadline and answers the second, patient one."""

    model = "slow"

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def complete(self, request, *, timeout_s: float) -> LlmReply:
        self.timeouts.append(timeout_s)
        if timeout_s < 10:
            return LlmReply(None, timeout_s * 1000, "timeout")
        return LlmReply('{"kind":"question","intent":"","topic":"other"}', 7000.0)


def test_off_script_gets_stand_by_then_the_patient_answer():
    backend = SlowThenFast()
    engine, own = parked_at_montreal(interpreter=LlmInterpreter(backend, mode="fallback", timeout_s=2.5, patience_s=15.0))
    ground = engine.facility("ground")
    engine.handle(msgspec.structs.replace(own, t=own.t + 1, com1_mhz=ground.mhz))
    first = engine.handle(Transcript(t=own.t + 2, text="Air Canada 779, what's the story with the de-icing today?",
                                     source="typed"))
    assert [o.instruction_id for o in first if isinstance(o, AtcTransmission)] == ["common.stand_by"]
    assert engine.deferred is not None
    later = engine.resolve_deferred()
    later += engine.handle(msgspec.structs.replace(own, t=own.t + 12, com1_mhz=ground.mhz))
    assert backend.timeouts[-1] == pytest.approx(15.0, abs=0.01)  # the second try had its patience
    assert any(isinstance(o, AtcTransmission) and o.instruction_id != "common.say_again" for o in later)


# --- helpers ----------------------------------------------------------------------------------------------------

def parked_at_montreal(**kwargs) -> tuple[AtcEngine, OwnshipState]:
    engine = AtcEngine(EngineConfig(destination="KLAX", cruise_ft=36000, callsign="ACA779", seed=4), **kwargs)
    own = None
    for event in Recording(FLIGHT).events():
        if event.t > 1300:
            break
        if isinstance(event, SIM_EVENT_TYPES):
            engine.handle(event)
            if isinstance(event, OwnshipState):
                own = event
    return engine, own


# --- runways: the one in use, or the plan's ----------------------------------------------------------------------

def taxi_runway(result) -> str:
    line = next(line for line in atc(result.lines) if "taxi" in line and "runway" in line)
    return re.search(r"runway (\w+)", line).group(1)


def test_runways_come_from_the_atis_unless_the_plan_is_enforced(copilot):
    """The SimBrief plan filed 06R; Montreal's ATIS had 06L in use. By default ATC gives 06L."""
    assert taxi_runway(copilot) == "06L"
    s = scenario(copilot="full")
    s.flight.dep_runway, s.flight.arr_runway = "06R", "24R"
    assert taxi_runway(run(s, FLIGHT, recording=FLIGHT)) == "06L"  # the plan alone changes nothing
    s.atc.enforce_fpln_runways = True
    assert taxi_runway(run(s, FLIGHT, recording=FLIGHT)) == "06R"


def test_an_enforced_runway_the_airport_lacks_falls_back_to_the_atis():
    s = scenario(copilot="full")
    s.flight.dep_runway = "33X"
    s.atc.enforce_fpln_runways = True
    assert taxi_runway(run(s, FLIGHT, recording=FLIGHT)) == "06L"
