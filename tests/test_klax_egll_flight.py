"""Regressions from the KLAX -> EGLL flight (2026-10-08), nine and a half hours in an A350 with the copilot on the
radio (full) and on the intercom.

ATC's side: a readback of "125 decimal to", a taxi readback with a taxiway speech-to-text turned into a word, a taxi
request taken back, "request back and start", Gander Oceanic on a frequency Heathrow Director also uses, Shannon and
Scottish handing the flight back and forth, an A321 the sim put on the final after the landing clearance, the runway
crossed on the way in with no hold short, and a gate taken on arrival.
"""

from pathlib import Path

import pytest

from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.readback.extract import taxi_route_matches, taxi_routes
from localtc.atc_core.readback.normalize import normalize
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_klax_egll"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    """The flight's own pilot calls, with the copilot working the radio as it did."""
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True, copilot="full").lines


@pytest.fixture(scope="module")
def pilot_only() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def at(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def atc(lines: list[str], start: float = 0.0, end: float = 1e9) -> list[str]:
    return [line for line in lines if " ATC " in line and start <= at(line) <= end]


# --- readbacks ------------------------------------------------------------------------------------------------------


def test_a_frequency_said_decimal_to_is_point_two():
    """ "Departure is on 125 decimal to": the readback of 125.2, asked for three times."""
    assert "125.2" in [t.text for t in normalize("Frequency Departure is on 125 decimal to Virgin 6.")]
    assert "121.4" in [t.text for t in normalize("one two one decimal for")]


def test_a_taxiway_heard_as_a_word_doesnt_end_the_route():
    """ "... kilo, trile, trile 1, Bravo, Bravo 1": Charlie heard as "trile". The route was cut at K and called wrong
    ("that was a perfect readback")."""
    want = ["D8", "D", "D9", "D", "K", "C", "C1", "B", "B1"]
    tokens = normalize("Taxiator runway 25R at Bravo 1 via delta 8, delta, delta 9, delta, kilo, trile, trile 1, "
                       "Bravo, Bravo 1. And we have the information alpha, Virgin 6.")
    routes = taxi_routes(tokens, want)
    assert routes and taxi_route_matches(routes[0], want), routes


def test_a_wrong_taxiway_is_still_wrong():
    want = ["D8", "D", "D9", "D", "K", "C", "C1", "B", "B1"]
    routes = taxi_routes(normalize("via delta 8, delta, delta 9, delta, kilo, echo, echo 1, bravo, bravo 1"), want)
    assert routes and not taxi_route_matches(routes[0], ["D8", "D", "D9"]), routes


def test_only_what_is_missing_is_asked_for_again(pilot_only):
    """ "I need a full readback, read back frequency 125.2" asked for one item and called it the full readback."""
    assert not any("full readback" in line for line in pilot_only)


# --- the calls ------------------------------------------------------------------------------------------------------


def test_request_back_and_start_is_the_pushback():
    heard = GrammarInterpreter().interpret("Ground, Virgin Flight 6. Request back and start.", None,
                                           InterpretContext(phase="PARKED"))
    assert heard.intent == "request_pushback"


def test_a_taxi_request_taken_back_isnt_asked_for_again(pilot_only):
    """ "Can we abort our taxi request? We're not quite ready": ground said "advise when ready", then gave the taxi
    again and asked "did you copy?"."""
    after = atc(pilot_only, 413.0, 745.0)
    assert after and "ready" in after[0], after
    assert not any("taxi via" in line or "did you copy" in line for line in after[1:]), after


def test_the_pushback_is_approved(pilot_only):
    assert "push and start" in atc(pilot_only, 746.0, 760.0)[0]


# --- the crossing ---------------------------------------------------------------------------------------------------


def test_gander_oceanic_answers_on_its_own_frequency(replay):
    """120.4 is Gander Oceanic's and Heathrow Director's: after the handoff to Gander, LocalTC took it for Heathrow,
    1,900 miles away, and nobody answered for two and a half hours."""
    assert not any("out_of_range" in line and "Heathrow" in line for line in replay)
    assert any("ATC       Gander Oceanic" in line for line in replay)


def test_no_handing_back_and_forth_along_a_boundary(replay):
    """Shannon, Scottish, Shannon, Scottish in eight minutes along the boundary."""
    handoffs = [line.split("contact ")[1].split(" on ")[0].split(" 1")[0] for line in replay
                if " ATC " in line and "contact " in line and ("Shannon" in line or "Scottish" in line)]
    assert len(handoffs) == len(set(handoffs)), handoffs


# --- the arrival ----------------------------------------------------------------------------------------------------


def test_cleared_to_land_is_taken_back_for_traffic_appearing_ahead(replay):
    """The sim put an A321 on a two mile final after the landing clearance: "number two" with it still standing read
    backwards. Now it's "continue", and the clearance comes again once the runway's free."""
    sequenced = next(line for line in replay if "number two, follow" in line)
    assert sequenced.rstrip(".").endswith("continue"), sequenced
    assert any("cleared to land" in line for line in atc(replay, at(sequenced), at(sequenced) + 120))


def test_the_taxi_in_holds_short_of_the_runway_it_crosses(replay):
    """ "When we cross an active runway it should tell us to hold short of the runway first." """
    taxi_in = next(line for line in replay if "ATC       Heathrow Ground" in line and "taxi to" in line)
    assert "hold short runway 09R" in taxi_in, taxi_in


def test_the_crossing_comes_as_the_runway_is_reached_not_at_the_exit(replay):
    """Just off 27L, moving away from it, ground cleared the flight across 09R (the same runway, three minutes on)."""
    taxi_in = next(line for line in replay if "ATC       Heathrow Ground" in line and "taxi to" in line)
    crossing = next(line for line in replay if "cross runway 09R" in line and " ATC " in line)
    assert at(crossing) - at(taxi_in) > 60, (taxi_in, crossing)


def test_a_gate_taken_is_swapped_for_the_one_next_door_with_an_apology(replay):
    """Gate 403 taken: ground gave Gate 236 across the airfield with "Gate 403 is occupied" on the end. The flight
    parked at 402, next door."""
    swapped = next(line for line in replay if "is occupied" in line)
    assert "sorry, Gate 403 is occupied, taxi to" in swapped and "402" in swapped, swapped
