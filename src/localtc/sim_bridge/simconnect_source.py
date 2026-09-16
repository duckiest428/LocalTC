"""``SimConnectSource``: the live MSFS 2024 bridge behind the ``SimSource`` interface.

A dedicated thread owns the SimConnect handle. It sends requests on a fixed
schedule, drains ``GetNextDispatch``, and hands decoded events to the asyncio
loop with ``call_soon_threadsafe``. If the sim isn't running or quits, the
thread reconnects with back-off and reports ``ConnectionStatus`` events.
"""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Protocol

from localtc.config import LiveConfig
from localtc.sim_api import (
    BusEvent,
    ConnectionStatus,
    SessionClock,
    SessionInfo,
    SimLifecycle,
    TrafficSnapshot,
    TrafficTarget,
)
from localtc.sim_bridge import definitions as defs
from localtc.sim_bridge.dll import SimConnectDll, SimConnectError, find_dll
from localtc.sim_bridge.protocol import (
    OBJECT_ID_USER,
    PAUSE_FLAGS,
    DataType,
    EventInfo,
    ExceptionInfo,
    ObjectData,
    OpenInfo,
    Period,
    QuitInfo,
    RequestFlag,
    SimObjectType,
    parse_message,
)

log = logging.getLogger(__name__)

DEF_OWNSHIP, DEF_IDENTITY, DEF_TRAFFIC = 1, 2, 3
REQ_OWNSHIP, REQ_IDENTITY, REQ_TRAFFIC = 1, 2, 3

EVT_SIM_START, EVT_SIM_STOP, EVT_PAUSE, EVT_FLIGHT_LOADED, EVT_AIRCRAFT_LOADED, EVT_CRASHED = range(1, 7)
SYSTEM_EVENTS = {
    EVT_SIM_START: "SimStart",
    EVT_SIM_STOP: "SimStop",
    EVT_PAUSE: "Pause_EX1",
    EVT_FLIGHT_LOADED: "FlightLoaded",
    EVT_AIRCRAFT_LOADED: "AircraftLoaded",
    EVT_CRASHED: "Crashed",
}

# SIMCONNECT_RECV_OPEN.dwApplicationVersionMajor
SIM_PRODUCTS = {11: "MSFS 2020", 12: "MSFS 2024"}
TARGET_SIM_MAJOR = 12

OPEN_TIMEOUT_S = 10.0
POLL_INTERVAL_S = 0.005

_STOP = object()


class SimConnectApi(Protocol):
    """What the source needs from the DLL; tests substitute a fake."""

    def open(self, app_name: str) -> int: ...
    def close(self, handle: int) -> None: ...
    def add_to_data_definition(
        self, handle: int, define_id: int, simvar: str, units: str | None, datatype: DataType
    ) -> None: ...
    def request_data_on_sim_object(
        self, handle: int, request_id: int, define_id: int, object_id: int, period: Period, flags: int = 0
    ) -> None: ...
    def request_data_on_sim_object_type(
        self, handle: int, request_id: int, define_id: int, radius_m: int, object_type: SimObjectType
    ) -> None: ...
    def subscribe_to_system_event(self, handle: int, event_id: int, name: str) -> None: ...
    def get_next_dispatch(self, handle: int) -> bytes | None: ...


class SimConnectSource:
    def __init__(
        self,
        cfg: LiveConfig | None = None,
        *,
        dll_factory: Callable[[], SimConnectApi] | None = None,
        clock: SessionClock | None = None,
    ) -> None:
        self._cfg = cfg or LiveConfig()
        self._dll_factory = dll_factory or (lambda: SimConnectDll(find_dll(self._cfg.dll_path or None)))
        self._clock = clock or SessionClock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._ready: asyncio.Future[SessionInfo] | None = None
        # Owned by the bridge thread:
        self._open: OpenInfo | None = None
        self._user_object_id: int | None = None
        self._traffic = _TrafficRound()
        self._last_connected: bool | None = None

    @property
    def clock(self) -> SessionClock:
        return self._clock

    async def start(self) -> SessionInfo:
        """Start the bridge thread and wait until the sim accepts the connection.

        Waits indefinitely unless ``live.connect_timeout_s`` is set. Raises
        ``SimConnectUnavailable`` if SimConnect can't be loaded at all.
        """
        if self._thread is not None:
            raise RuntimeError("source already started")
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._ready = self._loop.create_future()
        self._clock.reset()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="localtc-simconnect", daemon=True)
        self._thread.start()
        try:
            return await asyncio.wait_for(self._ready, self._cfg.connect_timeout_s or None)
        except BaseException:
            await self.stop()
            raise

    async def events(self) -> AsyncIterator[BusEvent]:
        if self._queue is None:
            raise RuntimeError("call start() first")
        while True:
            item = await self._queue.get()
            if item is _STOP:
                self._queue.put_nowait(_STOP)
                return
            yield item

    async def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5.0)
        if self._queue is not None:
            self._queue.put_nowait(_STOP)

    # --- bridge thread ------------------------------------------------------

    def _run(self) -> None:
        try:
            dll = self._dll_factory()
        except Exception as exc:
            log.error("SimConnect unavailable: %s", exc)
            self._resolve_ready(error=exc)
            return

        backoff = 1.0
        while not self._stop.is_set():
            try:
                handle = dll.open(self._cfg.app_name)
            except SimConnectError as exc:
                self._set_connected(False, f"waiting for simulator ({exc})")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self._cfg.retry_max_s)
                continue

            backoff = 1.0
            detail = "simulator closed the connection"
            try:
                self._session(dll, handle)
            except SimConnectError as exc:
                detail = str(exc)
                log.warning("SimConnect session ended: %s", exc)
            except Exception:
                detail = "internal error in SimConnect bridge"
                log.exception(detail)
            finally:
                with suppress(Exception):
                    dll.close(handle)
                self._open = None
                self._user_object_id = None
            if not self._stop.is_set():
                self._set_connected(False, detail)
                self._stop.wait(1.0)

    def _session(self, dll: SimConnectApi, handle: int) -> None:
        for define_id, datums in ((DEF_OWNSHIP, defs.OWNSHIP), (DEF_IDENTITY, defs.IDENTITY), (DEF_TRAFFIC, defs.TRAFFIC)):
            for d in datums:
                dll.add_to_data_definition(handle, define_id, d.simvar, d.units, d.datatype)
        for event_id, name in SYSTEM_EVENTS.items():
            dll.subscribe_to_system_event(handle, event_id, name)
        dll.request_data_on_sim_object(
            handle, REQ_IDENTITY, DEF_IDENTITY, OBJECT_ID_USER, Period.SECOND, RequestFlag.CHANGED
        )

        own_period = 1.0 / self._cfg.ownship_hz if self._cfg.ownship_hz > 0 else None
        traffic_period = self._cfg.traffic_interval_s if self._cfg.traffic_interval_s > 0 else None
        opened_at = time.monotonic()
        next_own = next_traffic = 0.0
        self._traffic = _TrafficRound()

        while not self._stop.is_set():
            while (buf := dll.get_next_dispatch(handle)) is not None:
                if not self._handle(buf):
                    return

            now = time.monotonic()
            if self._open is None:
                if now - opened_at > OPEN_TIMEOUT_S:
                    raise SimConnectError("no OPEN response from simulator")
            else:
                # PERIOD_ONCE on our own schedule gives a steady rate independent of sim FPS.
                if own_period and now >= next_own:
                    dll.request_data_on_sim_object(handle, REQ_OWNSHIP, DEF_OWNSHIP, OBJECT_ID_USER, Period.ONCE)
                    next_own = _next_deadline(next_own, own_period, now)
                if traffic_period and now >= next_traffic:
                    self._emit_traffic(self._traffic.begin())
                    dll.request_data_on_sim_object_type(
                        handle, REQ_TRAFFIC, DEF_TRAFFIC, self._cfg.traffic_radius_m, SimObjectType.AIRCRAFT
                    )
                    next_traffic = _next_deadline(next_traffic, traffic_period, now)
            self._stop.wait(POLL_INTERVAL_S)

    def _handle(self, buf: bytes) -> bool:
        """Handle one message; returns False when the sim has quit."""
        msg = parse_message(buf)
        t = self._clock.now()
        if isinstance(msg, OpenInfo):
            self._on_open(msg)
        elif isinstance(msg, QuitInfo):
            return False
        elif isinstance(msg, ExceptionInfo):
            log.warning("SimConnect exception %s (send id %d, index %d)", msg.name, msg.send_id, msg.index)
        elif isinstance(msg, EventInfo):
            if (event := _lifecycle_event(msg, t)) is not None:
                self._emit(event)
        elif isinstance(msg, ObjectData):
            self._on_data(msg, t)
        return True

    def _on_open(self, msg: OpenInfo) -> None:
        self._open = msg
        major = msg.app_version[0]
        product = SIM_PRODUCTS.get(major, f"unknown sim (v{major})")
        if major != TARGET_SIM_MAJOR:
            log.warning("Connected to %s; LocalTC targets MSFS 2024", product)
        info = SessionInfo(
            source_kind="live",
            sim_product=product,
            sim_version=".".join(map(str, msg.app_version)),
            simconnect_version=".".join(map(str, msg.simconnect_version)),
        )
        self._set_connected(True, f"{product} {info.sim_version} ({msg.app_name})")
        self._resolve_ready(info)

    def _on_data(self, msg: ObjectData, t: float) -> None:
        if msg.request_id == REQ_OWNSHIP:
            self._user_object_id = msg.object_id
            self._emit(defs.ownship_from_raw(defs.unpack(defs.OWNSHIP, msg.payload), t))
        elif msg.request_id == REQ_IDENTITY:
            self._emit(defs.identity_from_raw(defs.unpack(defs.IDENTITY, msg.payload), t))
        elif msg.request_id == REQ_TRAFFIC:
            target = None
            has_data = msg.out_of > 0 and len(msg.payload) >= defs.payload_size(defs.TRAFFIC)
            if has_data and msg.object_id != self._user_object_id:
                target = defs.traffic_from_raw(defs.unpack(defs.TRAFFIC, msg.payload), msg.object_id)
            self._emit_traffic(self._traffic.add(target, msg.out_of), t)

    def _emit_traffic(self, targets: tuple[TrafficTarget, ...] | None, t: float | None = None) -> None:
        if targets is not None:
            self._emit(TrafficSnapshot(t=self._clock.now() if t is None else t, targets=targets))

    def _set_connected(self, connected: bool, detail: str) -> None:
        if not connected and self._last_connected is False:
            return  # already reported; don't repeat on every retry
        self._last_connected = connected
        self._emit(ConnectionStatus(t=self._clock.now(), connected=connected, detail=detail))

    def _emit(self, event: BusEvent) -> None:
        with suppress(RuntimeError):  # loop already closed during shutdown
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def _resolve_ready(self, info: SessionInfo | None = None, error: BaseException | None = None) -> None:
        def apply() -> None:
            if self._ready.done():
                return
            if error is not None:
                self._ready.set_exception(error)
            else:
                self._ready.set_result(info)

        with suppress(RuntimeError):
            self._loop.call_soon_threadsafe(apply)


class _TrafficRound:
    """Collects the per-aircraft replies of one RequestDataOnSimObjectType into a snapshot.

    Counting replies against ``out_of`` works whether the sim numbers entries
    from 0 or 1. If a round gets no replies at all (nothing in range), the next
    ``begin()`` reports it as an empty snapshot.
    """

    def __init__(self) -> None:
        self._targets: list[TrafficTarget] = []
        self._received = 0
        self._active = False

    def begin(self) -> tuple[TrafficTarget, ...] | None:
        unfinished = tuple(self._targets) if self._active else None
        self._targets, self._received, self._active = [], 0, True
        return unfinished

    def add(self, target: TrafficTarget | None, out_of: int) -> tuple[TrafficTarget, ...] | None:
        if not self._active:
            return None  # late reply for a round already reported
        self._received += 1
        if target is not None:
            self._targets.append(target)
        if out_of == 0 or self._received >= out_of:
            self._active = False
            return tuple(self._targets)
        return None


def _lifecycle_event(msg: EventInfo, t: float) -> SimLifecycle | None:
    if msg.event_id == EVT_SIM_START:
        return SimLifecycle(t=t, kind="sim_start")
    if msg.event_id == EVT_SIM_STOP:
        return SimLifecycle(t=t, kind="sim_stop")
    if msg.event_id == EVT_PAUSE:
        if msg.data == 0:
            return SimLifecycle(t=t, kind="unpaused")
        flags = ",".join(name for bit, name in PAUSE_FLAGS.items() if msg.data & bit)
        return SimLifecycle(t=t, kind="paused", detail=flags)
    if msg.event_id == EVT_FLIGHT_LOADED:
        return SimLifecycle(t=t, kind="flight_loaded", detail=msg.filename or "")
    if msg.event_id == EVT_AIRCRAFT_LOADED:
        return SimLifecycle(t=t, kind="aircraft_loaded", detail=msg.filename or "")
    if msg.event_id == EVT_CRASHED:
        return SimLifecycle(t=t, kind="crashed")
    return None


def _next_deadline(previous: float, period: float, now: float) -> float:
    candidate = previous + period
    return candidate if candidate > now else now + period
