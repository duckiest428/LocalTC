"""Runs the SimSource contract against the real sim.

Needs Windows, MSFS 2024 running with a flight loaded, SimConnect.dll
findable, and LOCALTC_LIVE_SIM=1.
"""

import asyncio
import os
import sys

import pytest
from helpers.contract import check_source_contract

from localtc.config import LiveConfig
from localtc.sim_api import OwnshipState
from localtc.sim_bridge.simconnect_source import SimConnectSource

pytestmark = [
    pytest.mark.windows,
    pytest.mark.skipif(
        sys.platform != "win32" or os.environ.get("LOCALTC_LIVE_SIM") != "1",
        reason="needs Windows + running MSFS 2024 + LOCALTC_LIVE_SIM=1",
    ),
]


def test_live_sim_contract():
    source = SimConnectSource(LiveConfig(connect_timeout_s=30))
    info, events = asyncio.run(check_source_contract(source, min_events=10, max_events=40, timeout=30))
    assert info.sim_product == "MSFS 2024", f"connected to {info.sim_product} {info.sim_version}"
    own = [e for e in events if isinstance(e, OwnshipState)]
    assert own, "no own-ship data received"
    assert -90 <= own[-1].lat <= 90 and len(own[-1].squawk) == 4
