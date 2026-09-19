"""Regressions from the first voice flight: KGYR -> KPHX, MSFS 2024, Whisper base.en on CPU (2026-09-18).

The fixture is that flight's telemetry (thinned to about 1 Hz) and the pilot's transcripts, replayed
through today's ATC with the grammar alone. The pilot's later calls answer what ATC said *then*, so
this checks the individual answers, not a tidy dialogue.
"""

import re
from pathlib import Path

import pytest

from localtc.atc_core.llm.triggers import question_topic
from localtc.atc_core.readback import normalize
from localtc.atc_core.readback.normalize import render
from localtc.atc_core.values import Callsign
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_kgyr_kphx"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines


def after(lines: list[str], text: str, count: int = 3) -> list[str]:
    """The lines that follow the first line containing ``text``."""
    start = next(i for i, line in enumerate(lines) if text in line)
    return lines[start + 1 : start + 1 + count]


def test_the_clearance_readback_reaches_ground_working_clearance(replay):
    # KGYR has no clearance delivery; ground issues the clearance and must accept the readback.
    assert "READBACK  clearance.ifr correct" in after(replay, "Clear to Phoenix Sky Harbor as filed")[0]


def test_a_repeated_readback_gets_no_reply(replay):
    nxt = after(replay, "Time maintain 1500", 1)[0]
    assert "PILOT" in nxt  # not "say again", not the whole clearance again


def test_an_airline_style_callsign_is_never_abbreviated(replay):
    assert not any("P69" in line and "EXP69" not in line for line in replay)
    assert Callsign("EXP69").short.abbreviated is False
    assert Callsign("N172LT").short.abbreviated is True
    assert Callsign("CGABC").short.abbreviated is True
    assert Callsign("DP69").short.abbreviated is False


def test_asking_for_the_tower_frequency(replay):
    assert "contact Goodyear Tower 120.1" in after(replay, "Request frequency for tower", 1)[0]
    assert question_topic("EXP69. Request frequency for tower.") == "frequency"


def test_misheard_numbers_in_readbacks_are_confirmed(replay):
    # Probably right but not what ATC said: not "negative", not waved through either.
    assert "confirm frequency 120.1" in after(replay, "Goodyear Tower on 12.1, EXP69", 2)[1]  # 120.1 lost its zero
    assert "confirm maintain 1,500" in after(replay, "Maintain 1508 EXP69", 2)[1]
    assert "READBACK  departure.radar_contact correct" in after(replay, "Maintain 1500 EXP69", 1)[0]


def test_a_low_cruise_is_not_told_to_descend_or_climb(replay):
    atc = [line for line in replay if " ATC " in line]
    assert any("maintain 1,500, expect RNAV RWY" in line for line in atc)
    assert not any("descend and maintain 1,500" in line or "3,200" in line for line in atc)


def test_a_request_during_a_readback_is_a_request(replay):
    assert "maintain 1,500" in after(replay, "Negative request to maintain 1500", 1)[0]


def test_approach_clears_the_approach_well_before_short_final(replay):
    times = {name: float(re.match(r"\[\s*([\d.]+)\]", line).group(1)) for line in replay
             for name in ("cleared RNAV RWY", "APPROACH -> LANDING") if name in line}
    assert times["cleared RNAV RWY"] < times["APPROACH -> LANDING"] - 120


def test_landing_clearance_names_the_runway_that_exists(replay):
    landing = [line for line in replay if ", cleared to land." in line and " ATC " in line]
    assert landing and all("runway 08," in line for line in landing)


@pytest.mark.parametrize(("heard", "tokens"), [
    ("We'll climb to 3, 2, 0, 0, EXP69.", "we ll climb to <3200> exp <69>"),
    ("expect RNAV runway 0, 8, EXP69", "expect rnav runway <08> exp <69>"),
    ("runway 8, 3 mile final", "runway <8> <3> mile final"),  # two numbers
    ("squawk one two three four, two lima tango", "squawk <1234> <2> L T"),  # a group, then the callsign
    ("climb to 5, 0, 0, 0, 2LT", "climb to <5000> <2> lt"),
])
def test_digits_said_one_at_a_time(heard, tokens):
    assert render(normalize(heard)) == tokens
