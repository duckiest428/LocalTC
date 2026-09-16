"""Session time. Everything downstream of a SimSource reads time from its clock."""

import time
from collections.abc import Callable
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Seconds since the session started."""
        ...


class SessionClock:
    """Monotonic wall time since ``reset()``; used by live sources."""

    def __init__(self, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time_fn = time_fn
        self._start = time_fn()

    def reset(self) -> None:
        self._start = self._time_fn()

    def now(self) -> float:
        return self._time_fn() - self._start


class StreamClock:
    """Time taken from the event stream; used by replay so it can run faster than real time."""

    def __init__(self) -> None:
        self._now = 0.0

    def advance(self, t: float) -> None:
        if t > self._now:
            self._now = t

    def now(self) -> float:
        return self._now
