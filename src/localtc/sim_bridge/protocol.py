"""SimConnect constants and message layouts, mirroring ``SimConnect.h`` (pack 1).

Only what LocalTC uses is defined. Parsing works on plain ``bytes`` so it's
tested on any OS; ``dll.py`` copies each dispatch buffer into bytes first.
"""

import ctypes
import enum
import struct
from dataclasses import dataclass

DWORD = ctypes.c_uint32

S_OK = 0
UNUSED = 0xFFFFFFFF
OBJECT_ID_USER = 0
GROUP_PRIORITY_HIGHEST = 1
EVENT_FLAG_GROUPID_IS_PRIORITY = 0x00000010  # TransmitClientEvent: GroupID is a priority, not a group


class DataType(enum.IntEnum):
    INVALID = 0
    INT32 = 1
    INT64 = 2
    FLOAT32 = 3
    FLOAT64 = 4
    STRING8 = 5
    STRING32 = 6
    STRING64 = 7
    STRING128 = 8
    STRING256 = 9
    STRING260 = 10


class Period(enum.IntEnum):
    NEVER = 0
    ONCE = 1
    VISUAL_FRAME = 2
    SIM_FRAME = 3
    SECOND = 4


class RequestFlag(enum.IntFlag):
    DEFAULT = 0
    CHANGED = 1
    TAGGED = 2


class SimObjectType(enum.IntEnum):
    USER = 0
    ALL = 1
    AIRCRAFT = 2
    HELICOPTER = 3
    BOAT = 4
    GROUND = 5


class RecvId(enum.IntEnum):
    NULL = 0
    EXCEPTION = 1
    OPEN = 2
    QUIT = 3
    EVENT = 4
    EVENT_OBJECT_ADDREMOVE = 5
    EVENT_FILENAME = 6
    EVENT_FRAME = 7
    SIMOBJECT_DATA = 8
    SIMOBJECT_DATA_BYTYPE = 9
    ASSIGNED_OBJECT_ID = 12
    AIRPORT_LIST = 18
    # The docs number these 29/30/31, but MSFS 2024 was observed sending FACILITY_DATA_END as 29
    # (so FACILITY_DATA = 28). parse_message() tells them apart by layout; see FACILITY_IDS.
    FACILITY_DATA = 28
    FACILITY_DATA_END = 29
    FACILITY_MINIMAL_LIST = 30


# Any of these IDs may carry a facility message, depending on which numbering the sim uses.
FACILITY_IDS = {28, 29, 30, 31}
FACILITY_DATA_END_SIZE = 16  # SIMCONNECT_RECV + RequestId


class FacilityListType(enum.IntEnum):
    AIRPORT = 0
    WAYPOINT = 1
    NDB = 2
    VOR = 3


EXCEPTION_NAMES = {
    0: "NONE",
    1: "ERROR",
    2: "SIZE_MISMATCH",
    3: "UNRECOGNIZED_ID",
    4: "UNOPENED",
    5: "VERSION_MISMATCH",
    6: "TOO_MANY_GROUPS",
    7: "NAME_UNRECOGNIZED",
    8: "TOO_MANY_EVENT_NAMES",
    9: "EVENT_ID_DUPLICATE",
    10: "TOO_MANY_MAPS",
    11: "TOO_MANY_OBJECTS",
    12: "TOO_MANY_REQUESTS",
    18: "INVALID_DATA_TYPE",
    19: "INVALID_DATA_SIZE",
    20: "DATA_ERROR",
}

PAUSE_FLAGS = {1: "full", 2: "active", 4: "sim"}


class Recv(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("dwSize", DWORD), ("dwVersion", DWORD), ("dwID", DWORD)]


class RecvException(Recv):
    _pack_ = 1
    _fields_ = [("dwException", DWORD), ("dwSendID", DWORD), ("dwIndex", DWORD)]


class RecvOpen(Recv):
    _pack_ = 1
    _fields_ = [
        ("szApplicationName", ctypes.c_char * 256),
        ("dwApplicationVersionMajor", DWORD),
        ("dwApplicationVersionMinor", DWORD),
        ("dwApplicationBuildMajor", DWORD),
        ("dwApplicationBuildMinor", DWORD),
        ("dwSimConnectVersionMajor", DWORD),
        ("dwSimConnectVersionMinor", DWORD),
        ("dwSimConnectBuildMajor", DWORD),
        ("dwSimConnectBuildMinor", DWORD),
        ("dwReserved1", DWORD),
        ("dwReserved2", DWORD),
    ]


class RecvEvent(Recv):
    _pack_ = 1
    _fields_ = [("uGroupID", DWORD), ("uEventID", DWORD), ("dwData", DWORD)]


class RecvEventFilename(RecvEvent):
    _pack_ = 1
    _fields_ = [("szFileName", ctypes.c_char * 260), ("dwFlags", DWORD)]


class RecvSimObjectData(Recv):
    """Shared by SIMOBJECT_DATA and SIMOBJECT_DATA_BYTYPE; data follows the struct."""

    _pack_ = 1
    _fields_ = [
        ("dwRequestID", DWORD),
        ("dwObjectID", DWORD),
        ("dwDefineID", DWORD),
        ("dwFlags", DWORD),
        ("dwentrynumber", DWORD),
        ("dwoutof", DWORD),
        ("dwDefineCount", DWORD),
    ]


SIMOBJECT_DATA_OFFSET = ctypes.sizeof(RecvSimObjectData)  # 40


class RecvFacilitiesList(Recv):
    """Header of AIRPORT_LIST (and VOR/NDB/WAYPOINT lists); array elements follow."""

    _pack_ = 1
    _fields_ = [("dwRequestID", DWORD), ("dwArraySize", DWORD), ("dwEntryNumber", DWORD), ("dwOutOf", DWORD)]


class RecvFacilityData(Recv):
    """FACILITY_DATA header. The header declares ``IsListItem`` as a 4-byte BOOL (data at
    offset 40); a 1-byte bool (offset 37) is also accepted, see ``parse_message``."""

    _pack_ = 1
    _fields_ = [
        ("UserRequestId", DWORD),
        ("UniqueRequestId", DWORD),
        ("ParentUniqueRequestId", DWORD),
        ("Type", DWORD),
        ("IsListItem", DWORD),
        ("ItemIndex", DWORD),
        ("ListSize", DWORD),
    ]


class RecvFacilityDataEnd(Recv):
    _pack_ = 1
    _fields_ = [("RequestId", DWORD)]


FACILITY_DATA_OFFSET = ctypes.sizeof(RecvFacilityData)  # 40
FACILITY_DATA_OFFSET_BOOL8 = FACILITY_DATA_OFFSET - 3  # 37
FACILITIES_LIST_OFFSET = ctypes.sizeof(RecvFacilitiesList)  # 28


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class OpenInfo:
    app_name: str
    app_version: tuple[int, int, int, int]
    simconnect_version: tuple[int, int, int, int]


@dataclass(frozen=True)
class QuitInfo:
    pass


@dataclass(frozen=True)
class ExceptionInfo:
    code: int
    send_id: int
    index: int

    @property
    def name(self) -> str:
        return EXCEPTION_NAMES.get(self.code, f"code {self.code}")


@dataclass(frozen=True)
class EventInfo:
    event_id: int
    data: int
    filename: str | None = None


@dataclass(frozen=True)
class AirportListEntry:
    icao: str
    region: str
    lat: float
    lon: float
    alt_m: float


@dataclass(frozen=True)
class AirportList:
    request_id: int
    entry: int
    out_of: int
    airports: tuple[AirportListEntry, ...]


@dataclass(frozen=True)
class InputEventList:
    """MSFS 2024's input events of the user's aircraft (SimConnect_EnumerateInputEvents): the B: vars its cockpit
    switches move through. (name, hash, type) each."""

    request_id: int
    entry: int
    out_of: int
    events: tuple[tuple[str, int, int], ...]


INPUT_EVENT_SIZE = 76  # SIMCONNECT_INPUT_EVENT_DESCRIPTOR: char Name[64], UINT64 Hash, DWORD eType (packed)


@dataclass(frozen=True)
class AssignedObject:
    """The object id the sim gave an AI aircraft LocalTC created."""

    request_id: int
    object_id: int


@dataclass(frozen=True)
class ModelLivery:
    """MSFS 2024's installed aircraft and liveries (EnumerateSimObjectsAndLiveries), one part of the list."""

    request_id: int
    entry: int
    out_of: int
    models: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FacilityData:
    request_id: int
    unique_id: int
    parent_id: int
    type: int
    item_index: int
    list_size: int
    payload: bytes  # at offset 40
    # The same message read with a 1-byte IsListItem (everything after it 3 bytes earlier).
    payload_bool8: bytes
    item_index_bool8: int
    list_size_bool8: int


@dataclass(frozen=True)
class FacilityDataEnd:
    request_id: int


@dataclass(frozen=True)
class ObjectData:
    request_id: int
    object_id: int
    define_id: int
    entry: int
    out_of: int
    payload: bytes


Message = (
    OpenInfo | QuitInfo | ExceptionInfo | EventInfo | ObjectData | AirportList | FacilityData | FacilityDataEnd
    | InputEventList | AssignedObject | ModelLivery
)


def parse_message(buf: bytes) -> Message | None:
    """Parse one dispatch buffer. Returns None for message types LocalTC ignores."""
    header = _read(Recv, buf)
    rid = header.dwID
    if rid == RecvId.OPEN:
        m = _read(RecvOpen, buf)
        return OpenInfo(
            app_name=_cstr(m.szApplicationName),
            app_version=(
                m.dwApplicationVersionMajor,
                m.dwApplicationVersionMinor,
                m.dwApplicationBuildMajor,
                m.dwApplicationBuildMinor,
            ),
            simconnect_version=(
                m.dwSimConnectVersionMajor,
                m.dwSimConnectVersionMinor,
                m.dwSimConnectBuildMajor,
                m.dwSimConnectBuildMinor,
            ),
        )
    if rid == RecvId.QUIT:
        return QuitInfo()
    if rid == RecvId.EXCEPTION:
        m = _read(RecvException, buf)
        return ExceptionInfo(code=m.dwException, send_id=m.dwSendID, index=m.dwIndex)
    if rid == RecvId.EVENT:
        m = _read(RecvEvent, buf)
        return EventInfo(event_id=m.uEventID, data=m.dwData)
    if rid == RecvId.EVENT_FILENAME:
        m = _read(RecvEventFilename, buf)
        return EventInfo(event_id=m.uEventID, data=m.dwData, filename=_cstr(m.szFileName))
    if rid in (RecvId.SIMOBJECT_DATA, RecvId.SIMOBJECT_DATA_BYTYPE):
        m = _read(RecvSimObjectData, buf)
        end = m.dwSize if SIMOBJECT_DATA_OFFSET <= m.dwSize <= len(buf) else len(buf)
        return ObjectData(
            request_id=m.dwRequestID,
            object_id=m.dwObjectID,
            define_id=m.dwDefineID,
            entry=m.dwentrynumber,
            out_of=m.dwoutof,
            payload=bytes(buf[SIMOBJECT_DATA_OFFSET:end]),
        )
    if rid in FACILITY_IDS:
        return _parse_facility(buf)
    if rid == RecvId.AIRPORT_LIST:
        return _parse_airport_list(buf)
    if rid == RecvId.ASSIGNED_OBJECT_ID and len(buf) >= 20:
        request_id, object_id = struct.unpack_from("<II", buf, 12)
        return AssignedObject(request_id=request_id, object_id=object_id)
    if 32 <= rid <= 41:  # MSFS 2024's lists; their IDs moved between SDK versions, so they're told by their layout
        return _parse_input_events(buf) or _parse_models(buf)
    return None


def _parse_models(buf: bytes) -> ModelLivery | None:
    """Pairs of fixed-length strings (the title, the livery): the element size from the message."""
    if len(buf) < FACILITIES_LIST_OFFSET:
        return None
    m = _read(RecvFacilitiesList, buf)
    end = _message_end(m.dwSize, buf)
    count = m.dwArraySize
    if count == 0 or (end - FACILITIES_LIST_OFFSET) % count:
        return None
    element = (end - FACILITIES_LIST_OFFSET) // count
    if element not in (512, 520, 1024):
        return None
    half = element // 2
    models = []
    for i in range(count):
        base = FACILITIES_LIST_OFFSET + i * element
        title, livery = _cstr(buf[base:base + half]), _cstr(buf[base + half:base + element])
        if not title.isprintable() or not livery.isprintable():
            return None
        models.append((title, livery))
    return ModelLivery(request_id=m.dwRequestID, entry=m.dwEntryNumber, out_of=m.dwOutOf, models=tuple(models))


def _parse_input_events(buf: bytes) -> InputEventList | None:
    if len(buf) < FACILITIES_LIST_OFFSET:
        return None
    m = _read(RecvFacilitiesList, buf)
    end = _message_end(m.dwSize, buf)
    count = m.dwArraySize
    if count == 0 or end - FACILITIES_LIST_OFFSET != count * INPUT_EVENT_SIZE:
        return None
    events = []
    for i in range(count):
        base = FACILITIES_LIST_OFFSET + i * INPUT_EVENT_SIZE
        name = _cstr(buf[base:base + 64])
        if not name or not name.isprintable():
            return None
        hash_, kind = struct.unpack_from("<QI", buf, base + 64)
        events.append((name, hash_, kind))
    return InputEventList(request_id=m.dwRequestID, entry=m.dwEntryNumber, out_of=m.dwOutOf, events=tuple(events))


def _parse_facility(buf: bytes) -> "FacilityData | FacilityDataEnd | None":
    """FACILITY_DATA vs FACILITY_DATA_END by layout, not ID (the IDs differ between docs and sim)."""
    size = _message_end(_read(Recv, buf).dwSize, buf)
    if size == FACILITY_DATA_END_SIZE:
        return FacilityDataEnd(request_id=_read(RecvFacilityDataEnd, buf).RequestId)
    if size >= FACILITY_DATA_OFFSET_BOOL8:
        m = _read(RecvFacilityData, buf) if size >= FACILITY_DATA_OFFSET else None
        if m is None:
            return None
        end = _message_end(m.dwSize, buf)
        index8, size8 = struct.unpack_from("<II", buf, FACILITY_DATA_OFFSET_BOOL8 - 8)
        return FacilityData(
            request_id=m.UserRequestId,
            unique_id=m.UniqueRequestId,
            parent_id=m.ParentUniqueRequestId,
            type=m.Type,
            item_index=m.ItemIndex,
            list_size=m.ListSize,
            payload=bytes(buf[FACILITY_DATA_OFFSET:end]),
            payload_bool8=bytes(buf[FACILITY_DATA_OFFSET_BOOL8:end]),
            item_index_bool8=index8,
            list_size_bool8=size8,
        )
    return None  # FACILITY_MINIMAL_LIST and anything else we don't use


# The list's element: ident, region, then latitude, longitude, altitude (doubles). MSFS 2024's ident is 9 bytes, 2020's 6.
AIRPORT_ELEMENT_SIZES = (36, 33)


def _parse_airport_list(buf: bytes) -> AirportList:
    """The element size is worked out from the message, and checked: a chunk with only a few airports may carry a few
    bytes of padding, which made the size come out wrong and the entries misaligned (a latitude's bytes read as an
    ident: "P@", "-:P@"). The size whose entries all read as airports wins; an entry that still doesn't is dropped."""
    m = _read(RecvFacilitiesList, buf)
    end = _message_end(m.dwSize, buf)
    count = m.dwArraySize
    if not count:
        return AirportList(request_id=m.dwRequestID, entry=m.dwEntryNumber, out_of=m.dwOutOf, airports=())
    measured = (end - FACILITIES_LIST_OFFSET) // count
    sizes = [measured] + [s for s in AIRPORT_ELEMENT_SIZES if s != measured and s * count <= end - FACILITIES_LIST_OFFSET]
    best: list[AirportListEntry] = []
    for element in sizes:
        if element - 24 < 4:
            continue
        entries = _airport_entries(buf, count, element)
        good = [e for e in entries if _plausible(e)]
        if len(good) > len(best):
            best = good
        if len(good) == count:
            break
    if not best and measured - 24 < 4:
        raise ProtocolError(f"unexpected airport list element size {measured}")
    return AirportList(request_id=m.dwRequestID, entry=m.dwEntryNumber, out_of=m.dwOutOf, airports=tuple(best))


def _airport_entries(buf: bytes, count: int, element: int) -> list[AirportListEntry]:
    text = element - 24  # three doubles follow ident + region
    ident_len = text - 3
    out = []
    for i in range(count):
        base = FACILITIES_LIST_OFFSET + i * element
        chunk = buf[base : base + element]
        if len(chunk) < element:
            break
        lat, lon, alt = struct.unpack_from("<ddd", chunk, text)
        out.append(AirportListEntry(icao=_cstr(chunk[:ident_len]), region=_cstr(chunk[ident_len:text]), lat=lat, lon=lon,
                                    alt_m=alt))
    return out


def _plausible(e: AirportListEntry) -> bool:
    return (3 <= len(e.icao) <= 5 and e.icao.isascii() and e.icao.isalnum() and e.icao == e.icao.upper()
            and -90 <= e.lat <= 90 and -180 <= e.lon <= 180 and -1000 < e.alt_m < 6000)


def _message_end(declared_size: int, buf: bytes) -> int:
    return declared_size if 0 < declared_size <= len(buf) else len(buf)


def build_message(struct: Recv, recv_id: RecvId, payload: bytes = b"") -> bytes:
    """Serialize a message the way the sim would; used by tests and fakes."""
    struct.dwID = recv_id
    struct.dwSize = ctypes.sizeof(struct) + len(payload)
    struct.dwVersion = 1
    return bytes(struct) + payload


def _read(cls: type[ctypes.Structure], buf: bytes) -> ctypes.Structure:
    size = ctypes.sizeof(cls)
    if len(buf) < size:
        raise ProtocolError(f"{cls.__name__} needs {size} bytes, got {len(buf)}")
    return cls.from_buffer_copy(buf[:size])


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
