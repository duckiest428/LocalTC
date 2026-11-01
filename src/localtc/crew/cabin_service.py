"""Puts the cabin crew on the bus: the flight's events in, their announcements, calls and sounds published.

What the pilot says on the intercom goes to the cabin when it's for them (``CabinCrew.claims``): through the copilot's
service when there's a copilot (crew.service asks the cabin first, so the copilot never answers "cabin, a coffee
please"), else here.
"""

import asyncio
import logging

from localtc.bus import EventBus
from localtc.crew.cabin import CabinCrew
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    FlightArrived,
    IntercomHeard,
    OwnshipState,
    PhaseChanged,
    SimLifecycle,
    WeatherReport,
)

log = logging.getLogger(__name__)
REPLY_PAUSE_S = 1.2  # an answer comes a beat after the captain's words, not at once


class CabinService:
    def __init__(self, cabin: CabinCrew, bus: EventBus, *, intercom: bool = True) -> None:
        """``intercom``: the pilot's words on the intercom are heard here (False: the copilot's service hands them
        over)."""
        self.cabin, self.bus = cabin, bus
        kinds = [OwnshipState, AircraftSystems, AircraftIdentity, PhaseChanged, FlightArrived, WeatherReport, SimLifecycle]
        if intercom:
            kinds.append(IntercomHeard)
        self._inputs = bus.subscribe(*kinds)

    async def run(self) -> None:
        async for event in self._inputs:
            try:
                if isinstance(event, IntercomHeard):
                    if not self.cabin.claims(event.text, event.t):
                        continue
                    outputs = self.cabin.heard(event)
                    await asyncio.sleep(REPLY_PAUSE_S)
                else:
                    outputs = self.cabin.observe(event)
            except Exception:
                log.exception("Cabin crew failed on %s", type(event).__name__)
                continue
            for output in outputs:
                self.bus.publish(output)
