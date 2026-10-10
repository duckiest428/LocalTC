"""LocalTC's traffic on the bus: the sim's events in, the live positions fetched in the background, the manager's
commands out to the sim and its status out to the app (``manager.TrafficManager``)."""

import asyncio
import logging
import time
from pathlib import Path

from localtc.bus import EventBus
from localtc.sim_api import (
    AiObjectAssigned,
    AirportData,
    ConnectionStatus,
    EnumerateModels,
    ModelList,
    OwnshipState,
    SimCommand,
    SimSource,
    TrafficSnapshot,
)
from localtc.traffic.feed import FlightBook, LiveFeed
from localtc.traffic.manager import TrafficManager

log = logging.getLogger(__name__)

FEED_EVERY_S = 5.0  # the live positions asked for this often (the free sources' fair use)
FEED_SLOWER_S = 30.0  # ... and this often while none answers
TICK_S = 1.0


class TrafficService:
    def __init__(self, manager: TrafficManager, bus: EventBus, source: SimSource | None, *,
                 feed: LiveFeed | None = None, book: FlightBook | None = None, book_path: Path | None = None) -> None:
        self.manager, self.bus, self.source = manager, bus, source
        self.feed = feed or LiveFeed()
        self.book = book or FlightBook(book_path)
        manager.routes = self.book.route
        manager.types = self.book.type_of
        self._inputs = bus.subscribe(OwnshipState, TrafficSnapshot, AiObjectAssigned, ModelList, AirportData,
                                     ConnectionStatus)
        self._tasks: list[asyncio.Task] = []
        self._connected = True

    async def _out(self, outputs: list) -> None:
        for output in outputs:
            if isinstance(output, SimCommand):
                if self.source is not None:
                    await self.source.send(output)
            else:
                self.bus.publish(output)

    async def run(self) -> None:
        if self.source is not None:
            await self.source.send(EnumerateModels())
        self._tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._tick())]
        try:
            async for ev in self._inputs:
                try:
                    await self._observe(ev)
                except Exception:  # noqa: BLE001 - the traffic failing must never stop the flight
                    log.exception("Traffic failed on %s", type(ev).__name__)
        finally:
            for task in self._tasks:
                task.cancel()

    async def _observe(self, ev) -> None:
        m = self.manager
        if isinstance(ev, OwnshipState):
            m.on_own(ev)
        elif isinstance(ev, TrafficSnapshot):
            m.on_snapshot(ev)
        elif isinstance(ev, AiObjectAssigned):
            await self._out(m.on_assigned(ev, time.time()))
        elif isinstance(ev, ModelList):
            m.on_models(list(ev.models))
            log.info("Traffic: %d models installed, FSLTL %s", len(ev.models), "yes" if m.picker.fsltl else "no")
        elif isinstance(ev, AirportData):
            m.on_airport(ev.airport)
        elif isinstance(ev, ConnectionStatus):
            if ev.connected and not self._connected:
                # The sim back after a disconnect: what LocalTC had made there is gone with the old connection.
                m.set_on(False)
                m.set_on(True)
                if self.source is not None:
                    await self.source.send(EnumerateModels())
            self._connected = ev.connected

    async def _poll(self) -> None:
        """The live positions around the user, fetched off the event loop (and the routes and types missing)."""
        while True:
            own = self.manager.own
            wait = FEED_EVERY_S
            if own is not None and self.manager.on:
                radius = self.manager.s.radius_nm + 10
                flights = await asyncio.to_thread(self.feed.around, own.lat, own.lon, radius)
                self.manager.on_feed(flights, self.feed.source, time.time())
                if flights:
                    await asyncio.to_thread(self.book.look_up, flights)
                else:
                    wait = FEED_SLOWER_S
            await asyncio.sleep(wait)

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self._out(self.manager.tick(time.time()))
            except Exception:  # noqa: BLE001
                log.exception("Traffic tick failed")

    async def set_on(self, on: bool) -> None:
        await self._out(self.manager.set_on(on))

    async def close(self) -> None:
        """The flight is over: LocalTC's aircraft out of the sim."""
        await self._out(self.manager.set_on(False))
        await asyncio.to_thread(self.book.save)
