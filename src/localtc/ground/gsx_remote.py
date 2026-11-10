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
from localtc.ground.menu import stand
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


def parking_token(gate: str, handler_data: dict | None) -> str:
    """The name GSX knows a stand by ("G13" is its "Gate G 13"), from its list of the airport's parkings; ``gate`` as it
    is where the list isn't there or more than one fits (GSX's own search then answers for it)."""
    airport = (handler_data or {}).get("airport") or {}
    hits = {name for p in airport.get("parkings") or () if isinstance(p, dict)
            and (name := p.get("uiGateName") or p.get("uiName")) and stand(str(name)) == stand(gate)}
    return str(next(iter(hits))) if len(hits) == 1 else gate


def setting(settings: dict | None, key: str) -> dict | None:
    """One of GSX's settings, from the schema it sends (tabs, subtabs, fields)."""
    for tab in (settings or {}).get("tabs") or ():
        for sub in tab.get("subtabs") or (tab,):
            for item in sub.get("fields") or ():
                if item.get("key") == key:
                    return item
    return None


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


GATE_DONE = ("already_selected", "already_parked")  # gate.select: nothing to do, the stand is already the one
# GSX's settings for how fast a service goes (its "Timings" tab).
PACE = {"Boarding": "pax_density", "Deboarding": "pax_density_deboarding"}


class GsxRemote:
    name = "gsx"

    def __init__(self, *, host: str = "127.0.0.1", port_of: Callable[[], int] = port, retry_s: float = RETRY_S,
                 command_s: float = COMMAND_S, paces_file: Path | None = None) -> None:
        self.host, self.port_of, self.retry_s, self.command_s = host, port_of, retry_s, command_s
        # GSX's settings outlive this program: what a hurry changed is written down first, and a run that ended before
        # putting it back (a crash, the sim gone) has the next one do it.
        self.paces_file = paces_file
        self._paces: dict[str, int] = self._read_paces()  # setting -> the value to put back
        self._owed: set[str] = set(self._paces)  # ... and those no service is hurrying any more
        self._putting_back: asyncio.Task | None = None
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
        if self._ws is not None:
            self._owed |= set(self._paces)
            await self._put_back()
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
                if self._owed:
                    self._putting_back = asyncio.create_task(self._put_back())
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
                future.set_result(Result(bool(frame.get("ok")), str(error.get("code") or ""), str(error.get("message") or ""),
                                         frame.get("payload") or {}))
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

    async def select_gate(self, gate: str) -> Result:
        """Only where ``hello`` lists ``gate``. Already there counts as done (GSX answers it as a refusal); a stand GSX has
        an AI aircraft on is taken all the same, ATC having given it. Services running at a stand are never revoked."""
        args = {"gate": parking_token(gate, self._model.get("handlerData")), "revokeServices": False, "force": False}
        result = await self._command("gate.select", args)
        if result.code == "assigned_to_other":
            result = await self._command("gate.select", {**args, "force": True})
        return Result(True, result.code) if result.code in GATE_DONE else result

    async def speed_up(self, service: str) -> Result:
        """Passengers as dense as GSX goes, for this service. Only where ``hello`` lists ``settings``; the setting's name,
        its values and where it stands are read from GSX each time, and a build without it just says no."""
        key = PACE.get(service)
        if key is None or "settings" not in self._capabilities:
            return Result(False, "no_setting")
        got = await self._command("settings.get", {})
        if not got.ok:
            return got
        item = setting(got.payload.get("settings") or self._model.get("settings"), key)
        values = [c[0] for c in (item or {}).get("choices") or () if isinstance(c, list | tuple) and c]
        if item is None or not values or not isinstance(item.get("value"), int):
            return Result(False, "no_setting")
        if item["value"] >= max(values):
            return Result(True, "at_most")
        self._paces.setdefault(key, item["value"])
        self._owed.discard(key)
        self._write_paces()
        result = await self._command("settings.set", {"key": key, "value": max(values)})
        if not result.ok:
            self._paces.pop(key, None)
            self._write_paces()
        return result

    async def slow_down(self, service: str) -> Result:
        key = PACE.get(service)
        if key is None or key not in self._paces:
            return Result(True)
        self._owed.add(key)
        await self._put_back()
        return Result(key not in self._paces)

    async def _put_back(self) -> None:
        """The settings a hurry changed, as they were. What can't be done now stays owed, for the next connection."""
        if not self._owed:
            return
        for _ in range(20):  # (just connected: its ``hello`` first)
            if self._capabilities or self._ws is None:
                break
            await asyncio.sleep(0.1)
        for key in sorted(self._owed):
            if (await self._command("settings.set", {"key": key, "value": self._paces[key]})).ok:
                self._paces.pop(key)
                self._owed.discard(key)
        self._write_paces()

    def _read_paces(self) -> dict[str, int]:
        try:
            found = json.loads(self.paces_file.read_text(encoding="utf-8")) if self.paces_file else {}
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in found.items() if k in PACE.values() and isinstance(v, int)} if isinstance(found, dict) else {}

    def _write_paces(self) -> None:
        if self.paces_file is None:
            return
        try:
            if self._paces:
                self.paces_file.parent.mkdir(parents=True, exist_ok=True)
                self.paces_file.write_text(json.dumps(self._paces), encoding="utf-8")
            else:
                self.paces_file.unlink(missing_ok=True)
        except OSError:
            log.exception("GSX's pace settings couldn't be noted down")

    async def open_menu(self) -> Result:
        return await self._command("menu.open")  # never menu.toggle: on a menu already up, it closes it

    async def pick(self, index: int) -> Result:
        return await self._command("menu.pick", {"index": index})

    async def close_menu(self) -> Result:
        return await self._command("menu.close")

    async def answer_prompt(self, gen: int, text: str) -> Result:
        return await self._command("input.submit", {"gen": gen, "text": text})
