"""ctypes bindings for the MSFS 2024 SDK's ``SimConnect.dll`` (Windows only)."""

import ctypes
import os
import sys
from ctypes import POINTER, byref, c_char_p, c_float, c_int, c_long, c_uint32, c_void_p
from pathlib import Path

from localtc.sim_api import SourceUnavailable
from localtc.sim_bridge.protocol import S_OK, UNUSED, DataType, FacilityListType, Period, SimObjectType


class SimConnectError(RuntimeError):
    """A SimConnect call failed; usually the sim isn't running or the connection dropped."""


class SimConnectUnavailable(SourceUnavailable):
    """SimConnect can't be used on this machine (not Windows, or no DLL)."""


def find_dll(explicit: str | None = None) -> Path:
    """Locate SimConnect.dll: explicit path, $LOCALTC_SIMCONNECT_DLL, SDK env vars, the default SDK folder, then ./."""
    if sys.platform != "win32":
        raise SimConnectUnavailable(
            "The live SimConnect bridge only runs on Windows. "
            'Use source.kind = "replay" (or LOCALTC_SOURCE=replay) on this machine.'
        )
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if env_path := os.environ.get("LOCALTC_SIMCONNECT_DLL"):
        candidates.append(Path(env_path))
    for var in ("MSFS2024_SDK", "MSFS_SDK"):
        if sdk := os.environ.get(var):
            candidates.append(Path(sdk) / "SimConnect SDK" / "lib" / "SimConnect.dll")
    # The SDK installer's default location; it doesn't always set the variables above.
    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    candidates.append(Path(system_drive) / "MSFS 2024 SDK" / "SimConnect SDK" / "lib" / "SimConnect.dll")
    candidates.append(Path.cwd() / "SimConnect.dll")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    looked = "\n  ".join(str(c) for c in candidates)
    raise SimConnectUnavailable(
        "SimConnect.dll not found. Install the MSFS 2024 SDK, or set live.dll_path / "
        f"LOCALTC_SIMCONNECT_DLL.\nLooked in:\n  {looked}"
    )


class InitPosition(ctypes.Structure):
    """SIMCONNECT_DATA_INITPOSITION: where an AI aircraft starts."""

    _fields_ = [("Latitude", ctypes.c_double), ("Longitude", ctypes.c_double), ("Altitude", ctypes.c_double),
                ("Pitch", ctypes.c_double), ("Bank", ctypes.c_double), ("Heading", ctypes.c_double),
                ("OnGround", ctypes.c_uint32), ("Airspeed", ctypes.c_uint32)]


class SimConnectDll:
    def __init__(self, path: Path) -> None:
        if sys.platform != "win32":
            raise SimConnectUnavailable("SimConnect.dll can only be loaded on Windows")
        self.path = path
        lib = ctypes.WinDLL(str(path))
        self._open = _bind(lib, "SimConnect_Open", [POINTER(c_void_p), c_char_p, c_void_p, c_uint32, c_void_p, c_uint32])
        self._close = _bind(lib, "SimConnect_Close", [c_void_p])
        self._add_to_data_definition = _bind(
            lib, "SimConnect_AddToDataDefinition", [c_void_p, c_uint32, c_char_p, c_char_p, c_int, c_float, c_uint32]
        )
        self._request_data_on_sim_object = _bind(
            lib,
            "SimConnect_RequestDataOnSimObject",
            [c_void_p, c_uint32, c_uint32, c_uint32, c_int, c_uint32, c_uint32, c_uint32, c_uint32],
        )
        self._request_data_on_sim_object_type = _bind(
            lib, "SimConnect_RequestDataOnSimObjectType", [c_void_p, c_uint32, c_uint32, c_uint32, c_int]
        )
        self._subscribe_to_system_event = _bind(lib, "SimConnect_SubscribeToSystemEvent", [c_void_p, c_uint32, c_char_p])
        self._add_to_facility_definition = _bind(lib, "SimConnect_AddToFacilityDefinition", [c_void_p, c_uint32, c_char_p])
        self._request_facility_data = _bind(
            lib, "SimConnect_RequestFacilityData", [c_void_p, c_uint32, c_uint32, c_char_p, c_char_p]
        )
        # The docs spell it "Facilites"; bind whichever the DLL exports.
        self._request_facilities_list = _bind(
            lib, ("SimConnect_RequestFacilitiesList_EX1", "SimConnect_RequestFacilitesList_EX1"), [c_void_p, c_int, c_uint32]
        )
        self._map_client_event = _bind(lib, "SimConnect_MapClientEventToSimEvent", [c_void_p, c_uint32, c_char_p])
        self._transmit_client_event = _bind(
            lib, "SimConnect_TransmitClientEvent", [c_void_p, c_uint32, c_uint32, c_uint32, c_uint32, c_uint32]
        )
        try:  # (with a second value: "which one", for KOHLSMAN_SET and the like)
            self._transmit_client_event_ex1 = _bind(
                lib, "SimConnect_TransmitClientEvent_EX1",
                [c_void_p, c_uint32, c_uint32, c_uint32, c_uint32, c_uint32, c_uint32, c_uint32, c_uint32, c_uint32])
        except SimConnectUnavailable:
            self._transmit_client_event_ex1 = None
        try:  # MSFS 2024 only: the aircraft's input events (B: vars)
            self._enumerate_input_events = _bind(lib, "SimConnect_EnumerateInputEvents", [c_void_p, c_uint32])
            self._set_input_event = _bind(lib, "SimConnect_SetInputEvent", [c_void_p, ctypes.c_uint64, c_uint32, c_void_p])
        except SimConnectUnavailable:
            self._enumerate_input_events = self._set_input_event = None
        self._ai = {}  # creating and removing AI aircraft (EXPERIMENTAL traffic control); whichever this DLL has
        for key, names, args in (
            ("non_atc_ex1", ("SimConnect_AICreateNonATCAircraft_EX1",), [c_void_p, c_char_p, c_char_p, c_char_p, InitPosition, c_uint32]),
            ("non_atc", ("SimConnect_AICreateNonATCAircraft",), [c_void_p, c_char_p, c_char_p, InitPosition, c_uint32]),
            ("enroute_ex1", ("SimConnect_AICreateEnrouteATCAircraft_EX1",),
             [c_void_p, c_char_p, c_char_p, c_char_p, ctypes.c_int, c_char_p, ctypes.c_double, ctypes.c_int, c_uint32]),
            ("enroute", ("SimConnect_AICreateEnrouteATCAircraft",),
             [c_void_p, c_char_p, c_char_p, ctypes.c_int, c_char_p, ctypes.c_double, ctypes.c_int, c_uint32]),
            ("remove", ("SimConnect_AIRemoveObject",), [c_void_p, c_uint32, c_uint32]),
            ("models", ("SimConnect_EnumerateSimObjectsAndLiveries",), [c_void_p, c_uint32, ctypes.c_int]),
        ):
            try:
                self._ai[key] = _bind(lib, names, args)
            except SimConnectUnavailable:
                pass
        self._add_client_event_to_group = _bind(
            lib, "SimConnect_AddClientEventToNotificationGroup", [c_void_p, c_uint32, c_uint32, c_int]
        )
        self._set_group_priority = _bind(lib, "SimConnect_SetNotificationGroupPriority", [c_void_p, c_uint32, c_uint32])
        self._map_input_event = _bind(
            lib, ("SimConnect_MapInputEventToClientEvent_EX1", "SimConnect_MapInputEventToClientEvent"),
            [c_void_p, c_uint32, c_char_p, c_uint32, c_uint32, c_uint32, c_uint32, c_int],
        )
        self._set_input_group_state = _bind(lib, "SimConnect_SetInputGroupState", [c_void_p, c_uint32, c_uint32])
        self._set_data_on_sim_object = _bind(
            lib, "SimConnect_SetDataOnSimObject", [c_void_p, c_uint32, c_uint32, c_uint32, c_uint32, c_uint32, c_void_p]
        )
        self._get_next_dispatch = _bind(
            lib, "SimConnect_GetNextDispatch", [c_void_p, POINTER(c_void_p), POINTER(c_uint32)]
        )

    def open(self, app_name: str) -> int:
        handle = c_void_p()
        _check(self._open(byref(handle), app_name.encode(), None, 0, None, 0), "SimConnect_Open")
        return handle.value

    def close(self, handle: int) -> None:
        self._close(handle)

    def add_to_data_definition(
        self, handle: int, define_id: int, simvar: str, units: str | None, datatype: DataType
    ) -> None:
        hr = self._add_to_data_definition(
            handle, define_id, simvar.encode(), units.encode() if units else None, int(datatype), 0.0, UNUSED
        )
        _check(hr, f"AddToDataDefinition({simvar})")

    def request_data_on_sim_object(
        self,
        handle: int,
        request_id: int,
        define_id: int,
        object_id: int,
        period: Period,
        flags: int = 0,
        origin: int = 0,
        interval: int = 0,
        limit: int = 0,
    ) -> None:
        hr = self._request_data_on_sim_object(
            handle, request_id, define_id, object_id, int(period), int(flags), origin, interval, limit
        )
        _check(hr, "RequestDataOnSimObject")

    def request_data_on_sim_object_type(
        self, handle: int, request_id: int, define_id: int, radius_m: int, object_type: SimObjectType
    ) -> None:
        hr = self._request_data_on_sim_object_type(handle, request_id, define_id, radius_m, int(object_type))
        _check(hr, "RequestDataOnSimObjectType")

    def subscribe_to_system_event(self, handle: int, event_id: int, name: str) -> None:
        _check(self._subscribe_to_system_event(handle, event_id, name.encode()), f"SubscribeToSystemEvent({name})")

    def add_to_facility_definition(self, handle: int, define_id: int, field: str) -> None:
        _check(self._add_to_facility_definition(handle, define_id, field.encode()), f"AddToFacilityDefinition({field})")

    def request_facility_data(self, handle: int, define_id: int, request_id: int, icao: str, region: str = "") -> None:
        hr = self._request_facility_data(handle, define_id, request_id, icao.encode(), region.encode())
        _check(hr, f"RequestFacilityData({icao})")

    def request_facilities_list(self, handle: int, list_type: FacilityListType, request_id: int) -> None:
        _check(self._request_facilities_list(handle, int(list_type), request_id), "RequestFacilitiesList_EX1")

    def map_client_event_to_sim_event(self, handle: int, event_id: int, name: str) -> None:
        _check(self._map_client_event(handle, event_id, name.encode()), f"MapClientEventToSimEvent({name})")

    def transmit_client_event(self, handle: int, object_id: int, event_id: int, data: int, group: int, flags: int) -> None:
        hr = self._transmit_client_event(handle, object_id, event_id, data & 0xFFFFFFFF, group, flags)
        _check(hr, f"TransmitClientEvent({event_id}, {data})")

    def transmit_client_event_ex1(self, handle: int, object_id: int, event_id: int, group: int, flags: int,
                                  data0: int, data1: int = 0) -> None:
        if self._transmit_client_event_ex1 is None:
            self.transmit_client_event(handle, object_id, event_id, data0, group, flags)
            return
        hr = self._transmit_client_event_ex1(handle, object_id, event_id, group, flags, data0 & 0xFFFFFFFF,
                                             data1 & 0xFFFFFFFF, 0, 0, 0)
        _check(hr, f"TransmitClientEvent_EX1({event_id}, {data0}, {data1})")

    def enumerate_input_events(self, handle: int, request_id: int) -> None:
        if self._enumerate_input_events is None:
            raise SimConnectError("this SimConnect has no input events")
        _check(self._enumerate_input_events(handle, request_id), "EnumerateInputEvents")

    def set_input_event(self, handle: int, hash_: int, value: float) -> None:
        if self._set_input_event is None:
            raise SimConnectError("this SimConnect has no input events")
        v = ctypes.c_double(value)
        _check(self._set_input_event(handle, ctypes.c_uint64(hash_), ctypes.sizeof(v), ctypes.byref(v)),
               f"SetInputEvent({hash_:x}, {value})")

    def ai_create(self, handle: int, kind: str, request_id: int, title: str, livery: str, tail: str, *,
                  flight_number: int = -1, lat: float = 0, lon: float = 0, alt_ft: float = 0, heading: float = 0,
                  on_ground: bool = True, airspeed_kt: float = 0, plan: str = "", plan_position: float = 0) -> None:
        """An AI aircraft: ``parked`` (still, where it's put) or ``enroute`` (flying ``plan`` under the sim's AI)."""
        if kind == "enroute":
            if "enroute_ex1" in self._ai:
                hr = self._ai["enroute_ex1"](handle, title.encode(), livery.encode(), tail.encode(), flight_number,
                                             plan.encode(), plan_position, 0, request_id)
            elif "enroute" in self._ai:
                hr = self._ai["enroute"](handle, title.encode(), tail.encode(), flight_number, plan.encode(),
                                         plan_position, 0, request_id)
            else:
                raise SimConnectError("this SimConnect can't create AI aircraft")
        else:
            pos = InitPosition(lat, lon, alt_ft, 0.0, 0.0, heading, int(on_ground), int(airspeed_kt))
            if "non_atc_ex1" in self._ai:
                hr = self._ai["non_atc_ex1"](handle, title.encode(), livery.encode(), tail.encode(), pos, request_id)
            elif "non_atc" in self._ai:
                hr = self._ai["non_atc"](handle, title.encode(), tail.encode(), pos, request_id)
            else:
                raise SimConnectError("this SimConnect can't create AI aircraft")
        _check(hr, f"AICreate {kind} {title}")

    def ai_remove(self, handle: int, object_id: int, request_id: int) -> None:
        if "remove" not in self._ai:
            raise SimConnectError("this SimConnect can't remove AI aircraft")
        _check(self._ai["remove"](handle, object_id, request_id), f"AIRemoveObject({object_id})")

    def enumerate_models(self, handle: int, request_id: int) -> None:
        if "models" not in self._ai:
            raise SimConnectError("this SimConnect can't list the installed aircraft (MSFS 2024 only)")
        _check(self._ai["models"](handle, request_id, 2), "EnumerateSimObjectsAndLiveries")  # SIMCONNECT_SIMOBJECT_TYPE_AIRCRAFT (1 is ALL: refused)

    def map_input_to_events(self, handle: int, group: int, definition: str, down_event: int, up_event: int) -> None:
        """A key or joystick button (``definition``, e.g. "joystick:0:button:3") sends ``down_event`` when pressed
        and ``up_event`` when released, without taking the input away from the sim."""
        for event_id in (down_event, up_event):
            _check(self._map_client_event(handle, event_id, b""), "MapClientEventToSimEvent(private)")
            _check(self._add_client_event_to_group(handle, group, event_id, 0), "AddClientEventToNotificationGroup")
        _check(self._set_group_priority(handle, group, 1), "SetNotificationGroupPriority")  # highest
        hr = self._map_input_event(handle, group, definition.encode(), down_event, 0, up_event, 0, 0)
        _check(hr, f"MapInputEventToClientEvent({definition})")
        _check(self._set_input_group_state(handle, group, 1), "SetInputGroupState")  # on

    def set_data_on_sim_object(self, handle: int, define_id: int, object_id: int, data: bytes) -> None:
        """Write one data definition's values (packed as the definition reads them) to an object."""
        buffer = ctypes.create_string_buffer(data, len(data))
        hr = self._set_data_on_sim_object(handle, define_id, object_id, 0, 0, len(data), buffer)
        _check(hr, f"SetDataOnSimObject({define_id})")

    def get_next_dispatch(self, handle: int) -> bytes | None:
        """Copy the next pending message out of SimConnect's buffer, or None if there isn't one."""
        ptr, size = c_void_p(), c_uint32()
        if self._get_next_dispatch(handle, byref(ptr), byref(size)) != S_OK or not ptr.value:
            return None
        return ctypes.string_at(ptr.value, size.value)


def _bind(lib: ctypes.CDLL, name: str | tuple[str, ...], argtypes: list) -> "ctypes._CFuncPtr":
    names = (name,) if isinstance(name, str) else name
    for candidate in names:
        fn = getattr(lib, candidate, None)
        if fn is not None:
            break
    else:
        raise SimConnectUnavailable(f"SimConnect.dll has no {' / '.join(names)}; is it from the MSFS 2024 SDK?")
    fn.argtypes = argtypes
    fn.restype = c_long  # HRESULT
    return fn


def _check(hr: int, what: str) -> None:
    if hr != S_OK:
        raise SimConnectError(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")
