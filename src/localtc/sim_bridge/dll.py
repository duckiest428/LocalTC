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
