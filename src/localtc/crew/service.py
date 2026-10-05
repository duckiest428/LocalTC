"""Puts the Pilot Monitoring on the bus: every event in, its words and records published, its sim commands sent."""

import asyncio
import logging

from localtc.bus import EventBus
from localtc.crew.pm import PilotMonitoring
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AtcTransmission,
    IntercomHeard,
    OwnshipState,
    SimCommand,
    SimSource,
)

log = logging.getLogger(__name__)


class CrewService:
    def __init__(self, pm: PilotMonitoring, bus: EventBus, source: SimSource | None = None) -> None:
        self.pm, self.bus, self.source = pm, bus, source
        # Subscribed now, not in run(): a fast source could publish before run() starts.
        self._inputs = bus.subscribe(OwnshipState, AircraftSystems, AircraftIdentity, IntercomHeard, AtcTransmission)

    async def run(self) -> None:
        async for event in self._inputs:
            try:
                if isinstance(event, IntercomHeard):  # maybe a language model call: off the event loop
                    outputs = await asyncio.to_thread(self.pm.observe, event)
                else:
                    outputs = self.pm.observe(event)
            except Exception:
                log.exception("Copilot failed on %s", type(event).__name__)
                continue
            for output in outputs:
                if isinstance(output, SimCommand):
                    if self.source is not None:
                        await self.source.send(output)
                else:
                    self.bus.publish(output)
