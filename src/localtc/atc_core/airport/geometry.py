"""Airport geometry in local meters: runway polygons, thresholds, hold-short points, final approach alignment."""

import math
from dataclasses import dataclass
from functools import cached_property

from localtc.sim_api import Airport, Runway, TaxiPoint
from localtc.sim_api.geo import METERS_PER_NM, LocalFrame, angle_diff, haversine_nm, unit


@dataclass(frozen=True)
class RunwayEndGeometry:
    """One usable direction of a runway, e.g. 34L: departures roll along ``heading_true`` from the threshold."""

    ident: str
    heading_true: float
    threshold: tuple[float, float]  # local east/north meters
    ils_ident: str
    runway: "RunwayGeometry"

    @property
    def has_ils(self) -> bool:
        return bool(self.ils_ident)


class RunwayGeometry:
    def __init__(self, runway: Runway, frame: LocalFrame) -> None:
        self.runway = runway
        self.center = frame.to_xy(runway.lat, runway.lon)
        self.half_length = runway.length_m / 2
        self.half_width = runway.width_m / 2
        # Runway.heading_true is the primary end's direction.
        primary_heading = runway.heading_true % 360
        self.ends = (
            self._end(runway.primary.ident, primary_heading, runway.primary.ils_ident),
            self._end(runway.secondary.ident, (primary_heading + 180) % 360, runway.secondary.ils_ident),
        )

    def _end(self, ident: str, heading: float, ils: str) -> RunwayEndGeometry:
        ux, uy = unit(heading)
        threshold = (self.center[0] - ux * self.half_length, self.center[1] - uy * self.half_length)
        return RunwayEndGeometry(ident=ident, heading_true=heading, threshold=threshold, ils_ident=ils, runway=self)

    @property
    def name(self) -> str:
        return self.runway.name

    def along_across(self, xy: tuple[float, float]) -> tuple[float, float]:
        """Meters along the primary heading from the center, and across (right positive)."""
        ux, uy = unit(self.ends[0].heading_true)
        dx, dy = xy[0] - self.center[0], xy[1] - self.center[1]
        return dx * ux + dy * uy, dx * uy - dy * ux

    def contains(self, xy: tuple[float, float], margin_m: float = 0.0) -> bool:
        along, across = self.along_across(xy)
        return abs(along) <= self.half_length + margin_m and abs(across) <= self.half_width + margin_m

    def distance_to(self, xy: tuple[float, float]) -> float:
        """Meters from the runway rectangle (0 inside)."""
        along, across = self.along_across(xy)
        da = max(0.0, abs(along) - self.half_length)
        dc = max(0.0, abs(across) - self.half_width)
        return math.hypot(da, dc)

    def end_for_heading(self, heading_true: float, tolerance: float = 20.0) -> RunwayEndGeometry | None:
        best = min(self.ends, key=lambda e: angle_diff(e.heading_true, heading_true))
        return best if angle_diff(best.heading_true, heading_true) <= tolerance else None


@dataclass(frozen=True)
class HoldShort:
    point: TaxiPoint
    xy: tuple[float, float]
    runway: RunwayGeometry
    end: RunwayEndGeometry  # the runway end this hold-short point is closest to


@dataclass(frozen=True)
class FinalApproach:
    end: RunwayEndGeometry
    distance_nm: float  # along the extended centerline to the threshold
    lateral_m: float


class AirportGeometry:
    def __init__(self, airport: Airport) -> None:
        self.airport = airport
        self.frame = LocalFrame(airport.lat, airport.lon)
        self.runways = tuple(RunwayGeometry(r, self.frame) for r in airport.runways)
        self.hold_taxiways: dict[str, str] = {}  # runway end -> the taxiway its holding point is on (airport_fixes.toml)

    @property
    def icao(self) -> str:
        return self.airport.icao

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        return self.frame.to_xy(lat, lon)

    def distance_nm(self, lat: float, lon: float) -> float:
        return haversine_nm(lat, lon, self.airport.lat, self.airport.lon)

    @cached_property
    def ends(self) -> tuple[RunwayEndGeometry, ...]:
        return tuple(end for rwy in self.runways for end in rwy.ends)

    def end(self, ident: str) -> RunwayEndGeometry | None:
        return next((e for e in self.ends if e.ident == ident), None)

    @cached_property
    def hold_shorts(self) -> tuple[HoldShort, ...]:
        result = []
        for point in self.airport.taxi_points:
            if not point.is_hold_short or not self.runways:
                continue
            xy = self.xy(point.lat, point.lon)
            runway = min(self.runways, key=lambda r: r.distance_to(xy))
            end = min(runway.ends, key=lambda e: math.dist(e.threshold, xy))
            result.append(HoldShort(point=point, xy=xy, runway=runway, end=end))
        return tuple(result)

    def runway_at(self, lat: float, lon: float, margin_m: float = 5.0) -> RunwayGeometry | None:
        xy = self.xy(lat, lon)
        return next((r for r in self.runways if r.contains(xy, margin_m)), None)

    def nearest_hold_short(self, lat: float, lon: float) -> tuple[HoldShort, float] | None:
        if not self.hold_shorts:
            return None
        xy = self.xy(lat, lon)
        best = min(self.hold_shorts, key=lambda h: math.dist(h.xy, xy))
        return best, math.dist(best.xy, xy)

    def final_approach(
        self,
        lat: float,
        lon: float,
        heading_true: float,
        *,
        max_distance_nm: float = 15.0,
        max_lateral_m: float = METERS_PER_NM,
        heading_tolerance: float = 30.0,
    ) -> FinalApproach | None:
        """The runway end the aircraft is lined up to land on, if any."""
        xy = self.xy(lat, lon)
        best: FinalApproach | None = None
        for end in self.ends:
            if angle_diff(end.heading_true, heading_true) > heading_tolerance:
                continue
            ux, uy = unit(end.heading_true)
            dx, dy = xy[0] - end.threshold[0], xy[1] - end.threshold[1]
            before = -(dx * ux + dy * uy)  # meters before the threshold, along the approach
            lateral = dx * uy - dy * ux
            distance_nm = before / METERS_PER_NM
            if abs(lateral) > max_lateral_m or not (0.0 <= distance_nm <= max_distance_nm):
                continue
            candidate = FinalApproach(end=end, distance_nm=distance_nm, lateral_m=lateral)
            if best is None or abs(candidate.lateral_m) < abs(best.lateral_m):
                best = candidate
        return best


def select_runway(
    airport: AirportGeometry, wind_dir_true: float, wind_kt: float, *, calm_kt: float = 5.0
) -> RunwayEndGeometry | None:
    """Best runway end for the wind: most headwind, then ILS-equipped, then longest.

    Below ``calm_kt`` wind direction is ignored (calm-wind runway: ILS, then longest).
    """
    if not airport.ends:
        return None

    def score(end: RunwayEndGeometry) -> tuple[float, int, float]:
        headwind = 0.0 if wind_kt < calm_kt else wind_kt * math.cos(math.radians(wind_dir_true - end.heading_true))
        return round(headwind), int(end.has_ils), end.runway.runway.length_m

    return max(airport.ends, key=score)
