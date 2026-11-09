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
    def __init__(self, pm: PilotMonitoring, bus: EventBus, source: SimSource | None = None, cabin=None, ground=None) -> None:
        """``cabin``: the cabin crew (crew.cabin.CabinCrew), whose words on the intercom ("cabin, ...") are theirs.
        ``ground``: the ground crew's service (ground.service.GroundService): so are "ramp, ..."."""
        self.pm, self.bus, self.source, self.cabin, self.ground = pm, bus, source, cabin, ground
        # The cabin crew talk about the off-block time; the copilot doesn't say it too ("just have the cabin crew talk
        # about off block").
        pm.cabin_on_schedule = (lambda: bool(getattr(cabin, "active", False))) if cabin is not None else (lambda: False)
        # Subscribed now, not in run(): a fast source could publish before run() starts.
        # Everything the copilot watches to speak first (crew.monitor): ATC and the pilot on the radio (so it doesn't
        # talk over them), the phases, the readbacks, the traffic, the arrival at the gate.
        self._inputs = bus.subscribe(OwnshipState, AircraftSystems, AircraftVars, AircraftIdentity, IntercomHeard, AtcTransmission,
                                     PhaseChanged, ReadbackEvaluated, PttPressed, PttReleased, IntercomPressed,
                                     IntercomReleased, Transcript, AtcAlert, TrafficSnapshot, FlightArrived, ArrivalData)

    def _checklist_answer(self, event: IntercomHeard) -> bool:
        """The answer to the line of a checklist the copilot is reading: the copilot's, not the cabin's. "Cabin?" in
        the before landing checklist, answered "Cabin, ready", went to the purser, and the checklist stopped there."""
        progress = getattr(self.pm.monitor, "progress", None)
        if event.to == "cabin" or progress is None or progress.paused or progress.index >= len(progress.checklist.lines):
            return False
        line = progress.checklist.lines[progress.index]
        if "cabin" in line.challenge.lower():
            return True
        # Otherwise only what isn't said to them by name ("Jonas, ..."): the answer they were listening for is the line's.
        return self.cabin is not None and self.cabin._address(event.text) is None

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        async for event in self._inputs:
            started = loop.time()
            if isinstance(event, IntercomHeard) and event.to == "cabin" and not event.text.strip():
                continue  # the cabin's key, and nothing heard: not the copilot's to answer
            if isinstance(event, IntercomHeard) and self.ground is not None and self.ground.crew.claims(event.text, event.t, event.to):
                asyncio.create_task(self.ground.answer(event.text, event.t))
                continue  # for the ramp ("ramp, ...", or the answer to what they just asked), whoever else is listening
            if isinstance(event, IntercomHeard) and self.cabin is not None and not self._checklist_answer(event)                     and self.cabin.claims(event.text, event.t, event.to):
                from localtc.crew.cabin_service import answer

                asyncio.create_task(answer(self.cabin, event, self.bus))
                continue  # for the cabin: the copilot doesn't answer it
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
