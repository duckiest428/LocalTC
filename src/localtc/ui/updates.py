"""The app's side of updates: check on launch, download when asked (or by itself in auto mode), and hand
the swap to ``update.launch_apply`` when the app closes. Never during a flight."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from localtc import __version__, update

log = logging.getLogger(__name__)


class Updates:
    def __init__(self, mode: Callable[[], str], publish: Callable[[str, Any], None], flying: Callable[[], bool]) -> None:
        self.mode = mode  # "notify", "auto" or "off", read each time so a settings change applies at once
        self.publish = publish
        self.flying = flying
        self.release: update.Release | None = None
        self.state = "idle"  # idle, checking, available, downloading, ready, failed, current
        self.message = ""
        self.payload: Path | None = None
        self.apply_on_exit = False
        self.relaunch = False
        self.quit: Callable[[], None] | None = None  # set by the window: closes the app
        found = update.staged()
        if found is not None:
            self.state, self.payload = "ready", found[1]
            self.message = f"LocalTC {found[0]} is downloaded and ready to install."

    def view(self) -> dict:
        ok, why = update.can_apply()
        return {"current": __version__, "state": self.state, "message": self.message, "mode": self.mode(),
                "release": asdict(self.release) if self.release else None, "can_apply": ok, "why_not": why}

    def _set(self, state: str, message: str = "") -> None:
        self.state, self.message = state, message
        self.publish("update", self.view())

    async def check(self, *, force: bool = False) -> dict:
        if self.mode() == "off" and not force:
            return self.view()
        if self.state in ("downloading", "ready"):
            return self.view()
        self._set("checking")
        self.release = await asyncio.to_thread(update.check, force=force)
        if self.release is None:
            self._set("current", f"LocalTC {__version__} is the latest version.")
        else:
            self._set("available", f"LocalTC {self.release.version} is out (you have {__version__}).")
            if self.mode() == "auto" and update.can_apply()[0]:
                await self.download()
        return self.view()

    async def download(self) -> dict:
        if self.release is None or self.state == "downloading":
            return self.view()
        ok, why = update.can_apply()
        if not ok:
            self._set("available", why)
            return self.view()
        self._set("downloading", f"Downloading LocalTC {self.release.version} ...")
        try:
            self.payload = await asyncio.to_thread(update.download, self.release)
        except Exception as exc:
            log.warning("Update download failed: %s", exc)
            self._set("failed", f"The download failed: {exc}")
            return self.view()
        self._set("ready", f"LocalTC {self.release.version} is ready: install it now, or it goes in the next time LocalTC closes." if self.mode() == "auto" else f"LocalTC {self.release.version} is ready to install.")
        return self.view()

    async def install_now(self) -> dict:
        if self.flying():
            raise RuntimeError("Finish or stop the flight first: LocalTC never updates mid-flight.")
        if self.payload is None:
            raise RuntimeError("Nothing downloaded yet.")
        self.apply_on_exit, self.relaunch = True, True
        self._set("ready", "Restarting to install the update ...")
        if self.quit is not None:
            asyncio.get_running_loop().call_later(0.5, self.quit)
        return self.view()

    def on_exit(self) -> None:
        """The app is closing: a downloaded update goes in now (asked for, or in auto mode)."""
        if self.payload is None or not (self.apply_on_exit or self.mode() == "auto"):
            return
        try:
            update.launch_apply(self.payload, relaunch=self.relaunch)
        except Exception as exc:
            log.warning("Couldn't start the update: %s", exc)
