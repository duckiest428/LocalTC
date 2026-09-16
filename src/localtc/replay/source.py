"""``ReplaySource``: plays a recording back through the ``SimSource`` interface."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import msgspec

from localtc.replay.reader import Recording
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AircraftIdentity,
    BusEvent,
    ConnectionStatus,
    SessionInfo,
    StreamClock,
)

# Gap inserted between loops so t keeps increasing.
LOOP_GAP_S = 1.0

# State that is still true at start_at even though its event came earlier.
_PRIMER_TYPES = (ConnectionStatus, AircraftIdentity)


class ReplaySource:
    """Re-emits a recording with its original timing (scaled by ``speed``).

    Recorded radio events (PTT, transcripts) come out too, so a pump puts them
    back on the bus just as live components would. Their ``audio_ref`` resolves
    through ``recording.resolve_audio``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        speed: float = 1.0,
        start_at: float = 0.0,
        end_at: float | None = None,
        loop: bool = False,
        include_radio: bool = True,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if speed < 0:
            raise ValueError("speed must be >= 0")
        self._path = Path(path)
        self._speed = speed
        self._start_at = start_at
        self._end_at = end_at
        self._loop = loop
        self._include_radio = include_radio
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn or self._interruptible_sleep
        self._clock = StreamClock()
        self._stop = asyncio.Event()
        self._recording: Recording | None = None

    @property
    def clock(self) -> StreamClock:
        return self._clock

    @property
    def recording(self) -> Recording:
        if self._recording is None:
            raise RuntimeError("call start() first")
        return self._recording

    async def start(self) -> SessionInfo:
        self._recording = await asyncio.to_thread(Recording, self._path)
        recorded = self._recording.header.session
        return SessionInfo(
            source_kind="replay",
            sim_product=recorded.sim_product,
            sim_version=recorded.sim_version,
            simconnect_version=recorded.simconnect_version,
            recording=str(self._recording.root),
        )

    async def events(self) -> AsyncIterator[BusEvent]:
        recording = self.recording
        offset = 0.0
        anchor: tuple[float, float] | None = None  # (wall time, t) of the first emitted event
        while not self._stop.is_set():
            primer: dict[type, BusEvent] = {}
            pass_first_t: float | None = None
            last_out_t: float | None = None
            for event in recording.events():
                if self._stop.is_set():
                    return
                if not self._include_radio and not isinstance(event, SIM_EVENT_TYPES):
                    continue
                if event.t < self._start_at:
                    if isinstance(event, _PRIMER_TYPES):
                        primer[type(event)] = event
                    continue
                if self._end_at is not None and event.t > self._end_at:
                    break

                if pass_first_t is None:
                    pass_first_t = event.t
                out_t = event.t + offset

                if self._speed > 0:
                    now = self._time_fn()
                    if anchor is None:
                        anchor = (now, out_t)
                    delay = anchor[0] + (out_t - anchor[1]) / self._speed - now
                    if delay > 0:
                        await self._sleep_fn(delay)
                        if self._stop.is_set():
                            return

                for primed in primer.values():
                    yield self._emit(msgspec.structs.replace(primed, t=out_t))
                primer.clear()
                yield self._emit(event if offset == 0 else msgspec.structs.replace(event, t=out_t))
                last_out_t = out_t

            if not self._loop or last_out_t is None:
                return
            offset = last_out_t + LOOP_GAP_S - pass_first_t

    async def stop(self) -> None:
        self._stop.set()

    def _emit(self, event: BusEvent) -> BusEvent:
        self._clock.advance(event.t)
        return event

    async def _interruptible_sleep(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except TimeoutError:
            pass
