"""PortAudio shared by the voices' output and the microphone: restarted only with nothing playing.

Reading the device list again (a microphone Windows reconnected under a new number, a new default input) means
restarting PortAudio. Done while a voice was playing on another thread, it corrupted the heap and the app vanished
(0xc0000374 in ntdll, a second after "Microphone reopened"). Every stream that plays holds ``GATE.playing()``; the
restart waits for none to be open, and doesn't happen at all if one stays open.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager


class Gate:
    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._open = 0
        self._restarting = False

    @contextmanager
    def playing(self) -> Iterator[None]:
        """Around an output stream's life: never during a restart, and no restart while it's open."""
        with self._lock:
            while self._restarting:
                self._lock.wait()
            self._open += 1
        try:
            yield
        finally:
            with self._lock:
                self._open -= 1
                self._lock.notify_all()

    def hold(self) -> bool:
        """A stream that stays open (the sidetone): counted as open until ``let_go``. False during a restart."""
        with self._lock:
            if self._restarting:
                return False
            self._open += 1
            return True

    def let_go(self) -> None:
        with self._lock:
            self._open = max(0, self._open - 1)
            self._lock.notify_all()

    def restart(self, sd, wait_s: float = 3.0) -> bool:
        """PortAudio restarted, its device list read again. False (and nothing done) if a stream stayed open."""
        until = time.monotonic() + wait_s
        with self._lock:
            while self._open:
                left = until - time.monotonic()
                if left <= 0:
                    return False
                self._lock.wait(left)
            self._restarting = True
        try:
            sd._terminate()
            sd._initialize()
        finally:
            with self._lock:
                self._restarting = False
                self._lock.notify_all()
        return True


GATE = Gate()
