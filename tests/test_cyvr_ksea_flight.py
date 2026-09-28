"""Vancouver to Seattle (27 September 2026, an A220-300 as Air Canada 8272, on LocalTC 0.3.5), with the pilot's notes:

- "We'd like to get a taxi for the departure" got "contact tower"; after that every call on ground, "how are you
  doing today?" included, got "contact tower" again, even after ground had given the taxi.
- At the hold short of 08R (crossed to get to 31), "request to cross runway 8R" got "line up and wait runway 31",
  and the takeoff clearance came before the aircraft was anywhere near runway 31.
- "Give us a little more time" got "unable, information not available".
- The arrival was always the ILS; the altimeter changed as the aircraft flew; approach said "descend and maintain
  5,000" to a pilot descending via the STAR; "maintain 250 knots" to an aircraft doing 280.
- Tower cleared the landing, then let an A350 line up and take off in front of it on a mile-and-a-half final:
  no go-around. And the gate was "B251".
"""

from pathlib import Path

import pytest

from localtc.atc_core.airport import approaches
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback import (
    GrammarInterpreter,
    InterpretContext,
    PendingReadback,
)
from localtc.atc_core.values import Approach, Callsign
from localtc.atc_core.weather import WeatherTracker
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_cyvr_ksea"
LIBRARY = TemplateLibrary.load()
ACA = Callsign.named("ACA8272")


def scenario(**meta) -> Scenario:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    return Scenario(scenario=ScenarioMeta(recording=str(FLIGHT), **meta), flight=cfg.flight, atc=cfg.atc)


@pytest.fixture(scope="module")
def recorded():
    return run(scenario(), FLIGHT, recording=FLIGHT, recorded_pilot=True, recorded_copilot=True).lines


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def answer_to(lines: list[str], words: str) -> str:
    start = next(i for i, line in enumerate(lines) if "PILOT" in line and words in line)
    return next(line for line in lines[start + 1:] if " ATC " in line)


def heard(text: str, waiting: PendingReadback | None = None, phase: str = "TAXI_OUT"):
    return GrammarInterpreter().interpret(text, waiting, InterpretContext(callsign=ACA, phase=phase))


def pending(instruction: str, **slots) -> PendingReadback:
    r = LIBRARY.render(instruction, {**slots, "callsign": ACA})
    return PendingReadback(instruction, instruction.split(".")[0], r.expected, r.required, r.optional)


# --- ground: the taxi, and no "contact tower" loop ---------------------------------------------------------------

def test_a_taxi_for_the_departure_is_a_taxi(recorded):
    assert heard("Air Canada 8272. We'd like to get a taxi for the departure.", phase="PARKED").intent == "ready_to_taxi"
    assert "taxi to runway 31" in answer_to(recorded, "get a taxi for the departure")


def test_ground_never_sends_the_flight_to_tower_while_it_taxis(recorded):
    ground = [line for line in atc(recorded) if "Vancouver Ground" in line]
    assert not [line for line in ground if "contact Vancouver Tower" in line]
    assert "thank" in answer_to(recorded, "How are you doing today?").lower() \
        or "good" in answer_to(recorded, "How are you doing today?").lower()


def test_contact_clearance_is_a_readback_not_an_ifr_request(recorded):
    assert heard("Contact Clearance, Air Canada 8272.", phase="PARKED").intent != "request_ifr_clearance"
    contacts = [line for line in atc(recorded) if "contact Vancouver Clearance" in line]
    assert len(contacts) == 1


# --- tower: crossing, and nobody cleared from somewhere else ------------------------------------------------------

def test_a_crossing_asked_for_is_a_crossing(recorded):
    assert heard("Tower, Air Canada 8272. Request to cross runway 8R.").intent == "request_crossing"
    assert "cross runway 08R" in answer_to(recorded, "Request to cross runway 8R")
    assert not [line for line in atc(recorded) if "line up and wait" in line and at(line) < 1400]


@pytest.mark.parametrize("text", [
    ("Roger, we're not quite there yet Air Canada 8272, so if we can get a bit more time before this instruction, "
     "that would be great."),
    ("Just to find, sorry, we're encountering a little bit of issues here at the flight deck. Give us a little more "
     "time and yeah, sorry about that."),
])
def test_needing_more_time_gets_advise_when_ready(text, recorded):
    assert heard(text).intent == "need_time"
    assert "advise when ready" in answer_to(recorded, text[:40])


def test_no_takeoff_clearance_before_the_runway(recorded):
    takeoffs = [line for line in atc(recorded) if "cleared for takeoff" in line]
    assert takeoffs and at(takeoffs[0]) > 1500  # at runway 31, not at 08R's hold line (1316) or on the way


# --- the arrival: the approach to expect, and the words for it -----------------------------------------------------

def test_good_weather_in_the_us_is_the_visual(recorded):
    expected = [line for line in atc(recorded) if "expect" in line and "approach" in line]
    assert expected and all("expect visual approach runway 34R" in line for line in expected), expected
    assert "cleared visual approach runway 34R" in "".join(atc(recorded))
    assert not [line for line in atc(recorded) if "visual" in line and "localizer" in line]


@pytest.mark.parametrize(("region_faa", "vis", "precip", "expected"), [
    (True, 10.0, "", "VISUAL"),  # the US or Canada in good weather
    (True, 2.0, "", "ILS"),  # low visibility: the ILS
    (True, 10.0, "rain", "ILS"),  # rain: the ILS
    (True, None, "", "ILS"),  # weather unknown: the instrument approach
    (False, 10.0, "", "ILS"),  # ICAO: the instrument approach whatever the weather
])
def test_which_approach(region_faa, vis, precip, expected):
    from localtc.sim_api import Airport, ApproachProcedure

    airport = Airport(icao="KSEA", lat=47.45, lon=-122.31, elev_ft=433,
                      approaches=(ApproachProcedure(kind="ils", runway="34R"), ApproachProcedure(kind="rnav", runway="34R")))
    assert approaches.select_approach(airport, "34R", has_ils=True, visibility_sm=vis, visual_first=region_faa,
                                      precip=precip) == expected


def test_the_arrival_runway_in_the_cruise_is_seattles(recorded):
    assert "expect visual approach runway 34R" in answer_to(recorded, "estimated arrival runway to Seattle")


def test_approach_lets_a_descend_via_carry_on(recorded):
    checkin = answer_to(recorded, "Descending via the M-A-R-N-R-8 arrival")
    assert "descend via the MARNR8 arrival" in checkin and "descend and maintain" not in checkin


def test_speeds_are_reductions(recorded):
    speeds = [line for line in atc(recorded) if "knots" in line]
    assert speeds and all("reduce speed to" in line for line in speeds)


def test_an_airports_altimeter_stays_put_until_measured_there():
    weather = WeatherTracker()
    weather.altimeter_inhg = 29.90  # 90 nm north, in the cruise
    assert weather.altimeter_at("KSEA") == 29.90
    weather.altimeter_inhg = 29.96  # closer in, the air's pressure is different
    assert weather.altimeter_at("KSEA") == 29.90  # still the one given


def test_seattles_altimeter_is_one_number(recorded):
    import re

    said = {m.group(1) for line in atc(recorded) if "Seattle" in line and (m := re.search(r"altimeter (\d\d\.\d\d)", line))}
    assert len(said) == 1, said


# --- the landing: go around for an aircraft on the runway ---------------------------------------------------------

def test_go_around_for_the_a350_lining_up(recorded):
    go = [line for line in atc(recorded) if "go around" in line]
    assert go and 3900 < at(go[0]) < 3935  # THY204 lined up on 34R with the flight 1.5 nm out
    assert "contact Seattle Approach" in go[0]  # and back to approach for another try


def test_tower_clears_nobody_onto_the_runway_with_the_flight_on_final():
    from localtc.sim_api import RadioChatter

    result = run(scenario(), FLIGHT, recording=FLIGHT, recorded_pilot=True, recorded_copilot=True, chatter=True,
                 speech_s_per_char=0.06)
    on_final = [o for o in result.outputs if isinstance(o, RadioChatter) and o.station == "Seattle Tower"
                and o.speaker == "atc" and 3700 < o.t < 3990]
    assert not [o for o in on_final if "takeoff" in o.text or "line up" in o.text], [o.text for o in on_final]


# --- the gate ------------------------------------------------------------------------------------------------------

def test_the_gate_is_one_with_a_gates_name(recorded):
    gate = answer_to(recorded, "Request gate.")
    assert "Gate B" in gate and "B251" not in gate and "B249" not in gate


def test_odd_stand_numbers():
    from localtc.atc_core.airport.gates import Gate, odd_number
    from localtc.sim_api import ParkingSpot

    def gate(label: str) -> Gate:
        return Gate(ParkingSpot(index=0, name=f"GATE {label[0]} {label[1:]}", kind="gate_medium", lat=0, lon=0), "gate", label)

    b = [gate(x) for x in ("B7", "B9", "B12", "B251", "B249")]
    assert [odd_number(g, b) for g in b] == [False, False, False, True, True]
    a = [gate(x) for x in ("A101", "A102", "A103")]  # an airport that numbers them all that way
    assert not any(odd_number(g, a) for g in a)


# --- speech-to-text ------------------------------------------------------------------------------------------------

def test_readbacks_through_speech_to_text():
    clearance = pending("clearance.ifr", destination="Seattle-Tacoma International", altitude=5000, cruise=13000,
                        minutes=7, frequency=126.125, squawk="3305")
    text = ("Clearance Seattle International is filed. Climb maintain 5000, expect 13,000, 7 minutes under departure. "
            "Departure frequency is on 126 decimal, 125, squawk 3305.")
    assert heard(text, clearance, "PARKED").status == "correct"
    via = pending("center.descend_via", procedure="MARNR8", approach=Approach("VISUAL", "34R"))
    assert heard("This on via the M.A.R.N.R. 8 arrival. Air Canada 8272.", via, "CRUISE").status == "correct"
    ground = pending("clearance.readback_correct_ground", station="Vancouver Ground", frequency=121.7)
    assert heard("Contact Ground on 121771 Ready Air Canada 8272.", ground, "PARKED").status == "unclear"
    assert heard("Seattle Centre, Air Canada 8272, cruising at 13,000 feet.", None, "CRUISE").intent == "checkin"
    assert heard("Continue approach for 3, 4, right, Air Canada 8272.", None, "APPROACH").intent == "acknowledge"
