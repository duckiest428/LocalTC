"""Regressions from the Denver to Seattle flight (2026-09-23), a CS300 flown by voice, with the notes the pilot
marked on the way: centres that never changed, poor vectors, a broken ATIS, taxi readbacks that could not be
got right, an aircraft stopped on the taxiway ahead, and a gate that could not be asked for.
"""

import gzip
import json
from pathlib import Path

import msgspec
import pytest

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback import (
    GrammarInterpreter,
    InterpretContext,
    PendingReadback,
)
from localtc.atc_core.readback.intents import match_intents, resolve
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.sim_api import SIM_EVENT_TYPES, Airport, AtisBroadcast

FLIGHT = Path(__file__).parent / "fixtures" / "real_kden_ksea"


def scenario(**meta) -> Scenario:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    return Scenario(scenario=ScenarioMeta(recording=str(FLIGHT), **meta), flight=cfg.flight, atc=cfg.atc)


@pytest.fixture(scope="module")
def copilot() -> list[str]:
    """The whole flight with the copilot on the radio: it goes wherever ATC sends it."""
    return run(scenario(copilot="full"), FLIGHT, recording=FLIGHT).lines


@pytest.fixture(scope="module")
def recorded() -> list[str]:
    """The flight as flown, the pilot's own calls replayed."""
    return run(scenario(), FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


# --- the centres ------------------------------------------------------------------------------------------------

def test_the_cruise_is_found_with_the_local_altimeter_left_in(copilot):
    """30.15 in at FL380 reads 38,210: never within 200 ft of the plan, so the cruise was never detected and
    everything that waits for it (centre handoffs, the descent) never came."""
    assert any("DEPARTURE -> CRUISE" in line for line in copilot)


def test_centre_to_centre_across_the_country(copilot):
    import re

    handoffs = [m.group(1) for line in atc(copilot) if (m := re.search(r"contact ([A-Z][\w ]*? Center)", line))]
    assert handoffs[:3] == ["Denver Center", "Salt Lake Center", "Seattle Center"]


def test_centre_descends_via_the_arrival_not_to_3000(copilot):
    descent = next(line for line in atc(copilot) if "Seattle Center" in line and "descend" in line)
    assert "descend via the CHINS5 arrival" in descent


# --- ATIS and clearance -------------------------------------------------------------------------------------------

def test_denvers_atis_on_125_6_is_not_seattle_approach():
    """Seattle Approach also works 125.6: parked at Denver, 125.6 is Denver's ATIS."""
    engine = AtcEngine(EngineConfig(destination="KSEA", cruise_ft=38000, callsign="DAL2543", seed=3))
    broadcasts = []
    for event in Recording(FLIGHT).events():
        if event.t > 70:
            break
        if isinstance(event, SIM_EVENT_TYPES):
            broadcasts += [o for o in engine.handle(event) if isinstance(o, AtisBroadcast)]
    assert broadcasts and broadcasts[0].station.startswith("Denver International")


def test_readback_correct_then_contact_ground(copilot):
    delivery = next(line for line in atc(copilot) if "Denver Clearance" in line and "readback correct" in line)
    assert "contact Denver Ground" in delivery and "120.15" in delivery


# --- the taxi ------------------------------------------------------------------------------------------------------

DELTA = Callsign("DAL2543", telephony="Delta", flight_number="2543")
LIBRARY = TemplateLibrary.load()


def taxi_readback(text: str):
    slots = {"callsign": DELTA, "runway": "16R", "hold_point": "WE", "taxi_route": ("AN", "H", "BS", "F", "WB", "D5", "WE")}
    rendered = LIBRARY.render("ground.taxi_out_at", slots)
    pending = PendingReadback("ground.taxi_out_at", "ground", rendered.expected, rendered.required, rendered.optional)
    return GrammarInterpreter().interpret(text, pending, InterpretContext(callsign=DELTA))


@pytest.mark.parametrize("text", [
    # Word perfect, but "Delta" of the callsign was taken for one more taxiway, D.
    ("Runway one six right at whiskey echo, taxi via alpha november, hotel, bravo sierra, foxtrot, whiskey bravo, "
     "delta five, whiskey echo, Delta twenty-five forty-three."),
    ("Taxiways runway 16 right via Alpha November Hotel Bravo Sierra Foxtrot. Whiskey Bravo Delta 5 at Whiskey Echo "
     "Delta 2543."),
    # Speech-to-text: "Alpha November" came out "off of November", Foxtrot "Foxtrop". Close enough on a long route.
    "Taxi runway 16R, off of November Hotel, Bravo Sierra, Foxtrop. Whiskey Bravo, Delta 5, and at Whiskey Echo.",
])
def test_taxi_readbacks_the_pilot_could_not_get_accepted(text):
    assert taxi_readback(text).status == "correct"


def test_a_typed_route_is_a_route_and_a_wrong_taxiway_still_is_not():
    typed = taxi_readback("Taxi via an, h, bs, f, wb, d5, we, Delta 2543")
    assert (typed.status, typed.missing) == ("incomplete", ("runway",))  # the route is fine; the runway is missing
    wrong = taxi_readback("Runway 16R at whiskey echo, taxi via alpha november, hotel, bravo sierra, golf, "
                          "whiskey bravo, delta five, whiskey echo, Delta 2543")
    assert wrong.status == "incorrect"


def test_the_e170_stopped_ahead_on_the_taxiway(recorded):
    caution = [line for line in atc(recorded) if "stopped" in line and "ahead" in line]
    assert len(caution) == 1 and "E170" in caution[0]
    assert 900 < float(caution[0].split("]")[0].strip("[ ")) < 960  # a couple of hundred metres before reaching it


# --- the arrival -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Ground, Delta 2543. Request gate.", "Request gate.", "Delta 2543. Request parking.",
                                  "Ground, which gate for Delta 2543?"])
def test_asking_for_a_gate(text):
    match, ambiguous = resolve(match_intents(normalize(text)))
    assert not ambiguous and match.intent == "request_taxi_parking"


def test_the_gate_is_given_and_the_taxi_in_goes_on(recorded):
    gate = next(line for line in recorded if "Request gate" in line)
    answer = recorded[recorded.index(gate) + 1]
    assert "Seattle Ground" in answer and "Gate" in answer
    # Stopped on the taxiway waiting for it isn't parked; moving on again isn't a taxi out.
    phases = [line for line in recorded if " PHASE " in line]
    assert phases[-1].endswith("LANDING -> TAXI_IN (rollout complete)")


def test_the_engine_is_running_by_its_n1_when_the_aircraft_does_not_say():
    from localtc.sim_bridge import definitions as defs

    raw = {d.field: 0 for d in defs.OWNSHIP} | {"com1_type": "", "com1_ident": "", "eng_n1": 21.5, "xpdr_state": 4,
                                                  "xpdr_code": 0x1200, "altimeter_inhg": 29.92}
    assert defs.ownship_from_raw(raw, 0.0).engine_running


def test_approach_vectors_the_arrival_all_the_way_to_the_final(copilot):
    arrival = [line for line in atc(copilot) if "Seattle Approach" in line]
    headings = [line for line in arrival if "heading" in line]
    assert len(headings) >= 3
    assert "until established on the localizer, cleared ILS RWY 16L approach" in headings[-1]


def test_tower_keeps_the_runway_approach_cleared(copilot):
    tower = [line for line in atc(copilot) if "Seattle Tower" in line and "runway 16" in line]
    assert tower and all("16L" in line for line in tower)


def seattle() -> Airport:
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("type") == "airport_data" and row["airport"]["icao"] == "KSEA":
                return msgspec.convert(row["airport"], Airport)
    raise AssertionError


def test_seattle_tacoma_is_said_properly():
    from localtc.atc_core.phraseology import speech

    assert speech.airport_name(seattle().name, "KSEA") == "Seattle-Tacoma International"


def test_greetings_come_from_some_controllers_and_not_others(copilot):
    greeted = {line.split(": ")[0].split("ATC ")[1].strip() for line in atc(copilot) if "good afternoon" in line}
    assert 1 <= len(greeted) < len({line.split(": ")[0] for line in atc(copilot)})
    assert sum("good afternoon" in line for line in atc(copilot)) == len(greeted)  # each one says it once at most
