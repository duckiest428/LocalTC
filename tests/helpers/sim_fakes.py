"""A fake SimConnect DLL that speaks the real message layouts, so the bridge runs on any OS."""

import threading
from collections import deque

from localtc.sim_bridge import definitions as defs
from localtc.sim_bridge.dll import SimConnectError
from localtc.sim_bridge.protocol import (
    Period,
    RecvEvent,
    RecvEventFilename,
    RecvException,
    RecvId,
    RecvOpen,
    RecvSimObjectData,
    build_message,
)
from localtc.sim_bridge.simconnect_source import REQ_IDENTITY, REQ_OWNSHIP, REQ_TRAFFIC

USER_OBJECT_ID = 1

OWNSHIP_VALUES = {
    "lat": 47.9005123456, "lon": -122.2790987654, "alt_msl_ft": 606.04, "alt_indicated_ft": 626.0,
    "alt_agl_ft": 0.0, "altimeter_inhg": 30.1234, "hdg_mag": 324.04, "hdg_true": 340.0, "ias_kt": 0.0,
    "gs_kt": 0.0, "vs_fpm": 0.0, "on_ground": 1, "xpdr_code": 0x1200, "xpdr_state": 4, "xpdr_ident": 0,
    "com1_mhz": 118.30000305, "com2_mhz": 121.5, "com1_tx": 0, "com2_tx": 0, "com1_type": "TWR",
    "com1_ident": "KPAE", "gear_down": 1, "flaps_index": 1, "parking_brake": 1, "engine_running": 1,
}

IDENTITY_VALUES = {
    "title": "Cessna Skyhawk G1000", "atc_id": "N172LT", "airline": "", "flight_number": "",
    "atc_type": "Cessna", "atc_model": "C172",
}


def traffic_values(atc_id: str) -> dict:
    return {
        "atc_id": atc_id, "airline": "", "flight_number": "", "atc_model": "PA28", "lat": 47.91,
        "lon": -122.29, "alt_ft": 1600.0, "hdg_true": 90.0, "gs_kt": 95.0, "on_ground": 0,
    }


def data_message(recv_id: RecvId, request_id: int, object_id: int, entry: int, out_of: int, payload: bytes) -> bytes:
    s = RecvSimObjectData(dwRequestID=request_id, dwObjectID=object_id, dwentrynumber=entry, dwoutof=out_of)
    return build_message(s, recv_id, payload)


def open_message(major: int = 12) -> bytes:
    return build_message(
        RecvOpen(
            szApplicationName=b"KittyHawk",
            dwApplicationVersionMajor=major, dwApplicationVersionMinor=1,
            dwSimConnectVersionMajor=major, dwSimConnectVersionMinor=1,
        ),
        RecvId.OPEN,
    )


class FakeSimConnect:
    """Answers requests like the sim would. ``traffic`` lists (object_id, atc_id); include the user to test filtering."""

    def __init__(self, *, sim_major: int = 12, traffic=((USER_OBJECT_ID, "N172LT"), (4242, "N12345")),
                 failed_opens: int = 0) -> None:
        self.sim_major = sim_major
        self.traffic = traffic
        self.failed_opens = failed_opens
        self.opens = 0
        self.closes = 0
        self.definitions: dict[int, list[str]] = {}
        self.system_events: dict[str, int] = {}
        self._inbox: deque[bytes] = deque()
        self._lock = threading.Lock()

    # --- test controls
    def push(self, message: bytes) -> None:
        with self._lock:
            self._inbox.append(message)

    def push_event(self, name: str, data: int = 0, filename: str | None = None) -> None:
        event_id = self.system_events[name]
        if filename is None:
            self.push(build_message(RecvEvent(uEventID=event_id, dwData=data), RecvId.EVENT))
        else:
            msg = RecvEventFilename(szFileName=filename.encode())
            msg.uEventID, msg.dwData = event_id, data
            self.push(build_message(msg, RecvId.EVENT_FILENAME))

    def push_quit(self) -> None:
        self.push(build_message(RecvEvent(), RecvId.QUIT))

    def push_exception(self, code: int = 7) -> None:
        self.push(build_message(RecvException(dwException=code, dwSendID=3, dwIndex=2), RecvId.EXCEPTION))

    # --- SimConnectApi
    def open(self, app_name: str) -> int:
        self.opens += 1
        if self.opens <= self.failed_opens:
            raise SimConnectError("SimConnect_Open failed (HRESULT 0x80004005)")
        self.push(open_message(self.sim_major))
        return 1

    def close(self, handle: int) -> None:
        self.closes += 1

    def add_to_data_definition(self, handle, define_id, simvar, units, datatype) -> None:
        self.definitions.setdefault(define_id, []).append(simvar)

    def subscribe_to_system_event(self, handle, event_id, name) -> None:
        self.system_events[name] = event_id

    def request_data_on_sim_object(self, handle, request_id, define_id, object_id, period, flags=0, *rest) -> None:
        if request_id == REQ_OWNSHIP and period == Period.ONCE:
            payload = defs.pack(defs.OWNSHIP, OWNSHIP_VALUES)
            self.push(data_message(RecvId.SIMOBJECT_DATA, request_id, USER_OBJECT_ID, 1, 1, payload))
        elif request_id == REQ_IDENTITY:
            payload = defs.pack(defs.IDENTITY, IDENTITY_VALUES)
            self.push(data_message(RecvId.SIMOBJECT_DATA, request_id, USER_OBJECT_ID, 1, 1, payload))

    def request_data_on_sim_object_type(self, handle, request_id, define_id, radius_m, object_type) -> None:
        assert request_id == REQ_TRAFFIC
        n = len(self.traffic)
        for entry, (object_id, atc_id) in enumerate(self.traffic, start=1):
            payload = defs.pack(defs.TRAFFIC, traffic_values(atc_id))
            self.push(data_message(RecvId.SIMOBJECT_DATA_BYTYPE, request_id, object_id, entry, n, payload))

    def get_next_dispatch(self, handle: int) -> bytes | None:
        with self._lock:
            return self._inbox.popleft() if self._inbox else None
