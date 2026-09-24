"""The filed route as ATC uses it: where the climb ends, where the descent begins, and when.

SimBrief's navlog names both points (``TOC``, ``TOD``) and gives each fix its planned time from
takeoff, so with a plan ATC can say "expect FL350 one eight minutes after departure" and mean it,
and can clear the descent before the aircraft needs it. Without a plan the same questions are
answered from the cruise altitude alone, with the rules of thumb a controller would use.
"""

import math
from dataclasses import dataclass

from localtc.sim_api.geo import haversine_nm


@dataclass(frozen=True)
class RouteFix:
    ident: str
    lat: float
    lon: float
    alt_ft: int = 0
    stage: str = ""  # CLB, CRZ, DSC
    time_s: int = 0  # planned seconds from takeoff; 0 when the plan doesn't say


# Without a plan: average climb rates by where the cruise is (jets up high, turboprops and pistons lower).
CLIMB_FPM = ((24000, 1800.0), (12000, 1000.0), (0, 600.0))
TOD_NM_PER_1000FT = 3.0  # the descent rule of thumb
MINUTES_RANGE = (5, 30)  # what an "expect ... minutes after departure" can sensibly say


class Route:
    def __init__(self, fixes: tuple[RouteFix, ...] = ()) -> None:
        self.fixes = fixes

    def __bool__(self) -> bool:
        return bool(self.fixes)

    @property
    def top_of_climb(self) -> RouteFix | None:
        named = next((f for f in self.fixes if f.ident.upper() == "TOC"), None)
        return named or next((f for f in reversed(self.fixes) if f.stage == "CLB"), None)

    @property
    def top_of_descent(self) -> RouteFix | None:
        named = next((f for f in self.fixes if f.ident.upper() == "TOD"), None)
        if named is not None:
            return named
        first_down = next((i for i, f in enumerate(self.fixes) if f.stage == "DSC"), None)
        return self.fixes[first_down - 1] if first_down else None

    @property
    def arrival_floor_ft(self) -> int | None:
        """The last altitude the plan descends to before the airport: where "descend via" ends."""
        # Not the airport itself, which a SimBrief plan lists last at pattern altitude: the arrival's last fix.
        descent = [f.alt_ft for f in self.fixes if f.stage == "DSC" and f.alt_ft > 0
                   and not (len(f.ident) == 4 and f.ident.isalpha())]
        return int(math.ceil(descent[-1] / 1000.0) * 1000) if descent else None

    def minutes_to_cruise(self, cruise_ft: int) -> int:
        """Minutes from takeoff to the top of the climb: the plan's own time, else its distance at a climb
        groundspeed, else the cruise altitude at an average climb rate."""
        toc = self.top_of_climb
        if toc is not None and toc.time_s > 0:
            minutes = toc.time_s / 60
        elif toc is not None and len(self.fixes) > 1:
            minutes = self._along(0, self.fixes.index(toc)) / climb_groundspeed(cruise_ft) * 60
        else:
            rate = next(fpm for floor, fpm in CLIMB_FPM if cruise_ft >= floor)
            minutes = cruise_ft / rate
        return int(min(max(round(minutes), MINUTES_RANGE[0]), MINUTES_RANGE[1]))

    def descent_distance_nm(self, dest_lat: float, dest_lon: float, cruise_ft: int, elev_ft: float) -> float:
        """How far from the destination the descent starts: the plan's top of descent, else 3 nm a thousand."""
        tod = self.top_of_descent
        if tod is not None:
            return haversine_nm(tod.lat, tod.lon, dest_lat, dest_lon)
        return max(30.0, (cruise_ft - elev_ft) / 1000 * TOD_NM_PER_1000FT)

    def _along(self, first: int, last: int) -> float:
        return sum(haversine_nm(a.lat, a.lon, b.lat, b.lon) for a, b in zip(self.fixes[first:last], self.fixes[first + 1:last + 1]))


def climb_groundspeed(cruise_ft: int) -> float:
    """A typical average groundspeed over the whole climb, knots."""
    return 330.0 if cruise_ft >= 24000 else 200.0 if cruise_ft >= 12000 else 110.0
