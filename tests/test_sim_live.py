"""Against the live sim: run on the Windows PC with MSFS 2024 open and a flight loaded, parked at a gate with the
engines off (docs/windows-session.md). Skipped anywhere else.

    set LOCALTC_SIM_LIVE=1
    .venv\\Scripts\\python -m pytest tests/test_sim_live.py -v -s

Each test writes its report to %LOCALAPPDATA%\\LocalTC\\simcheck\\ (the JSON says what was sent and what the sim
showed) and prints it. A failure names the control or the traffic step the sim didn't answer, with what was sent:
that's what to fix (an aircraft profile's input event, the bridge, the traffic control).

The traffic test creates one aircraft beside yours and removes it. LOCALTC_SIM_ENROUTE="KSEA 16R" also flies one in
to that runway on a flight plan (two minutes), for whether the sim's AI flies LocalTC's plans and runways.
"""

import asyncio
import os
import sys

import pytest

from localtc import simcheck
from localtc.config import data_dir, load_config

pytestmark = pytest.mark.skipif(sys.platform != "win32" or os.environ.get("LOCALTC_SIM_LIVE") != "1",
                                reason="the live sim: Windows with MSFS running and LOCALTC_SIM_LIVE=1")


def live(check: str, **kw) -> simcheck.Report:
    from localtc.app import sim_check

    report = asyncio.run(sim_check(load_config(), check, **kw))
    out = data_dir() / "simcheck"
    out.mkdir(parents=True, exist_ok=True)
    path = simcheck.write(report, out / f"{report.check}.json")
    print(f"\n{report.text()}\nReport: {path}")
    return report


def test_the_aircraft_and_its_profile():
    report = live("aircraft")
    assert report.aircraft, "no aircraft: is a flight loaded?"
    assert report.input_events, "the aircraft lists no input events (MSFS 2024 lists them for its own aircraft)"
    assert not report.failed, [f"{s.name}: {s.detail}" for s in report.failed]


def test_the_copilots_hands_move_every_control():
    report = live("hands")
    assert report.steps and report.steps[0].name != "parked", "park the aircraft: on the ground, stopped"
    assert not [s for s in report.steps if s.restored is False], "a control wasn't put back: check the cockpit"
    assert not report.failed, [f"{s.name} <- {'; '.join(s.sent)}" for s in report.failed]


def test_traffic_is_seen_created_and_removed():
    where = os.environ.get("LOCALTC_SIM_ENROUTE", "").split()
    report = live("traffic", enroute=tuple(where) if len(where) == 2 else None,
                  plan_dir=data_dir() / "simcheck")
    assert not report.failed, [f"{s.name}: {s.detail}" for s in report.failed]
