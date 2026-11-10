"""The ATC's terrain: the highest ground around a point from the ``TerrainGrid`` events (the app's terrain tiles,
recorded with the flight so a replay has the same ground), and the minimum altitudes it gives.

- **The minimum vectoring altitude** at a point: the highest ground within 3 miles (5 beyond 40 miles of the
  airport) plus 1,000 ft, 2,000 ft over mountains (ground of 3,000 ft or more under it), rounded up to the hundred.
- **A corridor**: the highest minimum along a heading for the miles ahead, 3 miles either side.
- **The minimum sector altitude** around an airport: the highest ground within 25 miles plus 1,000 ft (2,000 over
  mountains), rounded up to the hundred.

What isn't known (no tiles for it here) is None: ATC then works to its usual minimums, never to a guess.
"""

import math
from dataclasses import dataclass

from localtc.sim_api import TerrainGrid

CLEARANCE_FT = 1000
MOUNTAIN_CLEARANCE_FT = 2000
MOUNTAIN_FT = 3000  # ground this high counts as mountainous (the 2,000 ft margin)
MVA_NEAR_NM = 3.0
MVA_FAR_NM = 5.0
MVA_NEAR_AIRPORT_NM = 40.0
MSA_NM = 25.0
CORRIDOR_WIDTH_NM = 3.0
CORRIDOR_STEP_NM = 3.0
MAX_GRIDS = 12
ENROUTE_FAA_NM = 3.5  # 4 statute miles
ENROUTE_ICAO_NM = 4.3  # 8 km
MOUNTAIN_LOOK_NM = 25.0
HEADING_STEP_DEG = 10


@dataclass(frozen=True)
class Grid:
    lat0: float
    lon0: float
    dlat: float
    dlon: float
    rows: int
    cols: int
    ft: tuple[int, ...]

    def covers(self, lat: float, lon: float) -> bool:
        return self.lat0 <= lat <= self.lat0 + self.rows * self.dlat and self.lon0 <= lon <= self.lon0 + self.cols * self.dlon

    def highest(self, lat: float, lon: float, radius_nm: float) -> tuple[int | None, bool]:
        """(The highest known ground within ``radius_nm``, whether all of the circle is in this grid and known)."""
        rlat = radius_nm / 60.0
        rlon = radius_nm / 60.0 / max(0.1, math.cos(math.radians(lat)))
        r0, r1 = math.floor((lat - rlat - self.lat0) / self.dlat), math.floor((lat + rlat - self.lat0) / self.dlat)
        c0, c1 = math.floor((lon - rlon - self.lon0) / self.dlon), math.floor((lon + rlon - self.lon0) / self.dlon)
        whole = r0 >= 0 and c0 >= 0 and r1 < self.rows and c1 < self.cols
        best = None
        for r in range(max(0, r0), min(self.rows - 1, r1) + 1):
            for c in range(max(0, c0), min(self.cols - 1, c1) + 1):
                # The cell's nearest point to the centre within the radius: the cell counts.
                clat = min(max(lat, self.lat0 + r * self.dlat), self.lat0 + (r + 1) * self.dlat)
                clon = min(max(lon, self.lon0 + c * self.dlon), self.lon0 + (c + 1) * self.dlon)
                if _nm(lat, lon, clat, clon) > radius_nm:
                    continue
                v = self.ft[r * self.cols + c]
                if v < 0:
                    whole = False
                    continue
                best = v if best is None else max(best, v)
        return best, whole


class TerrainModel:
    """The grids the flight has had, newest first."""

    def __init__(self) -> None:
        self.grids: list[Grid] = []

    def add(self, ev: TerrainGrid) -> None:
        grid = Grid(ev.lat0, ev.lon0, ev.dlat, ev.dlon, ev.rows, ev.cols, tuple(ev.ft))
        self.grids = [grid, *[g for g in self.grids if (g.lat0, g.lon0) != (grid.lat0, grid.lon0)]][:MAX_GRIDS]

    def __bool__(self) -> bool:
        return bool(self.grids)

    def highest_ft(self, lat: float, lon: float, radius_nm: float) -> int | None:
        """The highest ground within ``radius_nm`` (None: not all of it is known)."""
        best, whole = None, False
        for grid in self.grids:
            if not grid.covers(lat, lon):
                continue
            top, complete = grid.highest(lat, lon, radius_nm)
            if top is not None:
                best = top if best is None else max(best, top)
            whole = whole or complete
        return best if whole else None

    def minimum_ft(self, lat: float, lon: float, radius_nm: float) -> int | None:
        """The minimum altitude over the ground within ``radius_nm``: 1,000 ft above it (2,000 over mountains)."""
        top = self.highest_ft(lat, lon, radius_nm)
        if top is None:
            return None
        return minimum_over(top)

    def mva_ft(self, lat: float, lon: float, airport_nm: float | None = None) -> int | None:
        radius = MVA_FAR_NM if airport_nm is not None and airport_nm > MVA_NEAR_AIRPORT_NM else MVA_NEAR_NM
        return self.minimum_ft(lat, lon, radius)

    def msa_ft(self, lat: float, lon: float) -> int | None:
        return self.minimum_ft(lat, lon, MSA_NM)

    def corridor_ft(self, lat: float, lon: float, heading_true: float, miles: float) -> int | None:
        """The highest minimum along ``heading_true`` for ``miles`` (3 miles either side); None: not all known."""
        worst = None
        steps = max(1, int(math.ceil(miles / CORRIDOR_STEP_NM)))
        for i in range(steps + 1):
            d = min(miles, i * CORRIDOR_STEP_NM)
            plat = lat + d / 60.0 * math.cos(math.radians(heading_true))
            plon = lon + d / 60.0 * math.sin(math.radians(heading_true)) / max(0.1, math.cos(math.radians(lat)))
            here = self.minimum_ft(plat, plon, CORRIDOR_WIDTH_NM)
            if here is None:
                return None
            worst = here if worst is None else max(worst, here)
        return worst


    def sample_ft(self, lat: float, lon: float) -> int | None:
        """The ground's height at a point (the highest in its cell); None: not known."""
        return self.highest_ft(lat, lon, 0.1)

    def clearance_ft(self, lat: float, lon: float, altitude_ft: float) -> float | None:
        """How far above the ground an altitude is here."""
        ground = self.sample_ft(lat, lon)
        return None if ground is None else altitude_ft - ground

    def route_corridor_ft(self, points: list[tuple[float, float]]) -> int | None:
        """The highest minimum along a route's legs (each point (lat, lon)), 3 miles either side; None: not all known."""
        worst = None
        for (a_lat, a_lon), (b_lat, b_lon) in zip(points, points[1:], strict=False):
            miles = _nm(a_lat, a_lon, b_lat, b_lon)
            steps = max(1, int(math.ceil(miles / CORRIDOR_STEP_NM)))
            for i in range(steps + 1):
                f = i / steps
                here = self.minimum_ft(a_lat + (b_lat - a_lat) * f, a_lon + (b_lon - a_lon) * f, CORRIDOR_WIDTH_NM)
                if here is None:
                    return None
                worst = here if worst is None else max(worst, here)
        return worst

    def minimum_safe_ft(self, lat: float, lon: float, *, phase: str = "terminal", icao: bool = False,
                        airport_nm: float | None = None) -> int | None:
        """The lowest safe altitude here, by phase and region: in the terminal area the minimum vectoring altitude; en
        route, 1,000 ft over the ground (2,000 over mountains) within 4 statute miles (the FAA) or 8 km (ICAO)."""
        if phase == "enroute":
            return self.minimum_ft(lat, lon, ENROUTE_ICAO_NM if icao else ENROUTE_FAA_NM)
        return self.mva_ft(lat, lon, airport_nm)

    def mountainous(self, lat: float, lon: float) -> bool:
        """Ground of 3,000 ft or more around here: the 2,000 ft margin applies, and it's worth saying so."""
        high = self.highest_ft(lat, lon, MOUNTAIN_LOOK_NM)
        return high is not None and high >= MOUNTAIN_FT

    def safe_heading(self, lat: float, lon: float, wanted_true: float, altitude_ft: float, miles: float = 15.0) -> float | None:
        """The heading nearest ``wanted_true`` with a clear corridor at ``altitude_ft``; None when none within 90 degrees is.
        Ground that isn't known isn't held against a heading (``wanted_true`` itself, then)."""
        for off in range(0, 91, HEADING_STEP_DEG):
            for side in (1, -1) if off else (1,):
                heading = (wanted_true + side * off) % 360.0
                floor = self.corridor_ft(lat, lon, heading, miles)
                if floor is None or altitude_ft >= floor:
                    return heading
        return None


def minimum_over(highest_ft: float) -> int:
    margin = MOUNTAIN_CLEARANCE_FT if highest_ft >= MOUNTAIN_FT else CLEARANCE_FT
    return int(math.ceil((highest_ft + margin) / 100.0)) * 100


def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 60.0
    dx = (lon2 - lon1) * 60.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)
