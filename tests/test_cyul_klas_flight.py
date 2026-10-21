"""Regressions from the Montreal to Las Vegas flight (2026-10-18), an FSLabs A321 flown by voice as Air Canada 1709,
the departure part (to 4,400 s): tower held it short of 24R for a 737 rolling out on the parallel 24L, ground handed it
to tower only at the hold line, and the taxi guidance along the route ground gave.
"""

from pathlib import Path

import pytest

from localtc import scenario as scenarios
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_cyul_klas_departure"


@pytest.fixture(scope="module")
def flown():
    """The flight as flown (the pilot's own calls, today's ATC), with the engine looked at along the way."""
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    seen: dict[float, dict] = {}
    made = []
    real = scenarios.AtcEngine

    def engine(*a, **kw):
        made.append(real(*a, **kw))
        return made[-1]

    def look(event) -> None:
        t = getattr(event, "t", 0.0)
        for at in (3600.0, 3850.0, 3936.0):
            if t >= at and at not in seen and made:
                own = made[0].state.aircraft
                seen[at] = {"blocked": made[0]._departure_blocked("24R"), "marks": made[0].taxi_guide(),
                            "left": made[0]._taxi_left_m(own) if own is not None else None}

    scenarios.AtcEngine = engine
    try:
        result = run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True, on_input=look)
    finally:
        scenarios.AtcEngine = real
    return result.lines, seen


def atc(lines: list[str]) -> list[str]:
    return [line for line in lines if " ATC " in line]


def test_a_737_rolling_out_on_the_parallel_isnt_on_this_runways_final(flown):
    """ROU1605 rolling out on 24L, 1,600 m to the side of 24R's threshold: 24R's final, a mile wide all the way in,
    took it for one landing on 24R, "hold short 24R, Boeing 737 landing" (the pilot: "I thought there was a plane
    landing on 24R?"). A real one on 24R's final is on it."""
    from localtc.atc_core.airport.geometry import AirportGeometry
    from localtc.sim_api import AirportData

    airport = next(e.airport for e in Recording(FLIGHT).events() if isinstance(e, AirportData) and e.airport.icao == "CYUL")
    geo = AirportGeometry(airport)
    assert geo.final_approach(45.474633, -73.719155, 222.7, max_distance_nm=6.0) is None  # on 24L, past its threshold
    final = geo.final_approach(45.5294, -73.6472, 223.0, max_distance_nm=6.0)  # DAL2665, on 24L's final
    assert final is not None and final.end.ident == "24L"
    lines, seen = flown
    assert seen[3936.0]["blocked"] is None
    assert not any("hold short" in line and "landing" in line for line in atc(lines))


def test_ground_hands_over_to_tower_on_the_way_to_the_runway(flown):
    """Ground's "contact tower" came at the hold line; now on the way, with no runway left to cross."""
    lines, _ = flown
    handoff = next(line for line in atc(lines) if "Montreal Ground" in line and "contact Montreal Tower" in line)
    holding = next(line for line in lines if "TAXI_OUT -> RUNWAY_HOLD" in line)
    t = lambda line: float(line[1:line.index("]")])  # noqa: E731
    assert t(handoff) < t(holding) - 30


def test_arrows_along_the_route_and_a_stop_bar_at_the_hold(flown):
    _, seen = flown
    taxiing, near = seen[3600.0], seen[3850.0]
    assert len(taxiing["marks"]) >= 20 and {m[1] for m in taxiing["marks"]} == {"arrow"}
    assert {m[1] for m in near["marks"]} == {"arrow", "stop"}  # the hold short of 24R ahead
    assert near["left"] is not None and near["left"] < taxiing["left"]
