"""Puts the Pilot Monitoring on the bus: every event in, its words and records published, its sim commands sent."""

import asyncio
import logging

from localtc.bus import EventBus
from localtc.crew.pm import PilotMonitoring
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    ArrivalData,
    AtcAlert,
    AtcTransmission,
    FlightArrived,
    IntercomHeard,
    IntercomPressed,
    IntercomReleased,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    ReadbackEvaluated,
    SimCommand,
    SimSource,
    TrafficSnapshot,
    Transcript,
)

log = logging.getLogger(__name__)


class CrewService:
    def __init__(self, pm: PilotMonitoring, bus: EventBus, source: SimSource | None = None) -> None:
        self.pm, self.bus, self.source = pm, bus, source
        # Subscribed now, not in run(): a fast source could publish before run() starts.
        # Everything the copilot watches to speak first (crew.monitor): ATC and the pilot on the radio (so it doesn't
        # talk over them), the phases, the readbacks, the traffic, the arrival at the gate.
        self._inputs = bus.subscribe(OwnshipState, AircraftSystems, AircraftIdentity, IntercomHeard, AtcTransmission,
                                     PhaseChanged, ReadbackEvaluated, PttPressed, PttReleased, IntercomPressed,
                                     IntercomReleased, Transcript, AtcAlert, TrafficSnapshot, FlightArrived, ArrivalData)

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
