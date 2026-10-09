"""LocalTC's own SimConnect client: the sim's wire protocol spoken directly, so nothing has to be installed (the SDK's
``SimConnect.dll`` is the other way, ``live.connection = "dll"``).

MSFS listens for SimConnect clients on a named pipe (``\\\\.\\pipe\\Microsoft Flight Simulator\\SimConnect``, the one the
DLL uses by default) and on the ports in its ``SimConnect.xml`` (127.0.0.1:500 as shipped). Every call is one packet:
a 16-byte header (size, protocol version 6, 0xF0000000 + the call's number, a running packet number) and the call's
arguments as the DLL lays them out (strings in fixed-size, NUL-padded fields). What comes back is exactly what the
DLL's ``SimConnect_GetNextDispatch`` hands over (``protocol.parse_message`` reads both). The layouts were taken from
the SDK's DLL talking to MSFS 2024 12.2 (docs/windows-findings.md, 2026-10-09).

The same methods as ``dll.SimConnectDll``, so ``SimConnectSource`` takes either.
"""

import ctypes
import itertools
import logging
import os
import re
import socket
import struct
import sys
import threading
from collections import deque
from pathlib import Path

from localtc.sim_bridge.dll import SimConnectError, SimConnectUnavailable
from localtc.sim_bridge.protocol import UNUSED, DataType, FacilityListType, Period, SimObjectType

log = logging.getLogger(__name__)

PROTOCOL = 6
CLIENT_VERSION = (12, 2, 0, 0)  # the SDK's SimConnect this speaks as (MSFS 2024)
PIPE = "\\\\.\\pipe\\Microsoft Flight Simulator\\SimConnect"
DEFAULT_PORT = 500
MAX_PACKET = 1 << 24  # larger than anything the sim sends: a broken stream, not a packet

# The calls, by their number in the packet header (0xF0000000 + this).
OPEN, MAP_CLIENT_EVENT, TRANSMIT_CLIENT_EVENT, SET_SYSTEM_EVENT_STATE, ADD_CLIENT_EVENT_TO_GROUP = 0x01, 0x04, 0x05, 0x06, 0x07
SET_GROUP_PRIORITY, ADD_TO_DATA_DEFINITION, CLEAR_DATA_DEFINITION = 0x09, 0x0C, 0x0D
REQUEST_DATA_ON_SIM_OBJECT, REQUEST_DATA_ON_SIM_OBJECT_TYPE, SET_DATA_ON_SIM_OBJECT = 0x0E, 0x0F, 0x10
SUBSCRIBE_TO_SYSTEM_EVENT, UNSUBSCRIBE_FROM_SYSTEM_EVENT = 0x17, 0x18
AI_CREATE_NON_ATC, AI_RELEASE_CONTROL, AI_REMOVE_OBJECT, AI_SET_FLIGHT_PLAN = 0x29, 0x2B, 0x2C, 0x2D
REQUEST_SYSTEM_STATE = 0x35
TRANSMIT_CLIENT_EVENT_EX1, ADD_TO_FACILITY_DEFINITION, REQUEST_FACILITY_DATA = 0x44, 0x45, 0x46
REQUEST_FACILITIES_LIST_EX1, ENUMERATE_INPUT_EVENTS, SET_INPUT_EVENT = 0x49, 0x4F, 0x51
AI_CREATE_PARKED_ATC_EX1, AI_CREATE_ENROUTE_ATC_EX1, AI_CREATE_NON_ATC_EX1, ENUMERATE_MODELS = 0x57, 0x58, 0x59, 0x5B


def _s(text: str, size: int) -> bytes:
    """A fixed-size string field: as many bytes as fit, NUL-padded (always NUL-terminated)."""
    return text.encode("utf-8", "replace")[: size - 1].ljust(size, b"\0")


def server_addresses() -> list[tuple[str, object]]:
    """Where the sim listens, in the order to try: the default pipe, then each local pipe and TCP port in its
    SimConnect.xml (MSFS 2024 Store and Steam; MSFS 2020's too), then port 500 as shipped."""
    found: list[tuple[str, object]] = [("pipe", PIPE)]
    local, roaming = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
    files = [Path(local) / "Packages" / pkg / "LocalCache" / "SimConnect.xml"
             for pkg in ("Microsoft.Limitless_8wekyb3d8bbwe", "Microsoft.FlightSimulator_8wekyb3d8bbwe")]
    files += [Path(roaming) / name / "SimConnect.xml" for name in ("Microsoft Flight Simulator 2024", "Microsoft Flight Simulator")]
    for path in files:
        try:
            text = path.read_text(encoding="cp1252", errors="replace")
        except OSError:
            continue
        for block in re.findall(r"<SimConnect\.Comm>(.*?)</SimConnect\.Comm>", text, re.S | re.I):
            def get(tag: str) -> str:
                m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", block, re.S | re.I)
                return m.group(1) if m else ""
            protocol, port = get("Protocol").lower(), get("Port")
            if get("Scope").lower() not in ("", "local", "global"):
                continue
            if protocol == "ipv4" and port.isdigit() and int(port):
                found.append(("tcp", ("127.0.0.1", int(port))))
            elif protocol == "pipe" and port and port != "0":
                found.append(("pipe", "\\\\.\\pipe\\" + port.replace("/", "\\")))
    found.append(("tcp", ("127.0.0.1", DEFAULT_PORT)))
    return list(dict.fromkeys(found))


class _Tcp:
    def __init__(self, address: tuple[str, int]) -> None:
        self.sock = socket.create_connection(address, timeout=2.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.setblocking(False)
        self.name = f"{address[0]}:{address[1]}"

    def write(self, data: bytes) -> None:
        self.sock.setblocking(True)
        try:
            self.sock.sendall(data)
        finally:
            self.sock.setblocking(False)

    def read(self) -> bytes | None:
        """What's there now (b"" for nothing yet), or None once the sim has closed it."""
        try:
            data = self.sock.recv(1 << 16)
        except (BlockingIOError, InterruptedError):
            return b""
        return data or None

    def close(self) -> None:
        self.sock.close()


class _Pipe:
    """A named pipe, read without ever blocking (PeekNamedPipe first), so a write never waits behind a read."""

    def __init__(self, path: str) -> None:
        if sys.platform != "win32":
            raise OSError("named pipes are Windows only")
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.HANDLE]
        k.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k, self.wintypes, self.name = k, wintypes, path
        handle = k.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0, None)  # read/write, OPEN_EXISTING
        if handle in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), f"can't open {path}")
        self.handle = handle
        mode = wintypes.DWORD(0)  # PIPE_READMODE_BYTE: a message-type pipe read as a stream of bytes
        k.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None)

    def write(self, data: bytes) -> None:
        done = self.wintypes.DWORD(0)
        if not self.k.WriteFile(self.handle, data, len(data), ctypes.byref(done), None) or done.value != len(data):
            raise OSError(ctypes.get_last_error(), "write to the sim's pipe failed")

    def read(self) -> bytes | None:
        avail = self.wintypes.DWORD(0)
        if not self.k.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(avail), None):
            return None  # broken: the sim has gone
        if not avail.value:
            return b""
        buf = ctypes.create_string_buffer(avail.value)
        got = self.wintypes.DWORD(0)
        ok = self.k.ReadFile(self.handle, buf, avail.value, ctypes.byref(got), None)
        if not ok and ctypes.get_last_error() != 234:  # ERROR_MORE_DATA: the rest comes on the next read
            return None
        return buf.raw[: got.value]

    def close(self) -> None:
        self.k.CloseHandle(self.handle)


class _Connection:
    def __init__(self, transport: "_Tcp | _Pipe") -> None:
        self.transport = transport
        self.inbox: deque[bytes] = deque()
        self.buffer = b""
        self.packet_ids = itertools.count(1)
        self.lock = threading.Lock()
        self.closed = False

    def send(self, call: int, body: bytes) -> None:
        if self.closed:
            raise SimConnectError("the sim closed the connection")
        with self.lock:
            header = struct.pack("<4I", 16 + len(body), PROTOCOL, 0xF0000000 | call, next(self.packet_ids))
            try:
                self.transport.write(header + body)
            except OSError as exc:
                self.closed = True
                raise SimConnectError(f"the sim closed the connection ({exc})") from exc

    def pump(self) -> None:
        """Move what has arrived into whole packets."""
        while True:
            try:
                data = self.transport.read()
            except OSError:
                data = None
            if data is None:
                self.closed = True
                return
            if not data:
                return
            self.buffer += data
            while len(self.buffer) >= 4:
                size = struct.unpack_from("<I", self.buffer)[0]
                if size < 12 or size > MAX_PACKET:
                    self.closed = True
                    raise SimConnectError(f"garbled data from the sim (a packet of {size} bytes)")
                if len(self.buffer) < size:
                    break
                self.inbox.append(self.buffer[:size])
                self.buffer = self.buffer[size:]


class SimConnectWire:
    """The sim's SimConnect, spoken directly (no DLL): the same methods as ``dll.SimConnectDll``."""

    path = Path("built-in")

    def __init__(self, addresses: list[tuple[str, object]] | None = None) -> None:
        self._addresses = addresses
        self._connections: dict[int, _Connection] = {}
        self._handles = itertools.count(1)
        self.transport_name = ""

    def _connect(self) -> "_Tcp | _Pipe":
        tried = []
        for kind, where in self._addresses or server_addresses():
            try:
                return _Pipe(where) if kind == "pipe" else _Tcp(where)  # type: ignore[arg-type]
            except OSError as exc:
                tried.append(f"{where}: {exc.strerror or exc}")
        raise SimConnectError("the simulator isn't running (" + "; ".join(tried) + ")")

    def _c(self, handle: int) -> _Connection:
        conn = self._connections.get(handle)
        if conn is None:
            raise SimConnectError("not connected")
        return conn

    # --- the calls ---------------------------------------------------------------------------------------------

    def open(self, app_name: str) -> int:
        transport = self._connect()
        conn = _Connection(transport)
        handle = next(self._handles)
        self._connections[handle] = conn
        self.transport_name = transport.name
        body = _s(app_name, 256) + struct.pack("<I", 0) + b"\0XSF" + struct.pack("<4I", *CLIENT_VERSION)
        conn.send(OPEN, body)
        log.info("SimConnect: LocalTC's own client, over %s", transport.name)
        return handle

    def close(self, handle: int) -> None:
        conn = self._connections.pop(handle, None)
        if conn is not None:
            conn.closed = True
            try:
                conn.transport.close()
            except OSError:
                pass

    def add_to_data_definition(self, handle: int, define_id: int, simvar: str, units: str | None,
                               datatype: DataType) -> None:
        self._c(handle).send(ADD_TO_DATA_DEFINITION, struct.pack("<I", define_id) + _s(simvar, 256)
                             + _s(units or "", 256) + struct.pack("<IfI", int(datatype), 0.0, UNUSED))

    def clear_data_definition(self, handle: int, define_id: int) -> None:
        self._c(handle).send(CLEAR_DATA_DEFINITION, struct.pack("<I", define_id))

    def request_data_on_sim_object(self, handle: int, request_id: int, define_id: int, object_id: int, period: Period,
                                   flags: int = 0, origin: int = 0, interval: int = 0, limit: int = 0) -> None:
        self._c(handle).send(REQUEST_DATA_ON_SIM_OBJECT, struct.pack(
            "<8I", request_id, define_id, object_id, int(period), int(flags), origin, interval, limit))

    def request_data_on_sim_object_type(self, handle: int, request_id: int, define_id: int, radius_m: int,
                                        object_type: SimObjectType) -> None:
        self._c(handle).send(REQUEST_DATA_ON_SIM_OBJECT_TYPE,
                             struct.pack("<4I", request_id, define_id, radius_m, int(object_type)))

    def subscribe_to_system_event(self, handle: int, event_id: int, name: str) -> None:
        self._c(handle).send(SUBSCRIBE_TO_SYSTEM_EVENT, struct.pack("<I", event_id) + _s(name, 256))

    def add_to_facility_definition(self, handle: int, define_id: int, field: str) -> None:
        self._c(handle).send(ADD_TO_FACILITY_DEFINITION, struct.pack("<I", define_id) + _s(field, 256))

    def request_facility_data(self, handle: int, define_id: int, request_id: int, icao: str, region: str = "") -> None:
        self._c(handle).send(REQUEST_FACILITY_DATA,
                             struct.pack("<II", define_id, request_id) + _s(icao, 16) + _s(region, 4))

    def request_facilities_list(self, handle: int, list_type: FacilityListType, request_id: int) -> None:
        self._c(handle).send(REQUEST_FACILITIES_LIST_EX1, struct.pack("<II", int(list_type), request_id))

    def map_client_event_to_sim_event(self, handle: int, event_id: int, name: str) -> None:
        self._c(handle).send(MAP_CLIENT_EVENT, struct.pack("<I", event_id) + _s(name, 256))

    def transmit_client_event(self, handle: int, object_id: int, event_id: int, data: int, group: int, flags: int) -> None:
        self._c(handle).send(TRANSMIT_CLIENT_EVENT,
                             struct.pack("<5I", object_id, event_id, data & 0xFFFFFFFF, group, flags))

    def transmit_client_event_ex1(self, handle: int, object_id: int, event_id: int, group: int, flags: int,
                                  data0: int, data1: int = 0) -> None:
        self._c(handle).send(TRANSMIT_CLIENT_EVENT_EX1, struct.pack(
            "<9I", object_id, event_id, group, flags, data0 & 0xFFFFFFFF, data1 & 0xFFFFFFFF, 0, 0, 0))

    def enumerate_input_events(self, handle: int, request_id: int) -> None:
        self._c(handle).send(ENUMERATE_INPUT_EVENTS, struct.pack("<I", request_id))

    def set_input_event(self, handle: int, hash_: int, value: float) -> None:
        self._c(handle).send(SET_INPUT_EVENT, struct.pack("<QId", hash_, 8, value))

    def ai_create(self, handle: int, kind: str, request_id: int, title: str, livery: str, tail: str, *,
                  flight_number: int = -1, lat: float = 0, lon: float = 0, alt_ft: float = 0, heading: float = 0,
                  on_ground: bool = True, airspeed_kt: float = 0, plan: str = "", plan_position: float = 0,
                  pitch: float = 0, bank: float = 0) -> None:
        """An AI aircraft: ``parked`` (non-ATC: still where it's put, or moved by LocalTC), ``enroute`` (flying
        ``plan`` under the sim's AI), or ``parked_atc`` (at a gate of ``plan``, an airport ICAO, by the sim)."""
        head = _s(title, 256) + _s(livery, 256) + _s(tail, 12)
        if kind == "enroute":
            self._c(handle).send(AI_CREATE_ENROUTE_ATC_EX1, head + struct.pack("<i", flight_number) + _s(plan, 260)
                                 + struct.pack("<diI", plan_position, 0, request_id))
        elif kind == "parked_atc":
            self._c(handle).send(AI_CREATE_PARKED_ATC_EX1, head + _s(plan, 5) + struct.pack("<I", request_id))
        else:
            self._c(handle).send(AI_CREATE_NON_ATC_EX1, head + struct.pack(
                "<6dIII", lat, lon, alt_ft, pitch, bank, heading, int(on_ground), int(airspeed_kt), request_id))

    def ai_remove(self, handle: int, object_id: int, request_id: int) -> None:
        self._c(handle).send(AI_REMOVE_OBJECT, struct.pack("<II", object_id, request_id))

    def ai_release_control(self, handle: int, object_id: int, request_id: int) -> None:
        self._c(handle).send(AI_RELEASE_CONTROL, struct.pack("<II", object_id, request_id))

    def enumerate_models(self, handle: int, request_id: int) -> None:
        self._c(handle).send(ENUMERATE_MODELS, struct.pack("<II", request_id, 2))  # 2: aircraft (1, all, is refused)

    def set_data_on_sim_object(self, handle: int, define_id: int, object_id: int, data: bytes) -> None:
        self._c(handle).send(SET_DATA_ON_SIM_OBJECT, struct.pack("<5I", define_id, object_id, 0, 1, len(data)) + data)

    def get_next_dispatch(self, handle: int) -> bytes | None:
        conn = self._c(handle)
        if not conn.inbox:
            conn.pump()
        if conn.inbox:
            return conn.inbox.popleft()
        if conn.closed:
            raise SimConnectError("the sim closed the connection")
        return None


def make_client(connection: str, dll_path: str | None = None):
    """``builtin`` (LocalTC's own, nothing to install) or ``dll`` (the SDK's SimConnect.dll)."""
    if connection == "dll":
        from localtc.sim_bridge.dll import SimConnectDll, find_dll

        return SimConnectDll(find_dll(dll_path))
    if sys.platform != "win32":
        raise SimConnectUnavailable(
            "The live SimConnect bridge only runs on Windows. "
            'Use source.kind = "replay" (or LOCALTC_SOURCE=replay) on this machine.'
        )
    return SimConnectWire()
