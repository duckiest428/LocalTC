"""A fake SimConnect DLL that speaks the real message layouts, so the bridge runs on any OS."""

import math
import struct
import threading
from collections import deque

from localtc.sim_api import Airport

from localtc.sim_bridge import definitions as defs
from localtc.sim_bridge import facilities as fac
from localtc.sim_bridge.dll import SimConnectError
from localtc.sim_bridge.protocol import (
    Period,
    RecvFacilitiesList,
    RecvFacilityData,
    RecvFacilityDataEnd,
    RecvEvent,
    RecvEventFilename,
    RecvException,
    RecvId,
    RecvOpen,
    RecvSimObjectData,
    build_message,
)
from localtc.sim_bridge.simconnect_source import REQ_AIRPORT_LIST, REQ_IDENTITY, REQ_OWNSHIP, REQ_TRAFFIC

USER_OBJECT_ID = 1

OWNSHIP_VALUES = {
    "lat": 47.9005123456, "lon": -122.2790987654, "alt_msl_ft": 606.04, "alt_indicated_ft": 626.0,
    "alt_agl_ft": 0.0, "altimeter_inhg": 30.1234, "hdg_mag": 324.04, "hdg_true": 340.0, "ias_kt": 0.0,
    "gs_kt": 0.0, "vs_fpm": 0.0, "on_ground": 1, "xpdr_code": 0x1200, "xpdr_state": 4, "xpdr_ident": 0,
    "com1_mhz": 118.30000305, "com2_mhz": 121.5, "com1_tx": 0, "com2_tx": 0, "com1_type": "TWR",
    "com1_ident": "KPAE", "gear_down": 1, "flaps_index": 1, "parking_brake": 1, "engine_running": 1,
    "on_runway": 0, "wind_dir_true": 330.0, "wind_kt": 8.0, "magvar": 15.6, "altimeter_setting_inhg": 30.12,
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


def _reverse(mapping: dict[int, str]) -> dict[str, int]:
    return {v: k for k, v in mapping.items()}


DOCUMENTED_IDS = {RecvId.FACILITY_DATA: 29, RecvId.FACILITY_DATA_END: 30}  # what the SDK docs say


def _renumber(raw: bytes, recv_id: RecvId, documented: bool) -> bytes:
    if not documented:
        return raw
    return raw[:8] + struct.pack("<I", DOCUMENTED_IDS[recv_id]) + raw[12:]


def facility_message(request_id: int, item: fac.FacilityItem, index: int, values: dict, *, bool8: bool = False,
                     documented_ids: bool = False) -> bytes:
    header = RecvFacilityData(UserRequestId=request_id, UniqueRequestId=index + 1, Type=item.type_hint,
                              IsListItem=int(item is not fac.AIRPORT), ItemIndex=index)
    raw = build_message(header, RecvId.FACILITY_DATA, fac.pack(item, values))
    if bool8:  # the 1-byte bool layout: drop 3 bytes of IsListItem and shrink dwSize
        raw = struct.pack("<I", len(raw) - 3) + raw[4:29] + raw[32:]
    return _renumber(raw, RecvId.FACILITY_DATA, documented_ids)


def airport_messages(airport: Airport, request_id: int, *, bool8: bool = False, documented_ids: bool = False) -> list[bytes]:
    """Serialize an Airport the way the Facilities API would deliver it."""
    cos_lat = math.cos(math.radians(airport.lat))

    def bias(lat: float, lon: float) -> tuple[float, float]:
        return (lon - airport.lon) * fac.METERS_PER_DEG_LAT * cos_lat, (lat - airport.lat) * fac.METERS_PER_DEG_LAT

    designators, freq_kinds = _reverse(fac.RUNWAY_DESIGNATORS), _reverse(fac.FREQUENCY_KINDS)
    point_kinds, path_kinds, parking_kinds = (
        _reverse(fac.TAXI_POINT_KINDS), _reverse(fac.TAXI_PATH_KINDS), _reverse(fac.PARKING_KINDS))
    msgs = [facility_message(request_id, fac.AIRPORT, 0, {
        "LATITUDE": airport.lat, "LONGITUDE": airport.lon, "ALTITUDE": airport.elev_ft / 3.28084,
        "MAGVAR": airport.magvar, "NAME64": airport.name, "ICAO": airport.icao, "REGION": airport.region}, bool8=bool8, documented_ids=documented_ids)]
    for i, r in enumerate(airport.runways):
        msgs.append(facility_message(request_id, fac.RUNWAY, i, {
            "LATITUDE": r.lat, "LONGITUDE": r.lon, "ALTITUDE": r.elev_ft / 3.28084, "HEADING": r.heading_true,
            "LENGTH": r.length_m, "WIDTH": r.width_m, "PATTERN_ALTITUDE": 300.0, "SURFACE": r.surface,
            "PRIMARY_NUMBER": r.primary.number, "PRIMARY_DESIGNATOR": designators[r.primary.designator],
            "SECONDARY_NUMBER": r.secondary.number, "SECONDARY_DESIGNATOR": designators[r.secondary.designator],
            "PRIMARY_ILS_ICAO": r.primary.ils_ident, "SECONDARY_ILS_ICAO": r.secondary.ils_ident}, bool8=bool8, documented_ids=documented_ids))
    for i, f in enumerate(airport.frequencies):
        msgs.append(facility_message(request_id, fac.FREQUENCY, i, {
            "TYPE": freq_kinds[f.kind], "FREQUENCY": round(f.mhz * 1e6), "NAME": f.name}, bool8=bool8, documented_ids=documented_ids))
    for p in airport.taxi_points:
        x, z = bias(p.lat, p.lon)
        msgs.append(facility_message(request_id, fac.TAXI_POINT, p.index, {
            "TYPE": point_kinds[p.kind], "ORIENTATION": 0, "BIAS_X": x, "BIAS_Z": z}, bool8=bool8, documented_ids=documented_ids))
    for spot in airport.parking:
        x, z = bias(spot.lat, spot.lon)
        msgs.append(facility_message(request_id, fac.TAXI_PARKING, spot.index, {
            "TYPE": parking_kinds[spot.kind], "TAXI_POINT_TYPE": 0, "NAME": 1, "SUFFIX": 0,
            "NUMBER": int(spot.name.split()[-1]), "HEADING": spot.heading_true, "RADIUS": spot.radius_m,
            "BIAS_X": x, "BIAS_Z": z}, bool8=bool8, documented_ids=documented_ids))
    names = sorted({p.name for p in airport.taxi_paths if p.name})
    for i, p in enumerate(airport.taxi_paths):
        digits = "".join(c for c in p.runway if c.isdigit())
        msgs.append(facility_message(request_id, fac.TAXI_PATH, i, {
            "TYPE": path_kinds[p.kind], "WIDTH": p.width_m, "RUNWAY_NUMBER": int(digits or 0),
            "RUNWAY_DESIGNATOR": designators[p.runway[len(digits):]], "START": p.start, "END": p.end,
            "NAME_INDEX": names.index(p.name) if p.name else 0xFFFF}, bool8=bool8, documented_ids=documented_ids))
    for i, name in enumerate(names):
        msgs.append(facility_message(request_id, fac.TAXI_NAME, i, {"NAME": name}, bool8=bool8, documented_ids=documented_ids))
    msgs.append(_renumber(build_message(RecvFacilityDataEnd(RequestId=request_id), RecvId.FACILITY_DATA_END),
                          RecvId.FACILITY_DATA_END, documented_ids))
    return msgs


def airport_list_message(request_id: int, airports: list[Airport], *, ident_len: int = 9) -> bytes:
    body = b"".join(
        a.icao.encode().ljust(ident_len, b"\0") + a.region.encode().ljust(3, b"\0")
        + struct.pack("<ddd", a.lat, a.lon, a.elev_ft / 3.28084)
        for a in airports
    )
    header = RecvFacilitiesList(dwRequestID=request_id, dwArraySize=len(airports), dwEntryNumber=0, dwOutOf=1)
    return build_message(header, RecvId.AIRPORT_LIST, body)


class FakeSimConnect:
    """Answers requests like the sim would. ``traffic`` lists (object_id, atc_id); include the user to test filtering."""

    def __init__(self, *, sim_major: int = 12, traffic=((USER_OBJECT_ID, "N172LT"), (4242, "N12345")),
                 failed_opens: int = 0, airports: tuple[Airport, ...] = (), facility_bool8: bool = False) -> None:
        self.airports = {a.icao: a for a in airports}
        self.facility_bool8 = facility_bool8
        self.facility_definition: list[str] = []
        self.facility_requests: list[str] = []
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

    def add_to_facility_definition(self, handle, define_id, field) -> None:
        self.facility_definition.append(field)

    def request_facility_data(self, handle, define_id, request_id, icao, region="") -> None:
        self.facility_requests.append(icao)
        if icao in self.airports:
            for msg in airport_messages(self.airports[icao], request_id, bool8=self.facility_bool8):
                self.push(msg)
        else:
            self.push(build_message(RecvFacilityDataEnd(RequestId=request_id), RecvId.FACILITY_DATA_END))

    def request_facilities_list(self, handle, list_type, request_id) -> None:
        assert request_id == REQ_AIRPORT_LIST
        self.push(airport_list_message(request_id, list(self.airports.values())))

    def get_next_dispatch(self, handle: int) -> bytes | None:
        with self._lock:
            return self._inbox.popleft() if self._inbox else None
