"""Airport layout data: runways, frequencies and the taxiway graph.

Produced by the live bridge (SimConnect Facilities API), carried in
``AirportData`` events so recordings replay with the airport they were flown
at, and cached as JSON per ICAO.
"""

import msgspec

FEET_PER_METER = 3.28084


class RunwayEnd(msgspec.Struct, frozen=True, kw_only=True):
    number: int  # 1-36; 0 when the end has no number
    designator: str = ""  # "", "L", "R", "C", "W"
    ils_ident: str = ""

    @property
    def ident(self) -> str:
        return f"{self.number:02d}{self.designator}" if self.number else self.designator


class Runway(msgspec.Struct, frozen=True, kw_only=True):
    lat: float  # center
    lon: float
    elev_ft: float
    heading_true: float  # of the primary end
    length_m: float
    width_m: float
    primary: RunwayEnd
    secondary: RunwayEnd
    surface: int = 0

    @property
    def idents(self) -> tuple[str, str]:
        return self.primary.ident, self.secondary.ident

    @property
    def name(self) -> str:
        return f"{self.primary.ident}/{self.secondary.ident}"


class Frequency(msgspec.Struct, frozen=True, kw_only=True):
    # atis, multicom, unicom, ctaf, ground, tower, clearance, approach, departure,
    # center, fss, awos, asos, clearance_pretaxi, remote_clearance, other
    kind: str
    mhz: float
    name: str = ""


class TaxiPoint(msgspec.Struct, frozen=True, kw_only=True):
    index: int
    # normal, hold_short, ils_hold_short, hold_short_no_draw, ils_hold_short_no_draw, other
    kind: str
    lat: float
    lon: float

    @property
    def is_hold_short(self) -> bool:
        return "hold_short" in self.kind


class TaxiPath(msgspec.Struct, frozen=True, kw_only=True):
    kind: str  # taxi, runway, parking, path, closed, vehicle, road, other
    start: int  # taxi point index
    end: int  # taxi point index, or parking index when kind == "parking"
    name: str = ""  # taxiway name, e.g. "A3"
    runway: str = ""  # runway ident for kind == "runway"
    width_m: float = 0.0


class ParkingSpot(msgspec.Struct, frozen=True, kw_only=True):
    index: int
    name: str  # e.g. "GATE A 12", "PARKING 3"
    kind: str  # ramp_ga, gate_small, ...
    lat: float
    lon: float
    heading_true: float = 0.0
    radius_m: float = 0.0


class Airport(msgspec.Struct, frozen=True, kw_only=True):
    icao: str
    name: str = ""
    region: str = ""
    lat: float
    lon: float
    elev_ft: float
    magvar: float = 0.0  # degrees, east positive
    runways: tuple[Runway, ...] = ()
    frequencies: tuple[Frequency, ...] = ()
    taxi_points: tuple[TaxiPoint, ...] = ()
    taxi_paths: tuple[TaxiPath, ...] = ()
    parking: tuple[ParkingSpot, ...] = ()

    def frequencies_of(self, *kinds: str) -> tuple[Frequency, ...]:
        return tuple(f for f in self.frequencies if f.kind in kinds)
