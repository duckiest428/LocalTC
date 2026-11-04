"""GSX through its own documented interface, the Couatl Remote API (a WebSocket on this PC; its guide is a chapter of GSX's
manual). Nothing of GSX's is read, copied or shipped: only what it publishes there.

The server says hello, the client subscribes, a whole snapshot comes and then patches that each replace one top-level key.
Commands are answered by a result. It only listens while GSX's engine is up, and sessions don't survive a reconnect.
"""

import asyncio
import configparser
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from localtc.ground.api import GroundState, Menu, Result, Service
from localtc.ground.ws import WebSocket, WebSocketClosed

log = logging.getLogger(__name__)

DEFAULT_PORT = 8744
RETRY_S = 2.0
COMMAND_S = 10.0


def installed() -> bool:
    """Whether GSX is on this PC (its installer's registry key; never true off Windows)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\FSDreamTeam") as key:
            root = winreg.QueryValueEx(key, "root")[0]
    except (ImportError, OSError):
        return False
    return (Path(root) / "MSFS" / "fsdreamteam-gsx-pro").exists()


def port() -> int:
    """The port as GSX's own settings have it, read afresh each time (a change needs no restart here)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return DEFAULT_PORT
    ini = configparser.ConfigParser(strict=False, interpolation=None, inline_comment_prefixes=(";", "#"))
    try:
        ini.read(Path(appdata) / "Virtuali" / "CouatlAddons.ini", encoding="utf-8")
        return ini.getint("gsx", "remote_server_port", fallback=DEFAULT_PORT)
    except (configparser.Error, ValueError, OSError):
        return DEFAULT_PORT


def parse_service(raw: dict) -> Service:
    return Service(id=str(raw.get("id", "")), name=str(raw.get("displayName", "")), state=str(raw.get("state", "unknown")),
                   waiting=bool(raw.get("waiting")), operator=str(raw.get("operator") or ""),
                   status=str(raw.get("statusText") or ""), progress=str(raw.get("progressText") or ""),
                   can_trigger=bool(raw.get("canTrigger")))


def parse_state(model: dict, *, connected: bool, running: bool, capabilities: frozenset[str]) -> GroundState:
    menu = model.get("menu") or {}
    shown = bool(model.get("menuShown")) and bool(menu.get("title"))  # (the title stays stale for a moment after closing)
    entries = tuple(str(e) for e in menu.get("entries") or ())
    message = model.get("message") or {}
    airport = model.get("airport") or {}
    return GroundState(connected=connected, running=running, capabilities=capabilities,
                       services={s.id: s for s in map(parse_service, model.get("services") or ())},
                       menu=Menu(str(menu["title"]), entries, tuple(bool(d) for d in menu.get("disabled") or ())) if shown else None,
                       message=str(message.get("text") or "") if message.get("visible", True) else "",
                       parking=str(model.get("parking") or ""), airport=str(airport.get("icao") or ""),
                       prompt=model.get("prompt") or None)


class GsxRemote:
    name = "gsx"

    def __init__(self, *, host: str = "127.0.0.1", port_of: Callable[[], int] = port, retry_s: float = RETRY_S,
                 command_s: float = COMMAND_S) -> None:
        self.host, self.port_of, self.retry_s, self.command_s = host, port_of, retry_s, command_s
        self._ws: WebSocket | None = None
        self._task: asyncio.Task | None = None
        self._model: dict = {}
        self._running = False
        self._capabilities: frozenset[str] = frozenset()
        self._waiting: dict[str, asyncio.Future] = {}
        self._next = 0
        self._on_change: Callable[[], None] = lambda: None
        self._told_down = False

    @property
    def state(self) -> GroundState:
        return parse_state(self._model, connected=self._ws is not None, running=self._running, capabilities=self._capabilities)

    async def start(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                ws = await WebSocket.connect(self.host, self.port_of())
            except (OSError, TimeoutError, WebSocketClosed) as exc:
                if not self._told_down:
                    log.info("GSX's remote server isn't there (%s): trying again", exc)
                    self._told_down = True
                await asyncio.sleep(self.retry_s)
                continue
            self._told_down = False
            self._ws = ws
            try:
                await ws.send(json.dumps({"type": "subscribe", "channels": ["state"]}))
                while True:
                    self._frame(json.loads(await ws.receive()))
            except (WebSocketClosed, ConnectionError, OSError, ValueError) as exc:
                log.info("GSX's remote server went: %s", exc)
            finally:
                self._ws = None
                self._model, self._running = {}, False
                for future in self._waiting.values():
                    if not future.done():
                        future.set_result(Result(False, "not_connected"))
                self._waiting.clear()
                await ws.close()
                self._on_change()
            await asyncio.sleep(self.retry_s)

    def _frame(self, frame: dict) -> None:
        kind = frame.get("type")
        if kind == "hello":
            self._running = bool(frame.get("gsxRunning"))
            self._capabilities = frozenset(str(c).lower() for c in frame.get("capabilities") or ())
            log.info("GSX's remote server: running %s, can do %s", self._running, sorted(self._capabilities))
        elif kind == "snapshot":
            self._model = {k: v for k, v in frame.items() if k not in ("v", "type", "ts", "id")}
        elif kind == "patch":
            key = str(frame.get("path", "")).lstrip("/")
            if frame.get("value") is None:
                self._model.pop(key, None)
            else:
                self._model[key] = frame["value"]
        elif kind == "event" and frame.get("topic") == "engine":
            self._running = bool(frame.get("gsxRunning"))
        elif kind == "result":
            future = self._waiting.pop(str(frame.get("id")), None)
            if future is not None and not future.done():
                error = frame.get("error") or {}
                future.set_result(Result(bool(frame.get("ok")), str(error.get("code") or ""), str(error.get("message") or "")))
            return
        else:
            return
        self._on_change()

    async def _command(self, verb: str, args: dict | None = None) -> Result:
        ws = self._ws
        if ws is None:
            return Result(False, "not_connected")
        self._next += 1
        id_ = f"ltc-{self._next}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[id_] = future
        message = {"type": "command", "id": id_, "verb": verb}
        if args is not None:  # (a verb without arguments is sent without the key)
            message["args"] = args
        try:
            await ws.send(json.dumps(message))
            return await asyncio.wait_for(future, self.command_s)
        except (TimeoutError, asyncio.TimeoutError):
            return Result(False, "timeout")
        except (WebSocketClosed, ConnectionError, OSError):
            return Result(False, "not_connected")
        finally:
            self._waiting.pop(id_, None)

    async def trigger(self, service: str) -> Result:
        return await self._command("service.trigger", {"service": service})

    async def open_menu(self) -> Result:
        return await self._command("menu.open")  # never menu.toggle: on a menu already up, it closes it

    async def pick(self, index: int) -> Result:
        return await self._command("menu.pick", {"index": index})

    async def close_menu(self) -> Result:
        return await self._command("menu.close")

    async def answer_prompt(self, gen: int, text: str) -> Result:
        return await self._command("input.submit", {"gen": gen, "text": text})
