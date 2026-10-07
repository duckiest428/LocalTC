"""SimVar data definitions and their decoding into sim_api events.

Each tuple is registered as one SimConnect data definition; the sim returns
the values packed in the same order.
"""

import struct
from dataclasses import dataclass
from typing import Any

from localtc.sim_api import AircraftIdentity, AircraftSystems, OwnshipState, TrafficTarget
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
    # For the ATIS (added in 0.4, last so an older sim that doesn't know them only loses these): how hard it's
    # raining or snowing, and smoke (wildfire haze).
    Datum("precip_rate_mm", "AMBIENT PRECIP RATE", "millimeters of water"),
    Datum("in_smoke", "AMBIENT IN SMOKE", "Bool", I32),
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

# The switches and settings the copilot checks (localtc.crew). Requested with PERIOD_SECOND + FLAG_CHANGED.
AIRCRAFT: tuple[Datum, ...] = (
    Datum("gear_pct", "GEAR TOTAL PCT EXTENDED", "percent"),
    Datum("flaps_pct", "TRAILING EDGE FLAPS LEFT PERCENT", "percent"),
    Datum("flaps_positions", "FLAPS NUM HANDLE POSITIONS", "Number", I32),
    Datum("spoilers_pct", "SPOILERS HANDLE POSITION", "percent"),
    Datum("spoilers_armed", "SPOILERS ARMED", "Bool", I32),
    Datum("light_landing", "LIGHT LANDING", "Bool", I32),
    Datum("light_taxi", "LIGHT TAXI", "Bool", I32),
    Datum("light_strobe", "LIGHT STROBE", "Bool", I32),
    Datum("light_beacon", "LIGHT BEACON", "Bool", I32),
    Datum("light_nav", "LIGHT NAV", "Bool", I32),
    Datum("light_logo", "LIGHT LOGO", "Bool", I32),
    Datum("ap_master", "AUTOPILOT MASTER", "Bool", I32),
    Datum("ap_heading", "AUTOPILOT HEADING LOCK", "Bool", I32),
    Datum("ap_nav", "AUTOPILOT NAV1 LOCK", "Bool", I32),
    Datum("ap_approach", "AUTOPILOT APPROACH HOLD", "Bool", I32),
    Datum("ap_altitude", "AUTOPILOT ALTITUDE LOCK", "Bool", I32),
    Datum("ap_vs", "AUTOPILOT VERTICAL HOLD", "Bool", I32),
    Datum("ap_flc", "AUTOPILOT FLIGHT LEVEL CHANGE", "Bool", I32),
    Datum("athr_armed", "AUTOPILOT THROTTLE ARM", "Bool", I32),
    Datum("ap_heading_sel", "AUTOPILOT HEADING LOCK DIR", "degrees"),
    Datum("ap_altitude_sel", "AUTOPILOT ALTITUDE LOCK VAR", "feet"),
    Datum("ap_speed_sel", "AUTOPILOT AIRSPEED HOLD VAR", "knots"),
    Datum("ap_vs_sel", "AUTOPILOT VERTICAL HOLD VAR", "feet per minute"),
    Datum("com1_standby_mhz", "COM STANDBY FREQUENCY:1", "MHz"),
    Datum("radio_height_ft", "RADIO HEIGHT", "feet"),
    Datum("battery", "ELECTRICAL MASTER BATTERY", "Bool", I32),
    Datum("eng1", "GENERAL ENG COMBUSTION:1", "Bool", I32),
    Datum("eng2", "GENERAL ENG COMBUSTION:2", "Bool", I32),
    Datum("eng3", "GENERAL ENG COMBUSTION:3", "Bool", I32),
    Datum("eng4", "GENERAL ENG COMBUSTION:4", "Bool", I32),
    Datum("mach", "AIRSPEED MACH", "mach"),
)

# Asked for separately from AIRCRAFT: an add-on (or a sim version) that doesn't know one of these names fails only
# this request, not the switches the copilot works with.
AIRCRAFT_EXTRA: tuple[Datum, ...] = (
    Datum("vs0_kt", "DESIGN SPEED VS0", "knots"),
    Datum("vs1_kt", "DESIGN SPEED VS1", "knots"),
    Datum("takeoff_kt", "DESIGN TAKEOFF SPEED", "knots"),
    Datum("vmo_kt", "DESIGN SPEED VC", "knots"),
    Datum("stall_warning", "STALL WARNING", "Bool", I32),
    Datum("overspeed_warning", "OVERSPEED WARNING", "Bool", I32),
    Datum("loc_received", "NAV HAS LOCALIZER:1", "Bool", I32),
    Datum("gs_received", "NAV HAS GLIDE SLOPE:1", "Bool", I32),
    Datum("loc_deviation", "NAV CDI:1", "Number"),
    Datum("gs_deviation", "NAV GSI:1", "Number"),
)

# A third request (0.4), for the same reason: the load factor (turbulence), ice, the autobrake and the reversers.
AIRCRAFT_MORE: tuple[Datum, ...] = (
    Datum("g_force", "G FORCE", "GForce"),
    Datum("ice_pct", "STRUCTURAL ICE PCT", "percent over 100"),
    Datum("eng_anti_ice", "ENG ANTI ICE:1", "Bool", I32),
    Datum("wing_deice", "STRUCTURAL DEICE SWITCH", "Bool", I32),
    Datum("autobrake", "AUTO BRAKE SWITCH CB", "Number", I32),
    Datum("autobrake_active", "AUTOBRAKES ACTIVE", "Bool", I32),
    Datum("rev1", "TURB ENG REVERSE NOZZLE PERCENT:1", "percent"),
    Datum("rev2", "TURB ENG REVERSE NOZZLE PERCENT:2", "percent"),
    Datum("engine_type", "ENGINE TYPE", "Enum", I32),
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

# EXPERIMENTAL traffic control only: who each AI aircraft is (its model, to put the same one back) and where its AI
# is taking it. Asked every few seconds, and only with [traffic] control on.
TRAFFIC_IDENTITY: tuple[Datum, ...] = (
    Datum("title", "TITLE", None, S256),
    Datum("origin", "AI TRAFFIC FROMAIRPORT", None, S8),
    Datum("destination", "AI TRAFFIC TOAIRPORT", None, S8),
    Datum("state", "AI TRAFFIC STATE", None, S32),
)
# Its livery, MSFS 2024 only (a definition of its own: a sim without it loses only this).
TRAFFIC_LIVERY: tuple[Datum, ...] = (Datum("livery", "LIVERY NAME", None, S256),)

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
    """The values in ``payload``. One shorter than the definition (a sim that refused a variable at the end) reads
    the missing ones as zero."""
    size = payload_size(datums)
    if len(payload) < size:
        payload = payload + bytes(size - len(payload))
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
        fuel_lb=round(raw["fuel_lb"], 1),
        fuel_flow_pph=round(raw["fuel_flow_pph"], 1),
        gross_weight_lb=round(raw["gross_weight_lb"], 1),
        precip_rate_mm=round(raw.get("precip_rate_mm", 0.0), 2),
        in_smoke=bool(raw.get("in_smoke", 0)),
    )


RADIO_HEIGHT_TOP_FT = 2500.0


def systems_from_raw(raw: dict[str, Any], t: float, extra: dict[str, Any] | None = None,
                     more: dict[str, Any] | None = None) -> AircraftSystems:
    flags = ("spoilers_armed", "light_landing", "light_taxi", "light_strobe", "light_beacon", "light_nav", "light_logo",
             "ap_master", "ap_heading", "ap_nav", "ap_approach", "ap_altitude", "ap_vs", "ap_flc", "athr_armed", "battery")
    return AircraftSystems(
        t=t, **{f: bool(raw[f]) for f in flags},
        gear_pct=round(raw["gear_pct"], 1), flaps_pct=round(raw["flaps_pct"], 1),
        flaps_positions=int(raw["flaps_positions"]), spoilers_pct=round(raw["spoilers_pct"], 1),
        ap_heading_sel=round(raw["ap_heading_sel"] % 360, 1), ap_altitude_sel=round(raw["ap_altitude_sel"]),
        ap_speed_sel=round(raw["ap_speed_sel"]), ap_vs_sel=round(raw["ap_vs_sel"]),
        com1_standby_mhz=round_mhz(raw["com1_standby_mhz"]),
        # To 10 ft where callouts use it, and no higher than 2,500 ft: above that it changes with every hill and wave.
        radio_height_ft=float(round(min(raw["radio_height_ft"], RADIO_HEIGHT_TOP_FT), -1)),
        engines_running=sum(bool(raw[f"eng{i}"]) for i in range(1, 5)), mach=round(raw["mach"], 2),
        **_extra(extra or {}),
        **_more(more or {}),
    )


def _extra(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    return {
        "vs0_kt": float(round(raw["vs0_kt"])), "vs1_kt": float(round(raw["vs1_kt"])),
        "takeoff_kt": float(round(raw["takeoff_kt"])), "vmo_kt": float(round(raw["vmo_kt"])),
        "stall_warning": bool(raw["stall_warning"]), "overspeed_warning": bool(raw["overspeed_warning"]),
        "loc_received": bool(raw["loc_received"]), "gs_received": bool(raw["gs_received"]),
        # To 10 of the 127: enough for "alive", without a new message for every wobble of the needle.
        "loc_deviation": int(round(max(-127.0, min(127.0, raw["loc_deviation"])), -1)),
        "gs_deviation": int(round(max(-127.0, min(127.0, raw["gs_deviation"])), -1)),
    }


def _more(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    return {
        # To 0.05 g: turbulence shows in how it varies, and a level cruise doesn't send a new message every second.
        "g_force": round(raw["g_force"] * 20) / 20, "ice_pct": float(round(max(0.0, raw["ice_pct"]))),
        "anti_ice": bool(raw["eng_anti_ice"]) or bool(raw["wing_deice"]),
        "autobrake": int(raw["autobrake"]), "autobrake_active": bool(raw["autobrake_active"]),
        "reverser_pct": float(round(max(raw["rev1"], raw["rev2"], 0.0), -1)), "jet": int(raw["engine_type"]) == 1,
    }


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
