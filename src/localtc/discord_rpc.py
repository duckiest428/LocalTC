"""The flight on the pilot's Discord profile (``[ui] discord``, off by default), through the Discord app's own local
connection: where they're flying, the frequency they're on, and a button to localtc.tech. Nothing leaves this PC except
to the Discord app, and nothing happens without it."""

import json
import logging
import os
import queue
import struct
import sys
import threading
import uuid
from typing import Any

log = logging.getLogger(__name__)

# The application at discord.com/developers that the profile shows as "LocalTC" (its public id, not a secret).
APP_ID = "1567208592128475249"
SITE = "https://localtc.tech"
BUTTON = "Fly on LocalTC"
LOGO = "logo"  # the image uploaded to that application under Rich Presence
RETRY_S = 15.0  # Discord closed: look for it again this often
HANDSHAKE, FRAME = 0, 1


def activity(flight: dict, started: int) -> dict | None:
    """What the profile shows for a flight (``Controller.flight``); None: not flying."""
    if not flight:
        return None
    route = " → ".join(x for x in (flight.get("origin"), flight.get("destination")) if x)
    details = " · ".join(x for x in (route, flight.get("phase_label")) if x) or "Flying"
    tuned = flight.get("tuned") or {}
    station, mhz = tuned.get("station"), tuned.get("mhz")
    state = f"{station} {mhz:.3f}" if station and mhz else station or "Not on a frequency"
    return {"details": details[:128], "state": state[:128], "timestamps": {"start": started},
            "assets": {"large_image": LOGO, "large_text": "LocalTC"},
            "buttons": [{"label": BUTTON, "url": SITE}]}


def _open_pipe() -> Any | None:
    if sys.platform != "win32":
        return None
    for n in range(10):
        try:
            return open(rf"\\.\pipe\discord-ipc-{n}", "r+b", buffering=0)
        except OSError:
            continue
    return None


class Presence:
    """A thread of its own, as the pipe blocks: ``show`` hands it the latest activity (None clears it)."""

    def __init__(self, app_id: str = APP_ID, open_pipe=_open_pipe) -> None:
        self.app_id, self._open = app_id, open_pipe
        self._wanted: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._last: Any = None

    def show(self, what: dict | None) -> None:
        if not self.app_id or what == self._last:
            return
        self._last = what
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="localtc-discord", daemon=True)
            self._thread.start()
        self._wanted.put(what)

    def _run(self) -> None:
        pipe, what = None, None
        while True:
            try:
                what = self._wanted.get(timeout=RETRY_S if what is not None and pipe is None else None)
                while not self._wanted.empty():  # only the latest matters
                    what = self._wanted.get_nowait()
            except queue.Empty:
                pass
            try:
                if pipe is None and what is not None:
                    pipe = self._open()
                    if pipe is not None:
                        _send(pipe, HANDSHAKE, {"v": 1, "client_id": self.app_id})
                        _receive(pipe)
                if pipe is not None:
                    _send(pipe, FRAME, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                                        "args": {"pid": os.getpid(), "activity": what}})
                    _receive(pipe)
            except (OSError, ValueError, struct.error) as exc:  # Discord closed
                log.info("Discord connection lost: %s", exc)
                try:
                    pipe.close()
                except (OSError, AttributeError):
                    pass
                pipe = None


def _send(pipe: Any, op: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    pipe.write(struct.pack("<II", op, len(body)) + body)


def _receive(pipe: Any) -> dict:
    head = pipe.read(8)
    if len(head) < 8:
        raise ValueError("Discord closed the connection")
    _, size = struct.unpack("<II", head)
    return json.loads(pipe.read(size))
