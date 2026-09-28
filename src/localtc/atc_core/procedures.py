"""Flying the filed procedures, and when ATC lets them be flown instead of giving headings.

**Arrivals.** A controller vectors an arrival (FAA JO 7110.65 5-9-1) when nothing published takes it to the
final: no arrival procedure, one that ends in "expect radar vectors", or traffic to fit it in with. An
arrival flying a STAR whose path runs onto the final approach course (an RNAV STAR with a runway transition
that lines up with the ILS) is left to fly it, and cleared for the approach on the way (7110.65 4-8-1:
cleared for the approach from a published route that connects to it). A STAR that ends somewhere else,
beside the airport or on a downwind, is flown to its end, and the vectors start there.

- ``star_join``: the STAR fix where its path joins the runway's final, or None when it doesn't.
- ``progress``: how far the aircraft is from the path, and how much of it is left to a given fix.

**Departures.** The takeoff clearance says what to fly first (7110.65 3-9-9, 4-3-2). On an RNAV SID whose
path starts at the runway, a US tower says "RNAV to FACTS, runway 36R, cleared for takeoff"; on a SID flown
by radar vectors, or with none, the heading ("fly runway heading"). Elsewhere (ICAO, Canada) the SID itself
is the instruction: "runway 36R, cleared for takeoff".

- ``sid_first_fix``: the first fix of a SID that is a path from the runway (an RNAV SID), or None.
"""

import itertools
import math
from dataclasses import dataclass

from localtc.atc_core import vectors
from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport.geometry import RunwayEndGeometry
from localtc.atc_core.route import RouteFix

NM_M = 1852.0
JOIN_OUT_NM = (4.0, 30.0)  # a STAR fix this far out on the final ...
JOIN_SIDE_NM = 2.0  # ... and this close to its centreline is on it
JOIN_TRACK_DEG = 45.0  # the STAR leg into that fix has to be heading down the final, not across it
ON_PATH_NM = 6.0  # this close to the STAR's path, the aircraft is flying it
SID_PATH_NM = 15.0  # a SID whose first fix is this close to the field is a path from the runway (RNAV)


@dataclass(frozen=True)
class Progress:
    off_nm: float  # how far off the path the aircraft is
    left_nm: float  # the path still to fly to the fix asked about (0: passed it)


def star_join(geo: AirportGeometry, end: RunwayEndGeometry, fixes: tuple[RouteFix, ...]) -> RouteFix | None:
    """The first STAR fix on ``end``'s final approach course that the STAR arrives at along the final."""
    for before, fix in itertools.pairwise(fixes):
        out, side = vectors.frame(geo, end, fix.lat, fix.lon)
        if not (JOIN_OUT_NM[0] <= out <= JOIN_OUT_NM[1] and abs(side) <= JOIN_SIDE_NM):
            continue
        was_out, was_side = vectors.frame(geo, end, before.lat, before.lon)
        inbound, across = was_out - out, abs(side - was_side)
        if inbound > 0 and math.degrees(math.atan2(across, inbound)) <= JOIN_TRACK_DEG:
            return fix
    return None


def progress(geo: AirportGeometry, fixes: tuple[RouteFix, ...], lat: float, lon: float, to: RouteFix) -> Progress | None:
    """Where the aircraft is along the path through ``fixes``: its distance off it, and the distance along it
    still to fly to ``to`` (one of the fixes)."""
    if len(fixes) < 2 or to not in fixes:
        return None
    points = [geo.xy(f.lat, f.lon) for f in fixes]
    here = geo.xy(lat, lon)
    best: tuple[float, int, float] | None = None  # (off, leg, fraction along it)
    for i, (a, b) in enumerate(itertools.pairwise(points)):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        f = 0.0 if length2 == 0 else max(0.0, min(1.0, ((here[0] - a[0]) * dx + (here[1] - a[1]) * dy) / length2))
        off = math.dist(here, (a[0] + f * dx, a[1] + f * dy))
        if best is None or off < best[0]:
            best = (off, i, f)
    assert best is not None
    off, leg, f = best
    target = fixes.index(to)
    if leg >= target:
        return Progress(off / NM_M, 0.0)
    left = (1 - f) * math.dist(points[leg], points[leg + 1]) + sum(
        math.dist(points[i], points[i + 1]) for i in range(leg + 1, target))
    return Progress(off / NM_M, left / NM_M)


def sid_first_fix(geo: AirportGeometry | None, fixes: tuple[RouteFix, ...]) -> RouteFix | None:
    """The first fix of a SID flown as a path from the runway (an RNAV SID). A radar vector SID in a plan
    lists only the fixes far out where the vectors end: None."""
    if geo is None or not fixes:
        return None
    first = fixes[0]
    return first if geo.distance_nm(first.lat, first.lon) <= SID_PATH_NM else None
