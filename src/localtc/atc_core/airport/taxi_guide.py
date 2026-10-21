"""Taxi guidance: arrows on the ground along the route ground gave, a stop bar where the aircraft holds.

Pure: the route as ground gave it (points in the airport's local metres, ``AirportGeometry.xy``), where the aircraft is
and which way it's pointing, and where it must stop (a runway it isn't cleared onto or across) go in; the marks to
show come out, each with a key of its own. A mark keeps its key and its place while the aircraft taxis towards it, so
the sim's objects are made once and taken away once passed, never slid along the ground: the arrows are spaced
``SPACING_M`` apart from the start of the route, shown from ``FIRST_M`` ahead of the aircraft for ``AHEAD_M``.
"""

import itertools
import math
import zlib
from dataclasses import dataclass

SPACING_M = 12.0  # between one arrow and the next
FIRST_M = 25.0  # the first arrow this far ahead of the aircraft's reference point (under the nose: not seen)
AHEAD_M = 320.0  # arrows this far ahead
ON_ROUTE_M = 80.0  # further off the route than this, the aircraft isn't following it: the arrows from its nearest point
STOP_BAR_HALF_M = 9.0  # a stop bar across the taxiway: this far either side of the centreline
STOP_BAR_STEP_M = 4.5
HOLD_MARGIN_M = 45.0  # the stop bar this far out from a runway's edge (a hold line for an airliner's runway)


@dataclass(frozen=True)
class Mark:
    key: str
    kind: str  # "arrow" or "stop"
    x: float
    y: float
    hdg: float  # true, the way the route goes


def length(path: list[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in itertools.pairwise(path))


def project(path: list[tuple[float, float]], p: tuple[float, float], heading: float | None = None) -> tuple[float, float]:
    """(metres along ``path`` to the point nearest ``p``, metres off it). With ``heading``: of two stretches as near
    (a route that doubles back), the one going the way the aircraft points."""
    best = (math.inf, 0.0)
    run = 0.0
    for (ax, ay), (bx, by) in itertools.pairwise(path):
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        u = 0.0 if seg == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (seg * seg)))
        off = math.dist(p, (ax + u * dx, ay + u * dy))
        if heading is not None and seg > 0:
            against = abs((math.degrees(math.atan2(dx, dy)) - heading + 540.0) % 360.0 - 180.0) > 100.0
            off += 25.0 if against else 0.0  # a stretch the other way only when it's clearly the nearer
        if off < best[0]:
            best = (off, run + u * seg)
        run += seg
    return best[1], best[0]


def point_at(path: list[tuple[float, float]], s: float) -> tuple[float, float, float]:
    """(x, y, heading) ``s`` metres along ``path``."""
    run = 0.0
    for (ax, ay), (bx, by) in itertools.pairwise(path):
        seg = math.dist((ax, ay), (bx, by))
        if seg > 0 and run + seg >= s:
            u = (s - run) / seg
            return ax + u * (bx - ax), ay + u * (by - ay), math.degrees(math.atan2(bx - ax, by - ay)) % 360.0
        run += seg
    (ax, ay), (bx, by) = path[-2], path[-1]
    return bx, by, math.degrees(math.atan2(bx - ax, by - ay)) % 360.0


def stops(path: list[tuple[float, float]], runways: list, step_m: float = 3.0) -> list[float]:
    """Where along ``path`` it must stop: ``HOLD_MARGIN_M`` short of each of ``runways`` (``RunwayGeometry``: ones it
    isn't cleared onto or across) where the route first reaches it."""
    total = length(path)
    out = []
    for runway in runways:
        s = 0.0
        while s <= total:
            x, y, _ = point_at(path, s)
            if runway.contains((x, y), HOLD_MARGIN_M):
                out.append(max(0.0, s - 1.0))
                break
            s += step_m
    return sorted(out)


def route_key(path: list[tuple[float, float]]) -> str:
    """The same for the same route: a new route's marks are new objects."""
    return f"{zlib.crc32(repr([(round(x), round(y)) for x, y in path]).encode()) & 0xFFFF:04x}"


def marks(path: list[tuple[float, float]], here: tuple[float, float], heading: float,
          stop_at: list[float] = ()) -> list[Mark]:
    """The arrows ahead of the aircraft along ``path`` (to its end, or the first of ``stop_at`` ahead), and a stop
    bar there when it's in sight."""
    if len(path) < 2:
        return []
    total = length(path)
    along, off = project(path, here, heading)
    start = along + (FIRST_M if off <= ON_ROUTE_M else 0.0)
    stop = min([s for s in stop_at if s > along - 5.0], default=None)
    end = min(total, along + AHEAD_M, stop - SPACING_M / 2 if stop is not None else total)
    route = route_key(path)
    out = []
    s = math.ceil(start / SPACING_M) * SPACING_M
    while s <= end:
        x, y, hdg = point_at(path, s)
        out.append(Mark(f"{route}:a{round(s)}", "arrow", x, y, hdg))
        s += SPACING_M
    if stop is not None and stop <= along + AHEAD_M:
        x, y, hdg = point_at(path, stop)
        ax, ay = math.cos(math.radians(hdg)), -math.sin(math.radians(hdg))  # across the way, to the right
        n = int(STOP_BAR_HALF_M // STOP_BAR_STEP_M)
        for i in range(-n, n + 1):
            out.append(Mark(f"{route}:s{round(stop)}:{i}", "stop", x + ax * i * STOP_BAR_STEP_M,
                            y + ay * i * STOP_BAR_STEP_M, hdg))
    return out
