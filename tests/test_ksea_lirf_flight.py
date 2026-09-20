"""Regressions from the KSEA -> LIRF flight (2026-09-20), eleven hours in an A330.

The long cruise is what this flight brought out: phases and controllers that were right for a circuit
round the pattern and wrong for a crossing. It also covers pushback, the SID in the clearance, and the
taxi to the gate at an airport whose taxiways have two-letter names.
"""

from pathlib import Path

import pytest

from localtc.atc_core.phase import FlightPhase, PhaseThresholds
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.readback.extract import candidates, taxi_route_matches, values_equal
from localtc.atc_core.readback.normalize import normalize
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksea_lirf"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def phases(lines: list[str]) -> list[str]:
    return [line.split("PHASE")[1].split("(")[0].strip() for line in lines if " PHASE " in line]


def after(lines: list[str], text: str, count: int = 1) -> list[str]:
    start = next(i for i, line in enumerate(lines) if text in line)
    return [line for line in lines[start + 1 :] if " ATC " in line][:count]


# --- the cruise ----------------------------------------------------------------------------------


def test_avoiding_traffic_out_of_seattle_is_not_the_arrival(replay):
    """A TCAS descent half an hour in put the flight in ARRIVAL for the next ten hours, so Seattle
    Departure worked it all the way to Italy and the descent into Fiumicino was read out over Puget Sound."""
    reached_cruise = next(i for i, p in enumerate(phases(replay)) if p.endswith("CRUISE"))
    arrival = next(i for i, p in enumerate(phases(replay)) if p.endswith("ARRIVAL"))
    assert arrival == reached_cruise + 1


def test_the_cruise_lasts_the_whole_crossing(replay):
    cruise = next(line for line in replay if "-> CRUISE" in line)
    arrival = next(line for line in replay if "-> ARRIVAL" in line)
    hours = (float(arrival.split("]")[0].strip("[ ")) - float(cruise.split("]")[0].strip("[ "))) / 3600
    assert hours > 8


def test_a_descent_far_from_the_destination_is_not_the_arrival():
    from test_phase import own

    from localtc.atc_core.phase.context import PositionContext
    from localtc.atc_core.phase.detector import PhaseDetector

    def descending() -> list:
        return [own(float(i), alt_msl_ft=34000, alt_indicated_ft=34000, alt_agl_ft=34000, vs_fpm=-900,
                    on_ground=False, gs_kt=460) for i in range(120)]

    far = PositionContext(destination_distance_nm=4000.0)
    near = PositionContext(destination_distance_nm=120.0)
    over_the_ocean = PhaseDetector(PhaseThresholds(), cruise_ft=35000)
    over_the_ocean.phase, over_the_ocean.since_t = FlightPhase.CRUISE, 0.0
    assert all(over_the_ocean.update(tick, far) is None for tick in descending())

    near_the_field = PhaseDetector(PhaseThresholds(), cruise_ft=35000)
    near_the_field.phase, near_the_field.since_t = FlightPhase.CRUISE, 0.0
    assert any(near_the_field.update(tick, near) is not None for tick in descending())


def test_the_flight_is_handed_from_one_centre_to_the_next(replay):
    centres = [line for line in replay if "Center" in line and "contact" in line]
    assert len(centres) >= 3, centres


def test_departure_hands_the_cruise_on_to_a_centre(replay):
    """Seattle Departure worked the whole crossing because nothing ever took it off them."""
    cruise = next(i for i, line in enumerate(replay) if "-> CRUISE" in line)
    arrival = next(i for i, line in enumerate(replay) if "-> ARRIVAL" in line)
    handoff = [line for line in replay[cruise:arrival] if "Seattle Departure" in line and "contact" in line]
    assert handoff and "Center" in handoff[0], handoff


# --- the calls this flight made ------------------------------------------------------------------


@pytest.mark.parametrize(("text", "intent"), [
    ("DAL42, request pushback and start, gate S9", "request_pushback"),
    ("DAL42, request push and start", "request_pushback"),
    ("DAL42, request taxi", "ready_to_taxi"),
])
def test_calls_this_flight_made(text, intent):
    heard = GrammarInterpreter().interpret(text, None, InterpretContext(phase="PARKED"))
    assert heard.intent == intent


def test_pushback_is_approved_with_a_direction(replay):
    assert "push and start" in after(replay, "request pushback and start")[0]


def test_pushback_is_a_phase_of_its_own(replay):
    assert any(p.endswith("PUSHBACK") for p in phases(replay)), phases(replay)


# --- readbacks -----------------------------------------------------------------------------------


def test_the_flight_level_in_the_clearance_readback():
    """ "expect flight level three five zero, one zero minutes after departure" read as FL35010, so the
    copilot's own readback of the IFR clearance could never be right."""
    said = ("Cleared to Fiumicino as filed, climb and maintain five thousand, expect flight level three five "
            "zero one zero minutes after, departure one one niner point two, squawk one one two zero")
    assert candidates("cruise", normalize(said), None) == [35000]
    assert candidates("altitude", normalize(said), None) == [5000]


def test_two_letter_taxiways_read_back_letter_by_letter():
    """Fiumicino's DG and CH are spoken "delta golf", "charlie hotel": four letters for two taxiways."""
    assert taxi_route_matches(("D", "G", "D", "C", "H", "C", "B", "D", "M", "U"), ("DG", "D", "CH", "C", "B", "DM", "U"))
    assert not taxi_route_matches(("D", "K", "D"), ("DG", "D"))


def test_the_hold_short_point_inside_a_taxi_readback():
    """ "16L via B, holding point C" stopped reading the route at "holding" and came out as just B."""
    heard = candidates("taxi_route", normalize("16L via B, holding point C, DAL42"), ("B", "C"))
    assert any(values_equal("taxi_route", route, ("B", "C")) for route in heard), heard


def test_the_taxi_to_the_gate_is_read_back_correctly(replay):
    assert not any("negative, taxi via" in line for line in replay), \
        [line for line in replay if "negative" in line]


def test_the_runway_reported_on_final_is_the_one_flown(replay):
    """Fiumicino's wind favoured 34L and the flight came down 16L. Calling the runway that was expected
    rather than the one ahead leaves tower clearing an aircraft onto a runway it is pointing away from."""
    landed = next(line for line in replay if "-> LANDING" in line).split("runway ")[1].strip(" )")
    cleared = next(line for line in replay if "cleared to land" in line)
    assert f"runway {landed}," in cleared, (landed, cleared)


# --- naming --------------------------------------------------------------------------------------


def test_fiumicino_is_not_called_fiume(replay):
    assert not any("Fiume " in line for line in replay), [line for line in replay if "Fiume " in line]
