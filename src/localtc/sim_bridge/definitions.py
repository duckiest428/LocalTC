"""SimVar data definitions and their decoding into sim_api events.

Each tuple is registered as one SimConnect data definition; the sim returns
the values packed in the same order.
"""

import struct
from dataclasses import dataclass
from typing import Any

from localtc.sim_api import AircraftIdentity, OwnshipState, TrafficTarget
from localtc.sim_api.units import bco16_to_squawk, round_mhz, xpdr_mode_name
from localtc.sim_bridge.protocol import DataType

F64, I32 = DataType.FLOAT64, DataType.INT32
S8, S32, S64, S256 = DataType.STRING8, DataType.STRING32, DataType.STRING64, DataType.STRING256


@dataclass(frozen=True)
class Datum:
    field: str
    simvar: str
    units: str | None  # None for strings
    datatype: DataType = F64


OWNSHIP: tuple[Datum, ...] = (
    Datum("lat", "PLANE LATITUDE", "degrees"),
    Datum("lon", "PLANE LONGITUDE", "degrees"),
    Datum("alt_msl_ft", "PLANE ALTITUDE", "feet"),
    Datum("alt_indicated_ft", "INDICATED ALTITUDE", "feet"),
    Datum("alt_agl_ft", "PLANE ALT ABOVE GROUND", "feet"),
    Datum("altimeter_inhg", "KOHLSMAN SETTING HG:1", "inHg"),
    Datum("hdg_mag", "PLANE HEADING DEGREES MAGNETIC", "degrees"),
    Datum("hdg_true", "PLANE HEADING DEGREES TRUE", "degrees"),
    Datum("ias_kt", "AIRSPEED INDICATED", "knots"),
    Datum("gs_kt", "GROUND VELOCITY", "knots"),
    Datum("vs_fpm", "VERTICAL SPEED", "feet per minute"),
    Datum("on_ground", "SIM ON GROUND", "Bool", I32),
    Datum("xpdr_code", "TRANSPONDER CODE:1", "Bco16", I32),
    Datum("xpdr_state", "TRANSPONDER STATE:1", "Enum", I32),
    Datum("xpdr_ident", "TRANSPONDER IDENT:1", "Bool", I32),
    # Requested in MHz rather than BCD16 so 8.33 kHz channels survive.
    Datum("com1_mhz", "COM ACTIVE FREQUENCY:1", "MHz"),
    Datum("com2_mhz", "COM ACTIVE FREQUENCY:2", "MHz"),
    Datum("com1_tx", "COM TRANSMIT:1", "Bool", I32),
    Datum("com2_tx", "COM TRANSMIT:2", "Bool", I32),
    Datum("com1_type", "COM ACTIVE FREQ TYPE:1", None, S32),
    Datum("com1_ident", "COM ACTIVE FREQ IDENT:1", None, S32),
    Datum("gear_down", "GEAR HANDLE POSITION", "Bool", I32),
    Datum("flaps_index", "FLAPS HANDLE INDEX", "Number", I32),
    Datum("parking_brake", "BRAKE PARKING POSITION", "Bool", I32),
    Datum("engine_running", "GENERAL ENG COMBUSTION:1", "Bool", I32),
    Datum("on_runway", "ON ANY RUNWAY", "Bool", I32),
    # Fuel and weight: what an emergency call and a realistic clearance limit are worked out from.
    Datum("fuel_lb", "FUEL TOTAL QUANTITY WEIGHT", "pounds"),
    Datum("fuel_flow_pph", "ENG FUEL FLOW PPH:1", "pounds per hour"),
    Datum("gross_weight_lb", "TOTAL WEIGHT", "pounds"),
    Datum("wind_dir_true", "AMBIENT WIND DIRECTION", "degrees"),
    Datum("wind_kt", "AMBIENT WIND VELOCITY", "knots"),
    Datum("magvar", "MAGVAR", "degrees"),
    Datum("altimeter_setting_inhg", "SEA LEVEL PRESSURE", "inHg"),
    Datum("temperature_c", "AMBIENT TEMPERATURE", "celsius"),
    Datum("visibility_m", "AMBIENT VISIBILITY", "meters"),
    Datum("precip", "AMBIENT PRECIP STATE", "mask", I32),
    Datum("in_cloud", "AMBIENT IN CLOUD", "Bool", I32),
    Datum("zulu_s", "ZULU TIME", "seconds"),
    # Not every aircraft reports combustion (MSFS 2024's CS300 never does): N1 or RPM says the engine is turning.
    Datum("eng_n1", "TURB ENG N1:1", "percent"),
    Datum("eng_rpm", "GENERAL ENG RPM:1", "rpm"),
)

# Requested with PERIOD_SECOND + FLAG_CHANGED, so it only arrives when something changes.
IDENTITY: tuple[Datum, ...] = (
    Datum("title", "TITLE", None, S256),
    Datum("atc_id", "ATC ID", None, S32),
    Datum("airline", "ATC AIRLINE", None, S64),
    Datum("flight_number", "ATC FLIGHT NUMBER", None, S8),
    Datum("atc_type", "ATC TYPE", None, S32),
    Datum("atc_model", "ATC MODEL", None, S32),
)

TRAFFIC: tuple[Datum, ...] = (
    Datum("atc_id", "ATC ID", None, S32),
    Datum("airline", "ATC AIRLINE", None, S64),
    Datum("flight_number", "ATC FLIGHT NUMBER", None, S8),
    Datum("atc_model", "ATC MODEL", None, S32),
    Datum("lat", "PLANE LATITUDE", "degrees"),
    Datum("lon", "PLANE LONGITUDE", "degrees"),
    Datum("alt_ft", "PLANE ALTITUDE", "feet"),
    Datum("hdg_true", "PLANE HEADING DEGREES TRUE", "degrees"),
    Datum("gs_kt", "GROUND VELOCITY", "knots"),
    Datum("on_ground", "SIM ON GROUND", "Bool", I32),
)

_FORMAT_CODES = {
    DataType.INT32: "i",
    DataType.INT64: "q",
    DataType.FLOAT32: "f",
    DataType.FLOAT64: "d",
    DataType.STRING8: "8s",
    DataType.STRING32: "32s",
    DataType.STRING64: "64s",
    DataType.STRING128: "128s",
    DataType.STRING256: "256s",
    DataType.STRING260: "260s",
}


def struct_format(datums: tuple[Datum, ...]) -> str:
    return "<" + "".join(_FORMAT_CODES[d.datatype] for d in datums)


def payload_size(datums: tuple[Datum, ...]) -> int:
    return struct.calcsize(struct_format(datums))


def unpack(datums: tuple[Datum, ...], payload: bytes) -> dict[str, Any]:
    values = struct.unpack_from(struct_format(datums), payload)
    return {
        d.field: (v.split(b"\0", 1)[0].decode("utf-8", errors="replace") if isinstance(v, bytes) else v)
        for d, v in zip(datums, values)
    }


def pack(datums: tuple[Datum, ...], values: dict[str, Any]) -> bytes:
    """Inverse of ``unpack``; used by tests and the fake DLL."""
    out = [values[d.field].encode() if isinstance(values[d.field], str) else values[d.field] for d in datums]
    return struct.pack(struct_format(datums), *out)


def ownship_from_raw(raw: dict[str, Any], t: float) -> OwnshipState:
    return OwnshipState(
        t=t,
        lat=round(raw["lat"], 6),
        lon=round(raw["lon"], 6),
        alt_msl_ft=round(raw["alt_msl_ft"], 1),
        alt_indicated_ft=round(raw["alt_indicated_ft"], 1),
        alt_agl_ft=round(raw["alt_agl_ft"], 1),
        altimeter_inhg=round(raw["altimeter_inhg"], 2),
        hdg_mag=round(raw["hdg_mag"], 1),
        hdg_true=round(raw["hdg_true"], 1),
        ias_kt=round(raw["ias_kt"], 1),
        gs_kt=round(raw["gs_kt"], 1),
        vs_fpm=round(raw["vs_fpm"]),
        on_ground=bool(raw["on_ground"]),
        squawk=bco16_to_squawk(raw["xpdr_code"]),
        xpdr_mode=xpdr_mode_name(raw["xpdr_state"]),
        xpdr_ident=bool(raw["xpdr_ident"]),
        com1_mhz=round_mhz(raw["com1_mhz"]),
        com2_mhz=round_mhz(raw["com2_mhz"]),
        com1_tx=bool(raw["com1_tx"]),
        com2_tx=bool(raw["com2_tx"]),
        com1_type=raw["com1_type"],
        com1_ident=raw["com1_ident"],
        gear_down=bool(raw["gear_down"]),
        flaps_index=int(raw["flaps_index"]),
        parking_brake=bool(raw["parking_brake"]),
        engine_running=bool(raw["engine_running"]) or raw.get("eng_n1", 0.0) > 15.0 or raw.get("eng_rpm", 0.0) > 300.0,
        on_runway=bool(raw["on_runway"]),
        wind_dir_true=round(raw["wind_dir_true"], 1),
        wind_kt=round(raw["wind_kt"], 1),
        magvar=round(raw["magvar"], 1),
        altimeter_setting_inhg=round(raw["altimeter_setting_inhg"], 2),
        temperature_c=round(raw["temperature_c"], 1),
        visibility_m=round(raw["visibility_m"]),
        precip=int(raw["precip"]),
        in_cloud=bool(raw["in_cloud"]),
        zulu_s=round(raw["zulu_s"], 1),
    )


def identity_from_raw(raw: dict[str, Any], t: float) -> AircraftIdentity:
    return AircraftIdentity(t=t, **raw)


def traffic_from_raw(raw: dict[str, Any], object_id: int) -> TrafficTarget:
    return TrafficTarget(
        object_id=object_id,
        atc_id=raw["atc_id"],
        airline=raw["airline"],
        flight_number=raw["flight_number"],
        atc_model=raw["atc_model"],
        lat=round(raw["lat"], 6),
        lon=round(raw["lon"], 6),
        alt_ft=round(raw["alt_ft"], 1),
        hdg_true=round(raw["hdg_true"], 1),
        gs_kt=round(raw["gs_kt"], 1),
        on_ground=bool(raw["on_ground"]),
    )
