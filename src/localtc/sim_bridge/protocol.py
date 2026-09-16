"""SimConnect constants and message layouts, mirroring ``SimConnect.h`` (pack 1).

Only what LocalTC uses is defined. Parsing works on plain ``bytes`` so it's
tested on any OS; ``dll.py`` copies each dispatch buffer into bytes first.
"""

import ctypes
import enum
from dataclasses import dataclass

DWORD = ctypes.c_uint32

S_OK = 0
UNUSED = 0xFFFFFFFF
OBJECT_ID_USER = 0


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
class ObjectData:
    request_id: int
    object_id: int
    define_id: int
    entry: int
    out_of: int
    payload: bytes


Message = OpenInfo | QuitInfo | ExceptionInfo | EventInfo | ObjectData


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
    return None


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
