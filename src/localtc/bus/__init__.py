"""In-process async pub/sub connecting sources, the recorder, and (later) ATC, STT and TTS.

Sim events arrive from a SimSource through ``pump``. Radio events (PTT,
transcripts, ATC transmissions) are published directly by the components that
produce them. Every subscriber gets its own queue, so a slow consumer never
blocks the others.
"""

import asyncio
import logging

from localtc.sim_api import BusEvent, SimSource

log = logging.getLogger(__name__)

_CLOSED = object()


class Subscription:
    """An async iterator of bus events. Ends when it or the bus is closed."""

    def __init__(self, bus: "EventBus", types: tuple[type, ...] | None, maxsize: int) -> None:
        self._bus = bus
        self._types = types
        self._queue: asyncio.Queue = asyncio.Queue(maxsize)
        self._closed = False
        self.dropped = 0

    def offer(self, event: BusEvent) -> None:
        if self._closed or (self._types is not None and not isinstance(event, self._types)):
            return
        self._put(event)

    def _put(self, item: object) -> None:
        if self._queue.full():
            # Bounded subscribers prefer fresh data: drop the oldest item.
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(item)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._remove(self)
        self._put(_CLOSED)

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> BusEvent:
        item = await self._queue.get()
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)  # stay closed for later readers
            raise StopAsyncIteration
        return item


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []
        self._closed = False

    def subscribe(self, *types: type, maxsize: int = 0) -> Subscription:
        """Subscribe to all events, or only instances of ``types``. ``maxsize=0`` is unbounded."""
        if self._closed:
            raise RuntimeError("bus is closed")
        sub = Subscription(self, types or None, maxsize)
        self._subscriptions.append(sub)
        return sub

    def publish(self, event: BusEvent) -> None:
        for sub in tuple(self._subscriptions):
            sub.offer(event)

    def close(self) -> None:
        self._closed = True
        for sub in tuple(self._subscriptions):
            sub.close()

    def _remove(self, sub: Subscription) -> None:
        if sub in self._subscriptions:
            self._subscriptions.remove(sub)


async def pump(source: SimSource, bus: EventBus) -> int:
    """Forward every event from ``source`` onto ``bus`` until the source ends."""
    count = 0
    async for event in source.events():
        bus.publish(event)
        count += 1
    log.debug("source ended after %d events", count)
    return count


__all__ = ["EventBus", "Subscription", "pump"]
