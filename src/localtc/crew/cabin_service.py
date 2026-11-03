"""Puts the cabin crew on the bus: the flight's events in, their announcements, calls and sounds published.

What the pilot says on the cabin's own key or button is theirs. What's said on the intercom goes to the cabin when it's
for them (``CabinCrew.claims``): through the copilot's
service when there's a copilot (crew.service asks the cabin first, so the copilot never answers "cabin, a coffee
please"), else here.
"""

import asyncio
import logging

import msgspec

from localtc.bus import EventBus
from localtc.crew.cabin import WATCHED, CabinCrew
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AircraftVars,
    CabinSpeech,
    FlightArrived,
    IntercomHeard,
    OwnshipState,
    PhaseChanged,
    SimLifecycle,
    WatchVars,
    WeatherReport,
)

log = logging.getLogger(__name__)
REPLY_PAUSE_S = 1.2  # an answer comes a beat after the captain's words, not at once


async def answer(cabin: CabinCrew, event: IntercomHeard, bus: EventBus) -> None:
    """Their answer to the pilot: decided by the script (what they'll do about it), in their own words from the language
    model where there is one, a beat after the captain's words. On its own task: the model never holds up the flight."""
    try:
        outputs = cabin.heard(event)
        if outputs and isinstance(outputs[0], CabinSpeech) and cabin.answers_by_model(outputs[0].text):
            words, records = await asyncio.to_thread(cabin.word, event.text, outputs[0].text, event.t)
            for record in records:
                bus.publish(record)
            outputs[0] = msgspec.structs.replace(outputs[0], text=words, spoken="")
        else:
            await asyncio.sleep(REPLY_PAUSE_S)
    except Exception:
        log.exception("Cabin crew failed on the intercom")
        return
    for output in outputs:
        bus.publish(output)


class CabinService:
    def __init__(self, cabin: CabinCrew, bus: EventBus, *, intercom: bool = True, source=None) -> None:
        """``intercom``: the pilot's words on the intercom are heard here (False: the copilot's service hands them
        over)."""
        self.cabin, self.bus, self.source = cabin, bus, source
        kinds = [AircraftVars, OwnshipState, AircraftSystems, AircraftIdentity, PhaseChanged, FlightArrived, WeatherReport, SimLifecycle]
        if intercom:
            kinds.append(IntercomHeard)
        self._inputs = bus.subscribe(*kinds)

    async def run(self) -> None:
        asked = False
        async for event in self._inputs:
            if not asked and self.source is not None and isinstance(event, OwnshipState):  # connected: GSX, the doors
                asked = True
                await self.source.send(WatchVars(names=WATCHED, owner="cabin"))
            try:
                if isinstance(event, IntercomHeard):
                    if not self.cabin.claims(event.text, event.t, event.to):
                        continue
                    asyncio.create_task(answer(self.cabin, event, self.bus))
                    continue
                else:
                    outputs = self.cabin.observe(event)
            except Exception:
                log.exception("Cabin crew failed on %s", type(event).__name__)
                continue
            for output in outputs:
                if isinstance(output, CabinSpeech) and self.cabin.wants_words(output.text):
                    try:
                        words, records = await asyncio.to_thread(self.cabin.word_announcement, output.text, output.t)
                    except Exception:
                        log.exception("Cabin crew's own words failed")
                    else:
                        for record in records:
                            self.bus.publish(record)
                        if words != output.text:
                            output = msgspec.structs.replace(output, text=words, spoken="")
                self.bus.publish(output)
