"""One LocalTC app at a time.

Two of them both answered the radio: two controllers at once, and the one left running after its window was closed
went on talking. The first app holds a lock on a file in the data folder, with its address in it; a second one finds
the lock taken, asks the first to bring its window up, and ends. The lock goes with the process, however it ends.
"""

import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

FILE = "app.lock"


class Instance:
    def __init__(self, folder: Path) -> None:
        self.path = folder / FILE
        self._file = None

    def acquire(self) -> bool:
        """True: this is the only app. False: another one holds the lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+")  # noqa: SIM115 - held open for the app's life: that is the lock
        try:
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._file = f
        return True

    def publish(self, url: str) -> None:
        """The running app's address, for a second one to find it. (Written past the locked first byte on Windows.)"""
        if self._file is None:
            return
        self._file.seek(0)
        self._file.truncate()
        self._file.write(" " + json.dumps({"url": url, "pid": os.getpid()}))
        self._file.flush()

    def running_url(self) -> str | None:
        try:
            with open(self.path, encoding="utf-8") as f:
                f.seek(1)  # the first byte is the lock (Windows won't let another process read it)
                return json.loads(f.read().strip() or "{}").get("url")
        except (OSError, ValueError):
            return None

    def show_running(self) -> bool:
        """Ask the app that's running to bring its window to the front."""
        url = self.running_url()
        if not url:
            return False
        try:
            request = urllib.request.Request(url + "api/window/show", data=b"{}", method="POST",
                                             headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=3).close()
            return True
        except OSError as exc:
            log.info("The LocalTC that's running didn't answer: %s", exc)
            return False
