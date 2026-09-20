"""SimConnect Facilities API: the airport layout definition and assembly into ``sim_api.Airport``.

Replies arrive as one FACILITY_DATA message per item (the airport, then each
runway, frequency, taxi point, ...), then FACILITY_DATA_END. Items are
classified by payload size, which is unique per child definition below, and
the ``Type`` field is only a tie-breaker. That keeps parsing correct even if
the undocumented type enum or the header's bool width differ from what we
expect; ``AirportAssembler.report()`` shows what was actually received.
"""

import logging
import math
import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from localtc.sim_api import Airport, ApproachProcedure, Frequency, ParkingSpot, Runway, RunwayEnd, TaxiPath, TaxiPoint
from localtc.sim_api.airport import FEET_PER_METER
from localtc.sim_api.geo import METERS_PER_DEG_LAT, haversine_nm  # noqa: F401  (re-exported)
from localtc.sim_bridge.protocol import FacilityData

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class FacilityField:
    name: str
    code: str  # struct format code


@dataclass(frozen=True)
class FacilityItem:
    kind: str  # SimConnect name used in OPEN/CLOSE
    fields: tuple[FacilityField, ...]
    type_hint: int  # expected SIMCONNECT_FACILITY_DATA_TYPE value (unverified)

    @property
    def fmt(self) -> str:
        return "<" + "".join(f.code for f in self.fields)

    @property
    def size(self) -> int:
        return struct.calcsize(self.fmt)


def _fields(*spec: tuple[str, str]) -> tuple[FacilityField, ...]:
    return tuple(FacilityField(name, code) for name, code in spec)


AIRPORT = FacilityItem("AIRPORT", _fields(
    ("LATITUDE", "d"), ("LONGITUDE", "d"), ("ALTITUDE", "d"), ("MAGVAR", "f"),
    ("NAME64", "64s"), ("ICAO", "8s"), ("REGION", "8s"),
), 0)

RUNWAY = FacilityItem("RUNWAY", _fields(
    ("LATITUDE", "d"), ("LONGITUDE", "d"), ("ALTITUDE", "d"), ("HEADING", "f"), ("LENGTH", "f"), ("WIDTH", "f"),
    ("PATTERN_ALTITUDE", "f"), ("SURFACE", "i"), ("PRIMARY_NUMBER", "i"), ("PRIMARY_DESIGNATOR", "i"),
    ("SECONDARY_NUMBER", "i"), ("SECONDARY_DESIGNATOR", "i"), ("PRIMARY_ILS_ICAO", "8s"), ("SECONDARY_ILS_ICAO", "8s"),
), 1)

FREQUENCY = FacilityItem("FREQUENCY", _fields(("TYPE", "i"), ("FREQUENCY", "i"), ("NAME", "64s")), 3)

TAXI_POINT = FacilityItem("TAXI_POINT", _fields(("TYPE", "i"), ("ORIENTATION", "i"), ("BIAS_X", "f"), ("BIAS_Z", "f")), 14)

TAXI_PARKING = FacilityItem("TAXI_PARKING", _fields(
    ("TYPE", "i"), ("TAXI_POINT_TYPE", "i"), ("NAME", "i"), ("SUFFIX", "i"), ("NUMBER", "I"),
    ("HEADING", "f"), ("RADIUS", "f"), ("BIAS_X", "f"), ("BIAS_Z", "f"),
), 15)

TAXI_PATH = FacilityItem("TAXI_PATH", _fields(
    ("TYPE", "i"), ("WIDTH", "f"), ("RUNWAY_NUMBER", "i"), ("RUNWAY_DESIGNATOR", "i"),
    ("START", "i"), ("END", "i"), ("NAME_INDEX", "I"),
), 16)

TAXI_NAME = FacilityItem("TAXI_NAME", _fields(("NAME", "32s")), 17)

# Which instrument approaches the airport publishes, and to which runway. A subset of the SDK's APPROACH
# fields: enough to say "expect the ILS runway 16R" only where there is one.
APPROACH = FacilityItem("APPROACH", _fields(
    ("TYPE", "i"), ("SUFFIX", "i"), ("RUNWAY_NUMBER", "i"), ("RUNWAY_DESIGNATOR", "i"),
    ("FAF_ALTITUDE", "f"), ("MISSED_ALTITUDE", "f"),
), 4)

CHILDREN = (RUNWAY, FREQUENCY, TAXI_POINT, TAXI_PARKING, TAXI_PATH, TAXI_NAME, APPROACH)
ALL_ITEMS = (AIRPORT, *CHILDREN)
assert len({item.size for item in ALL_ITEMS}) == len(ALL_ITEMS), "facility item sizes must be unique"

FREQUENCY_KINDS = {
    0: "none", 1: "atis", 2: "multicom", 3: "unicom", 4: "ctaf", 5: "ground", 6: "tower", 7: "clearance",
    8: "approach", 9: "departure", 10: "center", 11: "fss", 12: "awos", 13: "asos", 14: "clearance_pretaxi",
    15: "remote_clearance",
}
TAXI_POINT_KINDS = {
    0: "none", 1: "normal", 2: "hold_short", 3: "ils_hold_short", 4: "hold_short_no_draw", 5: "ils_hold_short_no_draw",
}
TAXI_PATH_KINDS = {
    0: "none", 1: "taxi", 2: "runway", 3: "parking", 4: "path", 5: "closed", 6: "vehicle", 7: "road", 8: "painted_line",
}
PARKING_KINDS = {
    0: "none", 1: "ramp_ga", 2: "ramp_ga_small", 3: "ramp_ga_medium", 4: "ramp_ga_large", 5: "ramp_cargo",
    6: "ramp_mil_cargo", 7: "ramp_mil_combat", 8: "gate_small", 9: "gate_medium", 10: "gate_heavy", 11: "dock_ga",
    12: "fuel", 13: "vehicle", 14: "ramp_ga_extra", 15: "gate_extra",
}
PARKING_NAMES = {
    0: "", 1: "PARKING", 2: "N PARKING", 3: "NE PARKING", 4: "E PARKING", 5: "SE PARKING", 6: "S PARKING",
    7: "SW PARKING", 8: "W PARKING", 9: "NW PARKING", 10: "GATE", 11: "DOCK",
    **{12 + i: f"GATE {chr(ord('A') + i)}" for i in range(26)},
}
RUNWAY_DESIGNATORS = {0: "", 1: "L", 2: "R", 3: "C", 4: "W", 5: "A", 6: "B"}
APPROACH_KINDS = {
    0: "none", 1: "gps", 2: "vor", 3: "ndb", 4: "ils", 5: "localizer", 6: "sdf", 7: "lda", 8: "vordme", 9: "ndbdme",
    10: "rnav", 11: "backcourse",
}


def definition_lines() -> list[str]:
    """The strings passed to SimConnect_AddToFacilityDefinition, in order."""
    lines = ["OPEN AIRPORT", *(f.name for f in AIRPORT.fields)]
    for child in CHILDREN:
        lines += [f"OPEN {child.kind}", *(f.name for f in child.fields), f"CLOSE {child.kind}"]
    lines.append("CLOSE AIRPORT")
    return lines


def unpack(item: FacilityItem, payload: bytes) -> dict[str, Any]:
    values = struct.unpack_from(item.fmt, payload)
    return {
        f.name: (v.split(b"\0", 1)[0].decode("utf-8", errors="replace") if isinstance(v, bytes) else v)
        for f, v in zip(item.fields, values)
    }


def pack(item: FacilityItem, values: dict[str, Any]) -> bytes:
    """Inverse of ``unpack``; used by tests and the fake DLL."""
    return struct.pack(item.fmt, *(v.encode() if isinstance(v, str) else v for v in (values[f.name] for f in item.fields)))


@dataclass
class AirportAssembler:
    icao: str
    started_t: float = 0.0
    airport_raw: dict[str, Any] | None = None
    items: dict[str, dict[int, dict[str, Any]]] = field(default_factory=lambda: {c.kind: {} for c in CHILDREN})
    offsets: Counter = field(default_factory=Counter)
    type_ids: dict[str, set[int]] = field(default_factory=dict)
    unclassified: Counter = field(default_factory=Counter)  # (type, payload bytes) -> count

    def add(self, msg: FacilityData) -> None:
        match = self._classify(msg)
        if match is None:
            self.unclassified[(msg.type, len(msg.payload))] += 1
            return
        item, payload, offset = match
        self.offsets[offset] += 1
        self.type_ids.setdefault(item.kind, set()).add(msg.type)
        raw = unpack(item, payload)
        if item is AIRPORT:
            self.airport_raw = raw
        else:
            index = msg.item_index if offset == 40 else msg.item_index_bool8
            self.items[item.kind][index] = raw

    def _classify(self, msg: FacilityData) -> tuple[FacilityItem, bytes, int] | None:
        for payload, offset in ((msg.payload, 40), (msg.payload_bool8, 37)):
            candidates = [item for item in ALL_ITEMS if item.size == len(payload)]
            if candidates:
                best = next((c for c in candidates if c.type_hint == msg.type), candidates[0])
                return best, payload, offset
        return None

    def report(self) -> str:
        counts = ", ".join(f"{kind.lower()}={len(v)}" for kind, v in self.items.items())
        types = ", ".join(f"{k}:{sorted(v)}" for k, v in sorted(self.type_ids.items()))
        text = f"{self.icao}: airport={'yes' if self.airport_raw else 'no'} {counts}; data offsets {dict(self.offsets)}; type ids {types}"
        if self.unclassified:
            text += f"; UNCLASSIFIED (type, bytes): {dict(self.unclassified)}"
        return text

    def build(self) -> Airport | None:
        a = self.airport_raw
        if a is None:
            return None
        lat, lon = a["LATITUDE"], a["LONGITUDE"]

        def offset_latlon(bias_x: float, bias_z: float) -> tuple[float, float]:
            # BIAS_X is meters east, BIAS_Z meters north of the airport reference point.
            return (
                round(lat + bias_z / METERS_PER_DEG_LAT, 7),
                round(lon + bias_x / (METERS_PER_DEG_LAT * math.cos(math.radians(lat))), 7),
            )

        runways = tuple(_runway(r) for _, r in sorted(self.items["RUNWAY"].items()))
        frequencies = tuple(
            Frequency(kind=FREQUENCY_KINDS.get(f["TYPE"], "other"), mhz=round(f["FREQUENCY"] / 1e6, 3), name=f["NAME"])
            for _, f in sorted(self.items["FREQUENCY"].items())
        )
        points = []
        for index, p in sorted(self.items["TAXI_POINT"].items()):
            plat, plon = offset_latlon(p["BIAS_X"], p["BIAS_Z"])
            points.append(TaxiPoint(index=index, kind=TAXI_POINT_KINDS.get(p["TYPE"], "other"), lat=plat, lon=plon))
        parking = []
        for index, p in sorted(self.items["TAXI_PARKING"].items()):
            plat, plon = offset_latlon(p["BIAS_X"], p["BIAS_Z"])
            prefix = PARKING_NAMES.get(p["NAME"], "PARKING")
            parking.append(
                ParkingSpot(
                    index=index, name=f"{prefix} {p['NUMBER']}".strip(), kind=PARKING_KINDS.get(p["TYPE"], "other"),
                    lat=plat, lon=plon, heading_true=round(p["HEADING"], 1), radius_m=round(p["RADIUS"], 1),
                )
            )
        approaches = []
        for _, ap in sorted(self.items["APPROACH"].items()):
            kind = APPROACH_KINDS.get(ap["TYPE"], "other")
            if kind in ("none", "other"):
                continue
            number, designator = ap["RUNWAY_NUMBER"], RUNWAY_DESIGNATORS.get(ap["RUNWAY_DESIGNATOR"], "")
            suffix = chr(ap["SUFFIX"]) if 65 <= ap["SUFFIX"] <= 90 else ""
            approaches.append(ApproachProcedure(kind=kind, runway=f"{number:02d}{designator}" if number else "",
                                                suffix=suffix))
        names = {index: n["NAME"] for index, n in self.items["TAXI_NAME"].items()}
        paths = []
        for _, p in sorted(self.items["TAXI_PATH"].items()):
            kind = TAXI_PATH_KINDS.get(p["TYPE"], "other")
            runway = _runway_ident(p["RUNWAY_NUMBER"], p["RUNWAY_DESIGNATOR"]) if kind == "runway" else ""
            paths.append(
                TaxiPath(
                    kind=kind, start=p["START"], end=p["END"], name=names.get(p["NAME_INDEX"], "") if kind != "runway" else "",
                    runway=runway, width_m=round(p["WIDTH"], 1),
                )
            )
        return Airport(
            icao=a["ICAO"] or self.icao,
            name=a["NAME64"],
            region=a["REGION"],
            lat=lat,
            lon=lon,
            elev_ft=round(a["ALTITUDE"] * FEET_PER_METER, 1),
            # MSFS reports KPAE's 16°E as 344: 0-360, east negative. Store east-positive -180..180.
            magvar=round(-(((a["MAGVAR"] + 180) % 360) - 180), 1) + 0.0,
            runways=runways,
            frequencies=frequencies,
            approaches=tuple(approaches),
            taxi_points=tuple(points),
            taxi_paths=tuple(paths),
            parking=tuple(parking),
        )


def _runway_ident(number: int, designator: int) -> str:
    num = f"{number:02d}" if 1 <= number <= 36 else ""
    return num + RUNWAY_DESIGNATORS.get(designator, "")


def _runway(r: dict[str, Any]) -> Runway:
    def end(number_key: str, designator_key: str, ils_key: str) -> RunwayEnd:
        number = r[number_key] if 1 <= r[number_key] <= 36 else 0
        return RunwayEnd(number=number, designator=RUNWAY_DESIGNATORS.get(r[designator_key], ""), ils_ident=r[ils_key])

    return Runway(
        lat=r["LATITUDE"],
        lon=r["LONGITUDE"],
        elev_ft=round(r["ALTITUDE"] * FEET_PER_METER, 1),
        heading_true=round(r["HEADING"], 2),
        length_m=round(r["LENGTH"], 1),
        width_m=round(r["WIDTH"], 1),
        primary=end("PRIMARY_NUMBER", "PRIMARY_DESIGNATOR", "PRIMARY_ILS_ICAO"),
        secondary=end("SECONDARY_NUMBER", "SECONDARY_DESIGNATOR", "SECONDARY_ILS_ICAO"),
        surface=r["SURFACE"],
    )
