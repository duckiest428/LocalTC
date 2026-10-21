"""Taxi guidance in the sim: arrows on the ground along the route ground gave (``AtcEngine.taxi_guide``), a stop bar
short of a runway the aircraft isn't cleared onto or across, sent to the sim as ``GroundMarks`` while the pilot has
it on ([atc] taxi_guide, Quick Settings). The arrows and the bar are the sim objects of LocalTC's own package
(``localtc.simpkg``), put in the sim's Community folder when the guidance is turned on.
"""

import logging

from localtc.bus import EventBus
from localtc.sim_api import GroundMarks, OwnshipState, SimSource

log = logging.getLogger(__name__)

EVERY_S = 1.0  # the marks looked at this often (sim time)


class TaxiGuidance:
    def __init__(self, engine, source: SimSource, bus: EventBus, *, enabled: bool = False) -> None:
        self.engine, self.source = engine, source
        self._inputs = bus.subscribe(OwnshipState)
        self._enabled = enabled
        self._shown: tuple = ()
        self._next_t = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def set_enabled(self, on: bool) -> None:
        """On or off mid-flight; off takes the marks away at once."""
        self._enabled = on
        if not on:
            await self._show(())

    async def run(self) -> None:
        async for own in self._inputs:
            if own.t < self._next_t:
                continue
            self._next_t = own.t + EVERY_S
            try:
                await self._show(tuple(self.engine.taxi_guide()) if self._enabled else ())
            except Exception:  # noqa: BLE001 - the guidance failing never stops the flight
                log.exception("Taxi guidance failed")

    async def _show(self, marks: tuple) -> None:
        keys = tuple(m[0] for m in marks)
        if keys == tuple(m[0] for m in self._shown):
            return
        self._shown = marks
        await self.source.send(GroundMarks(marks=marks))

    async def close(self) -> None:
        """The flight's over: the marks out of the sim."""
        await self._show(())
