"""Montreal towards Orlando (7 November 2026, an FSLabs A321 as Rouge 1638, on LocalTC 0.5.5), with the pilot's notes:

- Cleared to expect runway 06L, then a new ATIS for 06R and 06L, and ground gave 06R without a word ("it should first
  ask us"). The runway from the clearance is kept while it's still in use; one no longer in use is said to be.
- The phase went parked, pushback, parked ... seven times at the end of the push: GSX gone, the sim reading 0.7 knots.
- The app crashed climbing out (a voice playing while the microphone was reopened: dsp.portaudio).
"""

from pathlib import Path

import pytest

from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_cyul_kmco"


@pytest.fixture(scope="module")
def recorded():
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    sc = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(sc, FLIGHT, recording=FLIGHT, recorded_pilot=True)


def test_one_push_not_seven(recorded):
    phases = [line for line in recorded.lines if "PHASE" in line]
    assert sum("-> PUSHBACK" in line for line in phases) == 1, phases


def test_the_runway_from_the_clearance_is_kept_while_its_in_use(recorded):
    taxi = next(line for line in recorded.lines if " ATC " in line and "Montreal Ground" in line and "taxi to runway" in line)
    assert "runway 06L" in taxi and " via " in taxi, taxi
