"""``SimConnectSource``: the live MSFS 2024 bridge behind the ``SimSource`` interface.

A dedicated thread owns the SimConnect handle. It sends requests on a fixed
schedule, drains ``GetNextDispatch``, and hands decoded events to the asyncio
loop with ``call_soon_threadsafe``. If the sim isn't running or quits, the
thread reconnects with back-off and reports ``ConnectionStatus`` events.
"""

import asyncio
import logging
import queue
import struct
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Protocol

from msgspec.structs import replace

from localtc.config import LiveConfig
from localtc.sim_api import (
    AircraftSystems,
    AirportData,
    BusEvent,
    ConnectionStatus,
    IntercomPressed,
    IntercomReleased,
    NearbyAirport,
    NearbyAirports,
    SessionClock,
    SessionInfo,
    PttPressed,
    PttReleased,
    RequestAirportData,
    RequestArrival,
    SendSimEvent,
    SetComFrequency,
    SetSimVar,
    SimCommand,
    SimLifecycle,
    TrafficSnapshot,
    TrafficTarget,
)
from localtc.sim_bridge import definitions as defs
from localtc.sim_bridge import arrivals, facilities
from localtc.sim_bridge.dll import SimConnectDll, SimConnectError, find_dll
from localtc.sim_bridge.protocol import (
    EVENT_FLAG_GROUPID_IS_PRIORITY,
    GROUP_PRIORITY_HIGHEST,
    OBJECT_ID_USER,
    PAUSE_FLAGS,
    AirportList,
    DataType,
    FACILITY_IDS,
    EventInfo,
    ExceptionInfo,
    FacilityData,
    FacilityDataEnd,
    FacilityListType,
    ObjectData,
    OpenInfo,
    Period,
    ProtocolError,
    QuitInfo,
    Recv,
    RecvId,
    RequestFlag,
    SimObjectType,
    parse_message,
)

log = logging.getLogger(__name__)

DEF_OWNSHIP, DEF_IDENTITY, DEF_TRAFFIC, DEF_AIRCRAFT, DEF_AIRCRAFT_EXTRA, DEF_FACILITY_AIRPORT = 1, 2, 3, 4, 5, 10
DEF_AIRCRAFT_MORE, DEF_FACILITY_ARRIVALS = 6, 11
REQ_OWNSHIP, REQ_IDENTITY, REQ_TRAFFIC, REQ_AIRPORT_LIST, REQ_AIRCRAFT, REQ_AIRCRAFT_EXTRA = 1, 2, 3, 4, 5, 6
REQ_AIRCRAFT_MORE = 7
FIRST_SIMVAR_DEFINITION = 50  # one data definition per variable the copilot writes (L:vars), from here up
FIRST_FACILITY_REQUEST = 100
FACILITY_TIMEOUT_S = 60.0
FACILITY_MESSAGES = {RecvId.AIRPORT_LIST, *FACILITY_IDS}
NEARBY_AIRPORTS, NEARBY_NM = 40, 150.0  # the airports around the aircraft reported to ATC, for diversions

EVT_SIM_START, EVT_SIM_STOP, EVT_PAUSE, EVT_FLIGHT_LOADED, EVT_AIRCRAFT_LOADED, EVT_CRASHED = range(1, 7)
# Client events LocalTC sends to the sim (same ID space as the system events above).
EVT_COM1_SET_HZ, EVT_COM2_SET_HZ = 20, 21
CLIENT_EVENTS = {EVT_COM1_SET_HZ: "COM_RADIO_SET_HZ", EVT_COM2_SET_HZ: "COM2_RADIO_SET_HZ"}
EVT_PTT_DOWN, EVT_PTT_UP = 30, 31  # push-to-talk from a joystick button or key bound through the sim
EVT_INTERCOM_DOWN, EVT_INTERCOM_UP = 32, 33  # the intercom (talking to the copilot), the same way
FIRST_COPILOT_EVENT = 100  # key events the copilot sends ("GEAR_DOWN"), mapped as first used, from here up
GROUP_PTT, GROUP_INTERCOM = 1, 2
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
    def add_to_facility_definition(self, handle: int, define_id: int, field: str) -> None: ...
    def request_facility_data(self, handle: int, define_id: int, request_id: int, icao: str, region: str = "") -> None: ...
    def request_facilities_list(self, handle: int, list_type: FacilityListType, request_id: int) -> None: ...
    def map_client_event_to_sim_event(self, handle: int, event_id: int, name: str) -> None: ...
    def transmit_client_event(self, handle: int, object_id: int, event_id: int, data: int, group: int, flags: int) -> None: ...
    def map_input_to_events(self, handle: int, group: int, definition: str, down_event: int, up_event: int) -> None: ...
    def set_data_on_sim_object(self, handle: int, define_id: int, object_id: int, data: bytes) -> None: ...
    def get_next_dispatch(self, handle: int) -> bytes | None: ...


class SimConnectSource:
    def __init__(
        self,
        cfg: LiveConfig | None = None,
        *,
        dll_factory: Callable[[], SimConnectApi] | None = None,
        clock: SessionClock | None = None,
        raw_tap: Callable[[bytes], None] | None = None,
    ) -> None:
        """``raw_tap`` receives every raw facility message (for ``localtc debug``)."""
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
        self._raw_tap = raw_tap
        self._commands: queue.SimpleQueue[SimCommand] = queue.SimpleQueue()
        self._systems: AircraftSystems | None = None  # the last switches sent on
        self._aircraft_raw: dict | None = None  # the latest of each of the three requests they're made from
        self._extra_raw: dict | None = None
        self._more_raw: dict | None = None
        self._arrival_assemblers: dict[int, arrivals.ArrivalAssembler] = {}
        self._copilot_events: dict[str, int] = {}  # key event name -> client event id, this connection
        self._simvar_definitions: dict[str, int] = {}  # variable -> data definition id, this connection
        self._position: tuple[float, float] | None = None
        self._assemblers: dict[int, facilities.AirportAssembler] = {}
        self._fetched_airports: set[str] = set()
        self._airport_list: list = []
        self._airport_list_received = 0
        self._nearest_to_fetch: str | None = None
        self._next_facility_request = FIRST_FACILITY_REQUEST

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

    async def send(self, command: SimCommand) -> None:
        """Queue a command for the bridge thread; handled once connected."""
        self._commands.put(command)

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
        self._copilot_events: dict[str, int] = {}
        self._simvar_definitions: dict[str, int] = {}
        for define_id, datums in ((DEF_OWNSHIP, defs.OWNSHIP), (DEF_IDENTITY, defs.IDENTITY), (DEF_TRAFFIC, defs.TRAFFIC),
                                  (DEF_AIRCRAFT, defs.AIRCRAFT), (DEF_AIRCRAFT_EXTRA, defs.AIRCRAFT_EXTRA),
                                  (DEF_AIRCRAFT_MORE, defs.AIRCRAFT_MORE)):
            for d in datums:
                dll.add_to_data_definition(handle, define_id, d.simvar, d.units, d.datatype)
        for event_id, name in SYSTEM_EVENTS.items():
            dll.subscribe_to_system_event(handle, event_id, name)
        for event_id, name in CLIENT_EVENTS.items():
            try:
                dll.map_client_event_to_sim_event(handle, event_id, name)
            except SimConnectError as exc:  # the copilot can't tune, but everything else works
                log.warning("Can't map %s: %s", name, exc)
        if self._cfg.ptt_input:
            try:
                dll.map_input_to_events(handle, GROUP_PTT, self._cfg.ptt_input, EVT_PTT_DOWN, EVT_PTT_UP)
                log.info("Push-to-talk: %s (through the sim)", self._cfg.ptt_input)
            except SimConnectError as exc:
                log.warning("Can't use %s as push-to-talk: %s", self._cfg.ptt_input, exc)
        if self._cfg.intercom_input:
            try:
                dll.map_input_to_events(handle, GROUP_INTERCOM, self._cfg.intercom_input, EVT_INTERCOM_DOWN, EVT_INTERCOM_UP)
                log.info("Intercom: %s (through the sim)", self._cfg.intercom_input)
            except SimConnectError as exc:
                log.warning("Can't use %s for the intercom: %s", self._cfg.intercom_input, exc)
        dll.request_data_on_sim_object(
            handle, REQ_IDENTITY, DEF_IDENTITY, OBJECT_ID_USER, Period.SECOND, RequestFlag.CHANGED
        )
        dll.request_data_on_sim_object(
            handle, REQ_AIRCRAFT, DEF_AIRCRAFT, OBJECT_ID_USER, Period.SECOND, RequestFlag.CHANGED
        )
        dll.request_data_on_sim_object(
            handle, REQ_AIRCRAFT_EXTRA, DEF_AIRCRAFT_EXTRA, OBJECT_ID_USER, Period.SECOND, RequestFlag.CHANGED
        )
        dll.request_data_on_sim_object(
            handle, REQ_AIRCRAFT_MORE, DEF_AIRCRAFT_MORE, OBJECT_ID_USER, Period.SECOND, RequestFlag.CHANGED
        )
        for line in facilities.definition_lines():
            dll.add_to_facility_definition(handle, DEF_FACILITY_AIRPORT, line)
        self._arrivals_ok = True
        try:  # the arrivals' restrictions, for the copilot: a sim that won't take it loses only these
            for line in arrivals.definition_lines():
                dll.add_to_facility_definition(handle, DEF_FACILITY_ARRIVALS, line)
        except SimConnectError as exc:
            self._arrivals_ok = False
            log.warning("Arrival procedures not available from this sim: %s", exc)
        self._arrival_assemblers.clear()
        self._assemblers.clear()
        self._fetched_airports.clear()
        nearest_period = self._cfg.nearest_airport_interval_s if self._cfg.nearest_airport_interval_s > 0 else None
        next_nearest = 0.0

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
                if nearest_period and self._position is not None and now >= next_nearest:
                    self._airport_list, self._airport_list_received = [], 0
                    dll.request_facilities_list(handle, FacilityListType.AIRPORT, REQ_AIRPORT_LIST)
                    next_nearest = now + nearest_period
                self._run_commands(dll, handle)
                if self._nearest_to_fetch:
                    self._request_airport(dll, handle, self._nearest_to_fetch)
                    self._nearest_to_fetch = None
                self._expire_assemblers()
            self._stop.wait(POLL_INTERVAL_S)

    def _handle(self, buf: bytes) -> bool:
        """Handle one message; returns False when the sim has quit."""
        if self._raw_tap is not None and Recv.from_buffer_copy(buf[:12]).dwID in FACILITY_MESSAGES:
            self._raw_tap(buf)
        try:
            msg = parse_message(buf)
        except ProtocolError as exc:
            # One malformed or unexpected message must not drop the whole connection.
            log.warning("Skipping unparseable SimConnect message: %s", exc)
            return True
        t = self._clock.now()
        if isinstance(msg, OpenInfo):
            self._on_open(msg)
        elif isinstance(msg, QuitInfo):
            return False
        elif isinstance(msg, ExceptionInfo):
            log.warning("SimConnect exception %s (send id %d, index %d)", msg.name, msg.send_id, msg.index)
        elif isinstance(msg, EventInfo):
            if msg.event_id == EVT_PTT_DOWN:
                self._emit(PttPressed(t=t))
            elif msg.event_id == EVT_PTT_UP:
                self._emit(PttReleased(t=t))
            elif msg.event_id == EVT_INTERCOM_DOWN:
                self._emit(IntercomPressed(t=t))
            elif msg.event_id == EVT_INTERCOM_UP:
                self._emit(IntercomReleased(t=t))
            elif (event := _lifecycle_event(msg, t)) is not None:
                self._emit(event)
        elif isinstance(msg, ObjectData):
            self._on_data(msg, t)
        elif isinstance(msg, FacilityData):
            if (assembler := self._assemblers.get(msg.request_id)) is not None:
                assembler.add(msg)
            elif (arrival := self._arrival_assemblers.get(msg.request_id)) is not None:
                arrival.add(msg)
        elif isinstance(msg, FacilityDataEnd):
            if (arrival := self._arrival_assemblers.pop(msg.request_id, None)) is not None:
                data = arrival.build(t)
                log.info("Arrival %s at %s: %d legs", arrival.name, arrival.icao, len(data.legs))
                self._emit(data)
            else:
                self._on_facility_end(msg, t)
        elif isinstance(msg, AirportList) and msg.request_id == REQ_AIRPORT_LIST:
            self._on_airport_list(msg)
        return True

    # --- airport data -----------------------------------------------------------

    def _run_commands(self, dll: SimConnectApi, handle: int) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if isinstance(command, RequestAirportData):
                self._request_airport(dll, handle, command.icao.upper(), force=True)
            elif isinstance(command, RequestArrival):
                self._request_arrival(dll, handle, command.icao.upper(), command.name.upper())
            elif isinstance(command, SetComFrequency):
                event_id = EVT_COM2_SET_HZ if command.radio == 2 else EVT_COM1_SET_HZ
                try:
                    dll.transmit_client_event(handle, OBJECT_ID_USER, event_id, command.hz, GROUP_PRIORITY_HIGHEST,
                                              EVENT_FLAG_GROUPID_IS_PRIORITY)
                    log.info("Tuning COM%d to %.3f", command.radio, command.hz / 1e6)
                except SimConnectError as exc:
                    log.warning("Couldn't tune COM%d: %s", command.radio, exc)
            elif isinstance(command, SendSimEvent):
                self._send_event(dll, handle, command)
            elif isinstance(command, SetSimVar):
                self._set_simvar(dll, handle, command)
            else:
                log.warning("unsupported command %r", command)

    def _send_event(self, dll: SimConnectApi, handle: int, command: SendSimEvent) -> None:
        """A key event from the copilot ("GEAR_DOWN"), mapped to a client event the first time it's sent."""
        try:
            event_id = self._copilot_events.get(command.name)
            if event_id is None:
                event_id = FIRST_COPILOT_EVENT + len(self._copilot_events)
                dll.map_client_event_to_sim_event(handle, event_id, command.name)
                self._copilot_events[command.name] = event_id
            dll.transmit_client_event(handle, OBJECT_ID_USER, event_id, command.value, GROUP_PRIORITY_HIGHEST,
                                      EVENT_FLAG_GROUPID_IS_PRIORITY)
            log.info("Copilot: %s %s", command.name, command.value)
        except SimConnectError as exc:
            log.warning("Copilot couldn't send %s: %s", command.name, exc)

    def _set_simvar(self, dll: SimConnectApi, handle: int, command: SetSimVar) -> None:
        """A variable from the copilot (an add-on's L:var), through a data definition of its own."""
        try:
            define_id = self._simvar_definitions.get(command.name)
            if define_id is None:
                define_id = FIRST_SIMVAR_DEFINITION + len(self._simvar_definitions)
                dll.add_to_data_definition(handle, define_id, command.name, command.unit, defs.F64)
                self._simvar_definitions[command.name] = define_id
            dll.set_data_on_sim_object(handle, define_id, OBJECT_ID_USER, struct.pack("<d", command.value))
            log.info("Copilot: %s = %s", command.name, command.value)
        except SimConnectError as exc:
            log.warning("Copilot couldn't set %s: %s", command.name, exc)

    def _request_airport(self, dll: SimConnectApi, handle: int, icao: str, *, force: bool = False) -> None:
        if not facilities.plausible_ident(icao):
            return
        if not force and icao in self._fetched_airports:
            return
        if any(a.icao == icao for a in self._assemblers.values()):
            return  # already in flight
        request_id = self._next_facility_request
        self._next_facility_request += 1
        self._assemblers[request_id] = facilities.AirportAssembler(icao, started_t=time.monotonic())
        self._fetched_airports.add(icao)
        dll.request_facility_data(handle, DEF_FACILITY_AIRPORT, request_id, icao)
        log.info("Requesting airport data for %s", icao)

    def _request_arrival(self, dll: SimConnectApi, handle: int, icao: str, name: str) -> None:
        if not getattr(self, "_arrivals_ok", False) or not facilities.plausible_ident(icao) or not name:
            return
        request_id = self._next_facility_request
        self._next_facility_request += 1
        self._arrival_assemblers[request_id] = arrivals.ArrivalAssembler(icao, name, started_t=time.monotonic())
        try:
            dll.request_facility_data(handle, DEF_FACILITY_ARRIVALS, request_id, icao)
            log.info("Requesting arrival %s at %s", name, icao)
        except SimConnectError as exc:
            self._arrival_assemblers.pop(request_id, None)
            log.warning("Couldn't ask for arrival %s at %s: %s", name, icao, exc)

    def _on_facility_end(self, msg: FacilityDataEnd, t: float) -> None:
        assembler = self._assemblers.pop(msg.request_id, None)
        if assembler is None:
            return
        log.info("Facility data: %s", assembler.report())
        airport = assembler.build()
        if airport is None:
            log.warning("No airport data returned for %s", assembler.icao)
            self._fetched_airports.discard(assembler.icao)
            return
        self._emit(AirportData(t=t, airport=airport))

    def _on_airport_list(self, msg: AirportList) -> None:
        self._airport_list.extend(msg.airports)
        self._airport_list_received += 1
        if self._airport_list_received < max(msg.out_of, 1) or self._position is None:
            return
        lat, lon = self._position
        # An ident that isn't letters and digits ("J@" turned up in the far north) isn't an airport: asking the sim
        # for its data only gets an exception back, and then a timeout.
        candidates = [a for a in self._airport_list if facilities.plausible_ident(a.icao)]
        if not candidates:
            return
        by_distance = sorted(candidates, key=lambda a: facilities.haversine_nm(lat, lon, a.lat, a.lon))
        self._nearest_to_fetch = by_distance[0].icao
        # The ones around, for ATC to pick a diversion from in an emergency (their layouts are fetched then).
        near = [a for a in by_distance[:NEARBY_AIRPORTS] if facilities.haversine_nm(lat, lon, a.lat, a.lon) <= NEARBY_NM]
        self._emit(NearbyAirports(t=self._clock.now(), airports=tuple(
            NearbyAirport(icao=a.icao, lat=round(a.lat, 5), lon=round(a.lon, 5), elev_ft=round(a.alt_m * 3.28084))
            for a in near)))

    def _expire_assemblers(self) -> None:
        now = time.monotonic()
        for request_id, assembler in list(self._assemblers.items()):
            if now - assembler.started_t > FACILITY_TIMEOUT_S:
                log.warning("Timed out waiting for airport data for %s", assembler.icao)
                self._fetched_airports.discard(assembler.icao)
                del self._assemblers[request_id]
        for request_id, arrival in list(self._arrival_assemblers.items()):
            if now - arrival.started_t > FACILITY_TIMEOUT_S:
                log.warning("Timed out waiting for arrival %s at %s", arrival.name, arrival.icao)
                del self._arrival_assemblers[request_id]

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
            ownship = defs.ownship_from_raw(defs.unpack(defs.OWNSHIP, msg.payload), t)
            self._position = (ownship.lat, ownship.lon)
            self._emit(ownship)
        elif msg.request_id == REQ_IDENTITY:
            self._emit(defs.identity_from_raw(defs.unpack(defs.IDENTITY, msg.payload), t))
        elif msg.request_id in (REQ_AIRCRAFT, REQ_AIRCRAFT_EXTRA, REQ_AIRCRAFT_MORE):
            if msg.request_id == REQ_AIRCRAFT:
                self._aircraft_raw = defs.unpack(defs.AIRCRAFT, msg.payload)
            elif msg.request_id == REQ_AIRCRAFT_EXTRA:
                self._extra_raw = defs.unpack(defs.AIRCRAFT_EXTRA, msg.payload)
            else:
                self._more_raw = defs.unpack(defs.AIRCRAFT_MORE, msg.payload)
            if self._aircraft_raw is None:
                return
            systems = defs.systems_from_raw(self._aircraft_raw, t, self._extra_raw, self._more_raw)
            if self._systems is None or replace(systems, t=self._systems.t) != self._systems:  # only what's kept changing
                self._systems = systems
                self._emit(systems)
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
