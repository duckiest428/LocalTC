"""Montreal to Orlando joined in the cruise (7 November 2026, LocalTC restarted at FL360 over Georgia, the copilot on
the radio), with the pilot's notes:

- "Hold at HIGGZ ... number 6, delay 10 minutes" and 24 seconds later "cleared out of the hold", then held and let go
  again ("why even have that dialogue when it just clears out of it?"): the aircraft ahead counted again can't move
  the flight up faster than the runway lands them, and out of a hold it isn't held again straight away.
- The copilot's "Hey. All set on my side. Rouge 1638, Montreal to Orlando" on the landing roll: the gate's hello, never
  said once the flight's flown.
- After parking, the copilot asked Orlando Ground for taxi again.
"""

from pathlib import Path

import pytest

from localtc.config import load_config, with_recorded
from localtc.crew.pm import PilotMonitoring
from localtc.crew.profiles import load_all
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.sim_api import CrewSpeech

FLIGHT = Path(__file__).parent / "fixtures" / "real_cyul_kmco_joined"


@pytest.fixture(scope="module")
def flown():
    import localtc.scenario as scenarios

    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    sc = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT), copilot="full"), flight=cfg.flight, atc=cfg.atc)

    class Crew:
        pm = None

        def observe(self, ev):
            return self.pm.observe(ev)

    listening, real = Crew(), scenarios.AtcEngine

    def engine(*a, **kw):
        made = real(*a, **kw)
        listening.pm = PilotMonitoring(made, profiles=load_all(), radio_mode=lambda: "full")
        return made

    scenarios.AtcEngine = engine
    try:
        return run(sc, FLIGHT, recording=FLIGHT, crew=listening)
    finally:
        scenarios.AtcEngine = real


def _at(line: str) -> float:
    return float(line[1:line.index("]")])


def test_the_hold_lasts_as_long_as_the_sequence_and_comes_once(flown):
    atc = [line for line in flown.lines if " ATC " in line]
    held = [line for line in atc if ", hold at " in line]
    left = [line for line in atc if "out of the hold" in line or "leave the hold" in line]
    assert len(held) == 1 and len(left) == 1, held + left
    assert _at(left[0]) - _at(held[0]) >= 6 * 60, (held, left)  # number 6, delay 10 minutes: not 24 seconds


def test_no_hello_from_the_copilot_after_the_flight(flown):
    words = [o.text for o in flown.outputs if isinstance(o, CrewSpeech)]
    assert not [w for w in words if "All set on my side" in w or "I'm with you" in w], words


def test_parked_at_the_end_no_taxi_asked_for(flown):
    parked = next(_at(line) for line in flown.lines if "-> PARKED" in line)
    after = [line for line in flown.lines if " PILOT " in line and _at(line) > parked]
    assert not [line for line in after if "ready to taxi" in line], after
