"""``SimConnectSource``: the live MSFS 2024 bridge behind the ``SimSource`` interface.

A dedicated thread owns the SimConnect handle. It sends requests on a fixed
schedule, drains ``GetNextDispatch``, and hands decoded events to the asyncio
loop with ``call_soon_threadsafe``. If the sim isn't running or quits, the
thread reconnects with back-off and reports ``ConnectionStatus`` events.
"""

import asyncio
import logging
import queue
import re
import struct
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Protocol

from msgspec.structs import replace

from localtc.config import LiveConfig
from localtc.sim_api import (
    AircraftInputEvents,
    AircraftSystems,
    AirportData,
    BusEvent,
    ConnectionStatus,
    NearbyAirport,
    NearbyAirports,
    SessionClock,
    SessionInfo,
    RequestAirportData,
    RequestArrival,
    SendSimEvent,
    SetInputEvent,
    TurnKnob,
    ClickSequence,
    WatchVars,
    AircraftVars,
    AiTrack,
    AiLights,
    SetAiVar,
    NudgeVar,
    SpawnAiAircraft,
    RemoveAiAircraft,
    EnumerateModels,
    AiObjectAssigned,
    ModelList,
    TrafficIdentity,
    SetComFrequency,
    SetSimVar,
    SimCommand,
    SimLifecycle,
    TrafficSnapshot,
    TrafficTarget,
)
from localtc.sim_bridge import definitions as defs
from localtc.sim_bridge import arrivals, facilities
from localtc.sim_bridge.knob import KnobTurn
from localtc.sim_bridge.motion import Track
from localtc.sim_bridge.dll import SimConnectError
from localtc.sim_bridge.wire import make_client
from localtc.sim_bridge.protocol import (
    AssignedObject,
    InputEventList,
    ModelLivery,
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
REQ_INPUT_EVENTS = 8
REQ_TRAFFIC_IDENT, REQ_TRAFFIC_LIVERY, REQ_MODELS, REQ_AI_REMOVE = 9, 10, 11, 12
DEF_TRAFFIC_IDENT, DEF_TRAFFIC_LIVERY = 12, 13
DEF_AI_MOVE, DEF_AI_GROUND, REQ_AI_GROUND = 14, 15, 15  # LocalTC's traffic: moved, and the ground under it
AI_GROUND_EVERY_S = 2.0
AI_LIGHT_EVENTS = ("LANDING_LIGHTS_SET", "TAXI_LIGHTS_SET", "BEACON_LIGHTS_SET", "STROBES_SET", "NAV_LIGHTS_SET",
                   "LOGO_LIGHTS_SET")
AI_FREEZE = ("FREEZE_LATITUDE_LONGITUDE_SET", "FREEZE_ALTITUDE_SET", "FREEZE_ATTITUDE_SET")
REQ_WATCH, FIRST_WATCH_DEFINITION = 13, 900  # the profile's own variables (WatchVars): a new definition each time
TRAFFIC_IDENT_EVERY_S = 8.0  # EXPERIMENTAL traffic control: who the traffic is, this often
FIRST_SIMVAR_DEFINITION = 50  # one data definition per variable the copilot writes (L:vars), from here up
FIRST_FACILITY_REQUEST = 100
NUDGE_GAP_S = 0.1
KNOB_READ_REQUEST = 1000  # + the variable's data definition: a knob being turned reads it back (TurnKnob)
FACILITY_TIMEOUT_S = 60.0
FACILITY_MESSAGES = {RecvId.AIRPORT_LIST, *FACILITY_IDS}
NEARBY_AIRPORTS, NEARBY_NM = 40, 150.0  # the airports around the aircraft reported to ATC, for diversions
NEARBY_ICAO = 8  # beyond those, the nearest with a four-letter ICAO code

EVT_SIM_START, EVT_SIM_STOP, EVT_PAUSE, EVT_FLIGHT_LOADED, EVT_AIRCRAFT_LOADED, EVT_CRASHED = range(1, 7)
# Client events LocalTC sends to the sim (same ID space as the system events above).
EVT_COM1_SET_HZ, EVT_COM2_SET_HZ = 20, 21
CLIENT_EVENTS = {EVT_COM1_SET_HZ: "COM_RADIO_SET_HZ", EVT_COM2_SET_HZ: "COM2_RADIO_SET_HZ"}
FIRST_COPILOT_EVENT = 100  # key events the copilot sends ("GEAR_DOWN"), mapped as first used, from here up
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
        self._dll_factory = dll_factory or (lambda: make_client(self._cfg.connection, self._cfg.dll_path or None))
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
        self._input_events: dict[str, int] = {}
        self._identity_title: str | None = None
        self._want_input_events = False
        self._copilot_events: dict[str, int] = {}
        self._simvar_definitions: dict[str, int] = {}
        self._nudges: dict[int, list[float]] = {}  # an encoder's definition -> the amounts still to add (NudgeVar)
        self._nudge_at: dict[int, tuple[float, float]] = {}  # ... -> its value as read and added to, the next write
        # The copilot's hands do one thing at a time: knobs turned and clickspots clicked in turn (TurnKnob,
        # ClickSequence), the one in hand with its variable's definition (a knob) or its clicks still to send.
        self._hands: deque[SimCommand] = deque()
        self._hand: tuple | None = None
        self._watch: tuple[str, ...] = ()
        self._watch_define = 0
        self._watch_count = 0
        self._ai_tracks: dict[int, Track] = {}  # LocalTC's traffic, as it's being moved (AiTrack)
        self._ai_ground_next = 0.0
        for name, unit in (("PLANE LATITUDE", "degrees"), ("PLANE LONGITUDE", "degrees"), ("PLANE ALTITUDE", "feet"),
                           ("PLANE PITCH DEGREES", "degrees"), ("PLANE BANK DEGREES", "degrees"),
                           ("PLANE HEADING DEGREES TRUE", "degrees")):
            dll.add_to_data_definition(handle, DEF_AI_MOVE, name, unit, defs.F64)
        for name in ("GROUND ALTITUDE", "STATIC CG TO GROUND"):
            dll.add_to_data_definition(handle, DEF_AI_GROUND, name, "feet", defs.F64)
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
        self._traffic_liveries: dict[int, str] = {}
        self._models: list[tuple[str, str]] = []
        if self._cfg.traffic_identity:  # EXPERIMENTAL traffic control only
            for d in defs.TRAFFIC_IDENTITY:
                dll.add_to_data_definition(handle, DEF_TRAFFIC_IDENT, d.simvar, d.units, d.datatype)
            try:
                for d in defs.TRAFFIC_LIVERY:
                    dll.add_to_data_definition(handle, DEF_TRAFFIC_LIVERY, d.simvar, d.units, d.datatype)
            except SimConnectError as exc:
                log.info("No livery names from this sim: %s", exc)
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
        next_own = next_traffic = next_ident = 0.0
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
                if self._cfg.traffic_identity and now >= next_ident:
                    next_ident = now + TRAFFIC_IDENT_EVERY_S
                    for req, definition in ((REQ_TRAFFIC_IDENT, DEF_TRAFFIC_IDENT), (REQ_TRAFFIC_LIVERY, DEF_TRAFFIC_LIVERY)):
                        try:
                            dll.request_data_on_sim_object_type(handle, req, definition, self._cfg.traffic_radius_m,
                                                                SimObjectType.AIRCRAFT)
                        except SimConnectError as exc:
                            log.info("Traffic identity not available: %s", exc)
                if nearest_period and self._position is not None and now >= next_nearest:
                    self._airport_list, self._airport_list_received = [], 0
                    dll.request_facilities_list(handle, FacilityListType.AIRPORT, REQ_AIRPORT_LIST)
                    next_nearest = now + nearest_period
                if self._want_input_events and hasattr(dll, "enumerate_input_events"):
                    self._want_input_events = False
                    try:
                        dll.enumerate_input_events(handle, REQ_INPUT_EVENTS)
                    except SimConnectError as exc:
                        log.info("No input events from this sim: %s", exc)
                self._run_commands(dll, handle)
                self._work_hands(dll, handle)
                self._move_ai(dll, handle)
                self._nudge(dll, handle)
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
            if (event := _lifecycle_event(msg, t)) is not None:
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
        elif isinstance(msg, InputEventList) and msg.request_id == REQ_INPUT_EVENTS:
            self._on_input_events(msg)
        elif isinstance(msg, AssignedObject):
            self._emit(AiObjectAssigned(t=t, request_id=msg.request_id, object_id=msg.object_id))
        elif isinstance(msg, ModelLivery) and msg.request_id == REQ_MODELS:
            self._models += list(msg.models)
            if msg.entry + 1 >= msg.out_of:
                self._emit(ModelList(t=t, models=tuple(self._models)))
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
            elif isinstance(command, SetInputEvent):
                self._set_input_event(dll, handle, command)
            elif isinstance(command, NudgeVar):
                self._start_nudge(dll, handle, command)
            elif isinstance(command, (TurnKnob, ClickSequence)):
                self._hands.append(command)
            elif isinstance(command, WatchVars):
                self._watch_vars(dll, handle, command.names)
            elif isinstance(command, SetAiVar):
                self._set_ai_var(dll, handle, command)
            elif isinstance(command, SpawnAiAircraft):
                try:
                    dll.ai_create(handle, command.kind, command.request_id, command.title, command.livery, command.tail,
                                  flight_number=command.flight_number, lat=command.lat, lon=command.lon,
                                  alt_ft=command.alt_ft, heading=command.heading, on_ground=command.on_ground,
                                  airspeed_kt=command.airspeed_kt, plan=command.plan, plan_position=command.plan_position)
                except (SimConnectError, AttributeError) as exc:
                    log.warning("Traffic control couldn't create %s: %s", command.tail or command.title, exc)
            elif isinstance(command, AiTrack):
                self._track_ai(dll, handle, command)
            elif isinstance(command, AiLights):
                self._light_ai(dll, handle, command)
            elif isinstance(command, RemoveAiAircraft):
                self._ai_tracks.pop(command.object_id, None)
                try:
                    dll.ai_remove(handle, command.object_id, REQ_AI_REMOVE)
                except (SimConnectError, AttributeError) as exc:
                    log.info("Traffic control couldn't remove object %d: %s", command.object_id, exc)
            elif isinstance(command, EnumerateModels):
                try:
                    self._models = []
                    dll.enumerate_models(handle, REQ_MODELS)
                except (SimConnectError, AttributeError) as exc:
                    log.info("No list of installed aircraft: %s", exc)
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
            if command.index and hasattr(dll, "transmit_client_event_ex1"):
                dll.transmit_client_event_ex1(handle, OBJECT_ID_USER, event_id, GROUP_PRIORITY_HIGHEST,
                                              EVENT_FLAG_GROUPID_IS_PRIORITY, command.value, command.index)
            else:
                dll.transmit_client_event(handle, OBJECT_ID_USER, event_id, command.value, GROUP_PRIORITY_HIGHEST,
                                          EVENT_FLAG_GROUPID_IS_PRIORITY)
            log.info("Copilot: %s %s%s", command.name, command.value, f" (#{command.index})" if command.index else "")
        except SimConnectError as exc:
            log.warning("Copilot couldn't send %s: %s", command.name, exc)

    def _set_input_event(self, dll: SimConnectApi, handle: int, command: SetInputEvent) -> None:
        """A cockpit control through the aircraft's own input event (MSFS 2024), by its name in the aircraft's list."""
        hash_ = self._input_events.get(command.name.upper())
        if hash_ is None:
            log.warning("Copilot: this aircraft has no input event %s (its list is in the log)", command.name)
            return
        try:
            dll.set_input_event(handle, hash_, command.value)
            log.info("Copilot: input event %s = %s", command.name, command.value)
        except (SimConnectError, AttributeError) as exc:
            log.warning("Copilot couldn't set %s: %s", command.name, exc)

    def _start_knob(self, dll: SimConnectApi, handle: int, command: TurnKnob) -> bool:
        if not command.event and command.name.upper() not in self._input_events:
            log.warning("Copilot: this aircraft has no input event %s (its list is in the log)", command.name)
            return False
        try:
            define_id = self._var_definition(dll, handle, command.var, command.unit)
        except SimConnectError as exc:
            log.warning("Copilot can't read %s to turn %s: %s", command.var, command.name, exc)
            return False
        turn = KnobTurn(command.name.upper(), command.target, command.step, command.wrap, learn=command.learn,
                        div=command.div, mod=command.mod, burst=command.burst)
        self._hand = (turn, define_id, command)
        log.info("Copilot: turning %s to %s", command.name, command.target)
        return True

    def _event_id(self, dll: SimConnectApi, handle: int, name: str) -> int:
        event_id = self._copilot_events.get(name)
        if event_id is None:
            event_id = FIRST_COPILOT_EVENT + len(self._copilot_events)
            dll.map_client_event_to_sim_event(handle, event_id, name)
            self._copilot_events[name] = event_id
        return event_id

    def _click(self, dll: SimConnectApi, handle: int, event: str, code: int) -> None:
        dll.transmit_client_event(handle, OBJECT_ID_USER, self._event_id(dll, handle, event), code,
                                  GROUP_PRIORITY_HIGHEST, EVENT_FLAG_GROUPID_IS_PRIORITY)

    def _work_hands(self, dll: SimConnectApi, handle: int) -> None:
        """The control in hand a step further (a knob read or turned, the next click), else the next one taken up."""
        now = time.monotonic()
        while self._hand is None and self._hands:
            command = self._hands.popleft()
            if isinstance(command, ClickSequence):
                self._hand = (list(command.codes), now, command)
                log.info("Copilot: %s (%s)", command.name, " ".join(map(str, command.codes)))
            else:
                self._start_knob(dll, handle, command)
        if self._hand is None:
            return
        try:
            if isinstance(self._hand[2], ClickSequence):
                codes, next_t, seq = self._hand
                if now >= next_t:
                    if codes:
                        self._click(dll, handle, seq.event, codes.pop(0))
                        self._hand = (codes, now + seq.gap_s, seq)
                    else:
                        self._hand = None
                return
            turn, define_id, command = self._hand
            if turn.done:
                self._hand = None
                return
            what = turn.due(now)
            if what == "step":
                step = turn.pending.pop()
                if command.event:
                    self._click(dll, handle, command.event, command.up if step > 0 else command.down)
                else:
                    dll.set_input_event(handle, self._input_events[turn.name], step)
            elif what == "read":
                dll.request_data_on_sim_object(handle, KNOB_READ_REQUEST + define_id, define_id, OBJECT_ID_USER,
                                               Period.ONCE)
        except (SimConnectError, KeyError, AttributeError) as exc:
            log.warning("Copilot couldn't work %s: %s", getattr(self._hand[2], "name", "a control"), exc)
            self._hand = None

    def _track_ai(self, dll: SimConnectApi, handle: int, c: AiTrack) -> None:
        """One of LocalTC's aircraft's new state: frozen against the sim's own physics the first time, then carried
        on from here (motion.py)."""
        now = time.monotonic()
        new = Track(c.object_id, c.lat, c.lon, c.alt_ft, c.hdg, c.gs_kt, c.vs_fpm, c.turn_dps, c.on_ground, c.pitch,
                    c.bank, now, c.blend_s)
        track = self._ai_tracks.get(c.object_id)
        if track is None:
            self._ai_tracks[c.object_id] = track = new
            track.blend_s = 0.0
            try:
                for name in AI_FREEZE:
                    dll.transmit_client_event(handle, c.object_id, self._event_id(dll, handle, name), 1,
                                              GROUP_PRIORITY_HIGHEST, EVENT_FLAG_GROUPID_IS_PRIORITY)
                track.frozen = True
                if c.on_ground:
                    dll.request_data_on_sim_object(handle, REQ_AI_GROUND, DEF_AI_GROUND, c.object_id, Period.ONCE)
            except SimConnectError as exc:
                log.info("Traffic: couldn't take hold of object %d: %s", c.object_id, exc)
        else:
            track.update(new, now)

    def _light_ai(self, dll: SimConnectApi, handle: int, c: AiLights) -> None:
        try:
            event = "GEAR_DOWN" if c.gear_down else "GEAR_UP"
            dll.transmit_client_event(handle, c.object_id, self._event_id(dll, handle, event), 0, GROUP_PRIORITY_HIGHEST,
                                      EVENT_FLAG_GROUPID_IS_PRIORITY)
            for name, on in zip(AI_LIGHT_EVENTS, (c.landing, c.taxi, c.beacon, c.strobe, c.nav, c.logo)):
                dll.transmit_client_event(handle, c.object_id, self._event_id(dll, handle, name), int(on),
                                          GROUP_PRIORITY_HIGHEST, EVENT_FLAG_GROUPID_IS_PRIORITY)
        except SimConnectError as exc:
            log.info("Traffic: couldn't set the lights of object %d: %s", c.object_id, exc)

    def _move_ai(self, dll: SimConnectApi, handle: int) -> None:
        """LocalTC's traffic written where it is now: each as often as its distance from the user calls for."""
        if not self._ai_tracks:
            return
        now = time.monotonic()
        ask_ground = now >= self._ai_ground_next
        if ask_ground:
            self._ai_ground_next = now + AI_GROUND_EVERY_S
        for oid, track in list(self._ai_tracks.items()):
            if now < track.next_write:
                continue
            lat, lon, alt, hdg, pitch, bank = track.at(now)
            track.next_write = now + track.interval(self._position, lat, lon)
            try:
                dll.set_data_on_sim_object(handle, DEF_AI_MOVE, oid, struct.pack("<6d", lat, lon, alt, pitch, bank, hdg))
                if ask_ground and track.on_ground and (track.gs_kt > 1.0 or track.ground_ft is None):
                    dll.request_data_on_sim_object(handle, REQ_AI_GROUND, DEF_AI_GROUND, oid, Period.ONCE)
            except SimConnectError as exc:
                log.info("Traffic: object %d can't be moved (%s): let go", oid, exc)
                self._ai_tracks.pop(oid, None)

    def _watch_vars(self, dll: SimConnectApi, handle: int, names: tuple[str, ...]) -> None:
        """The profile's variables read every second from now (a new definition: they can't be taken out of one)."""
        if names == self._watch:
            return
        try:
            if self._watch_define:
                dll.request_data_on_sim_object(handle, REQ_WATCH, self._watch_define, OBJECT_ID_USER, Period.NEVER)
            self._watch, self._watch_define = names, 0
            if not names:
                return
            define_id = FIRST_WATCH_DEFINITION + self._watch_count
            self._watch_count += 1
            for name in names:
                dll.add_to_data_definition(handle, define_id, name, "number", defs.F64)
            dll.request_data_on_sim_object(handle, REQ_WATCH, define_id, OBJECT_ID_USER, Period.SECOND,
                                           RequestFlag.CHANGED)
            self._watch_define = define_id
            log.info("Copilot: reading %d of the aircraft's own variables", len(names))
        except SimConnectError as exc:
            log.warning("Copilot can't read the aircraft's own variables: %s", exc)

    def _var_definition(self, dll: SimConnectApi, handle: int, var: str, unit: str) -> int:
        key = f"{var}|{unit}" if unit != "number" else var
        define_id = self._simvar_definitions.get(key)
        if define_id is None:
            define_id = FIRST_SIMVAR_DEFINITION + len(self._simvar_definitions)
            dll.add_to_data_definition(handle, define_id, var, unit, defs.F64)
            self._simvar_definitions[key] = define_id
        return define_id

    def _set_ai_var(self, dll: SimConnectApi, handle: int, command: SetAiVar) -> None:
        """A variable of an AI aircraft LocalTC created (traffic control): a number, or a string (``text``)."""
        kind = defs.F64 if not command.text and command.unit else DataType.STRING64 if len(command.text) >= 8             else DataType.STRING8
        key = f"ai|{command.name}|{command.unit}|{int(kind)}"
        try:
            define_id = self._simvar_definitions.get(key)
            if define_id is None:
                define_id = FIRST_SIMVAR_DEFINITION + len(self._simvar_definitions)
                dll.add_to_data_definition(handle, define_id, command.name, command.unit or None, kind)
                self._simvar_definitions[key] = define_id
            if kind == defs.F64:
                data = struct.pack("<d", command.value)
            else:
                size = 64 if kind == DataType.STRING64 else 8
                data = command.text.encode("ascii", "replace")[:size - 1].ljust(size, bytes(1))
            dll.set_data_on_sim_object(handle, define_id, command.object_id, data)
        except SimConnectError as exc:
            log.info("Traffic control couldn't set %s on object %d: %s", command.name, command.object_id, exc)

    def _start_nudge(self, dll: SimConnectApi, handle: int, command: NudgeVar) -> None:
        """Read the counter; the amounts are added as it comes back (``_on_nudge``)."""
        try:
            define_id = self._var_definition(dll, handle, command.name, "number")
            self._nudges[define_id] = list(command.deltas)
            self._nudge_at.pop(define_id, None)
            log.info("Copilot: %s by %s", command.name, " then ".join(f"{d:+g}" for d in command.deltas))
            dll.request_data_on_sim_object(handle, KNOB_READ_REQUEST + define_id, define_id, OBJECT_ID_USER, Period.ONCE)
        except SimConnectError as exc:
            log.warning("Copilot couldn't turn %s: %s", command.name, exc)

    def _on_nudge(self, define_id: int, value: float) -> None:
        self._nudge_at[define_id] = (value, 0.0)

    def _nudge(self, dll: SimConnectApi, handle: int) -> None:
        """The counters read: add the next amount, each its own write a little apart (the add-on sees the counter
        move twice: past the stop, then up)."""
        now = time.monotonic()
        for define_id, (value, next_t) in list(self._nudge_at.items()):
            deltas = self._nudges.get(define_id)
            if not deltas:
                del self._nudge_at[define_id]
                self._nudges.pop(define_id, None)
                continue
            if now < next_t:
                continue
            value += deltas.pop(0)
            self._nudge_at[define_id] = (value, now + NUDGE_GAP_S)
            try:
                dll.set_data_on_sim_object(handle, define_id, OBJECT_ID_USER, struct.pack("<d", value))
            except SimConnectError as exc:
                log.warning("Copilot couldn't turn an encoder: %s", exc)
                self._nudges.pop(define_id, None)

    def _on_input_events(self, msg: InputEventList) -> None:
        for name, hash_, _kind in msg.events:
            self._input_events[name.upper()] = hash_
        if msg.entry + 1 >= msg.out_of:
            names = sorted(self._input_events)
            # The aircraft's controls by name, for its copilot profile ([actions.x] input = "NAME"); in a bug report.
            log.info("Input events of this aircraft (%d): %s", len(names), ", ".join(names))
            self._emit(AircraftInputEvents(t=self._clock.now(), names=tuple(names)))

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
        # Among the nearest, a city's heliports and strips ("RJ26P") can be all of them (Haneda's 40 nearest): the
        # nearest few with an ICAO code too, for a diversion and for a flight plan traffic control files from one.
        near += [a for a in by_distance[NEARBY_AIRPORTS:] if re.fullmatch(r"[A-Z]{4}", a.icao)
                 and facilities.haversine_nm(lat, lon, a.lat, a.lon) <= NEARBY_NM][:NEARBY_ICAO]
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
        if msg.request_id >= KNOB_READ_REQUEST:
            if msg.request_id - KNOB_READ_REQUEST in self._nudges and len(msg.payload) >= 8                     and msg.request_id - KNOB_READ_REQUEST not in self._nudge_at:
                self._on_nudge(msg.request_id - KNOB_READ_REQUEST, struct.unpack_from("<d", msg.payload)[0])
            hand = self._hand
            if hand is not None and isinstance(hand[0], KnobTurn) and len(msg.payload) >= 8 \
                    and KNOB_READ_REQUEST + hand[1] == msg.request_id:
                hand[0].on_value(struct.unpack_from("<d", msg.payload)[0], time.monotonic())
                if hand[0].done:
                    log.info("Copilot: %s turned to %g", hand[0].name, struct.unpack_from("<d", msg.payload)[0])
        elif msg.request_id == REQ_AI_GROUND and msg.define_id == DEF_AI_GROUND and len(msg.payload) >= 16:
            if (track := self._ai_tracks.get(msg.object_id)) is not None:
                ground, cg = struct.unpack_from("<2d", msg.payload)
                track.ground_ft = ground + cg
        elif msg.request_id == REQ_WATCH and self._watch:
            n = min(len(self._watch), len(msg.payload) // 8)
            values = struct.unpack_from(f"<{n}d", msg.payload)
            self._emit(AircraftVars(t=t, values=dict(zip(self._watch, values))))
        elif msg.request_id == REQ_OWNSHIP:
            self._user_object_id = msg.object_id
            ownship = defs.ownship_from_raw(defs.unpack(defs.OWNSHIP, msg.payload), t)
            self._position = (ownship.lat, ownship.lon)
            self._emit(ownship)
        elif msg.request_id == REQ_IDENTITY:
            identity = defs.identity_from_raw(defs.unpack(defs.IDENTITY, msg.payload), t)
            if identity.title != self._identity_title:  # a new aircraft: its own controls
                self._identity_title = identity.title
                self._input_events = {}
                self._want_input_events = True
            self._emit(identity)
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
        elif msg.request_id == REQ_TRAFFIC_LIVERY and msg.out_of > 0:
            if len(msg.payload) >= defs.payload_size(defs.TRAFFIC_LIVERY):
                self._traffic_liveries[msg.object_id] = defs.unpack(defs.TRAFFIC_LIVERY, msg.payload)["livery"]
        elif msg.request_id == REQ_TRAFFIC_IDENT and msg.out_of > 0:
            if msg.object_id != self._user_object_id and len(msg.payload) >= defs.payload_size(defs.TRAFFIC_IDENTITY):
                raw = defs.unpack(defs.TRAFFIC_IDENTITY, msg.payload)
                self._emit(TrafficIdentity(t=t, object_id=msg.object_id, title=raw["title"], origin=raw["origin"],
                                           destination=raw["destination"], state=raw["state"],
                                           livery=self._traffic_liveries.get(msg.object_id, "")))
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
