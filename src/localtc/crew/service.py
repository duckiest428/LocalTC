"""Puts the Pilot Monitoring on the bus: every event in, its words and records published, its sim commands sent."""

import asyncio
import logging

from localtc.bus import EventBus
from localtc.crew.pm import PilotMonitoring
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AircraftVars,
    ArrivalData,
    AtcAlert,
    AtcTransmission,
    CrewSpeech,
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
REPLY_PAUSE_S = 0.9  # the copilot's answer comes at least this long after the captain's words are in


class CrewService:
    def __init__(self, pm: PilotMonitoring, bus: EventBus, source: SimSource | None = None) -> None:
        self.pm, self.bus, self.source = pm, bus, source
        # Subscribed now, not in run(): a fast source could publish before run() starts.
        # Everything the copilot watches to speak first (crew.monitor): ATC and the pilot on the radio (so it doesn't
        # talk over them), the phases, the readbacks, the traffic, the arrival at the gate.
        self._inputs = bus.subscribe(OwnshipState, AircraftSystems, AircraftVars, AircraftIdentity, IntercomHeard, AtcTransmission,
                                     PhaseChanged, ReadbackEvaluated, PttPressed, PttReleased, IntercomPressed,
                                     IntercomReleased, Transcript, AtcAlert, TrafficSnapshot, FlightArrived, ArrivalData)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        async for event in self._inputs:
            started = loop.time()
            try:
                # Off the event loop, in order: what the pilot says may need the language model, and so may the
                # copilot's own calls (put in its own words in the LLM modes); the sim's data doesn't wait for either.
                if isinstance(event, IntercomHeard) or self.pm.model is not None:
                    outputs = await asyncio.to_thread(self.pm.observe, event)
                else:
                    outputs = self.pm.observe(event)
            except Exception:
                log.exception("Copilot failed on %s", type(event).__name__)
                continue
            if isinstance(event, IntercomHeard) and any(isinstance(o, CrewSpeech) for o in outputs):
                # A person answers after a beat, not the instant the captain stops talking.
                await asyncio.sleep(max(0.0, REPLY_PAUSE_S - (loop.time() - started)))
            for output in outputs:
                if isinstance(output, SimCommand):
                    if self.source is not None:
                        await self.source.send(output)
                else:
                    self.bus.publish(output)
