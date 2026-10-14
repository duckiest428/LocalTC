"""LocalTC's traffic on the ground where the live data has nothing: an arrival the receivers lost on final is landed,
rolled out, turned off at a taxiway ahead and taxied to a free stand its wings fit, along the airport's taxiways, the
way a crew would ("going down the runway painfully slow, not vacating, then it just disappeared").

``taxi_in_path`` works the way out; ``Follower`` moves along it a second at a time.
"""

import math
import random
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from localtc.atc_core.airport.aircraft_size import fits_stand
from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.airport.taxi_route import TaxiGraph

EXIT_MIN_M = 350.0  # the first exit taken is at least this far ahead of where it's slowed enough
EXIT_MAX_M = 3500.0
TAXI_KT = 15.0
TURN_KT = 8.0
STAND_KT = 4.0
TURN_DEG = 40.0  # a turn this sharp is taken slowly
NEAR_STAND_M = 80.0


@dataclass
class Follower:
    """Where one is along its path (lat/lon points), and how fast it's going."""

    points: list[tuple[float, float]]
    index: int = 0  # the point it's heading for
    gs_kt: float = 20.0
    exit_index: int = 1  # the point where it leaves the runway: until then it's still slowing on it
    done: bool = False
    spot: int | None = None  # the stand at the end
    headings: list[float] = field(default_factory=list)

    def step(self, lat: float, lon: float, dt: float = 1.0) -> tuple[float, float, float, float]:
        """A second on: (lat, lon, heading, speed)."""
        if self.index >= len(self.points):
            self.done = True
            return lat, lon, _bearing(self.points[-2], self.points[-1]) if len(self.points) > 1 else 0.0, 0.0
        target = self.points[self.index]
        ahead = _metres(lat, lon, *target)
        rest = ahead + sum(_metres(*a, *b) for a, b in zip(self.points[self.index:], self.points[self.index + 1:]))
        turn = self._turn_at(self.index)
        want = TAXI_KT
        if self.index <= self.exit_index:
            want = max(TURN_KT, self.gs_kt - 3.0)  # still braking on the runway, then the turn off
        elif turn > TURN_DEG and ahead < 60:
            want = TURN_KT
        if rest < NEAR_STAND_M:
            want = STAND_KT
        self.gs_kt += max(-3.0, min(1.0, want - self.gs_kt))
        move = self.gs_kt * 0.5144 * dt
        hdg = _bearing((lat, lon), target)
        while move >= ahead and self.index < len(self.points):
            move -= ahead
            lat, lon = target
            self.index += 1
            if self.index >= len(self.points):
                self.done = True
                return lat, lon, hdg, 0.0
            target = self.points[self.index]
            ahead = _metres(lat, lon, *target)
            hdg = _bearing((lat, lon), target)
        k = move / ahead if ahead > 0 else 1.0
        lat, lon = lat + (target[0] - lat) * k, lon + (target[1] - lon) * k
        return lat, lon, hdg, self.gs_kt

    def _turn_at(self, i: int) -> float:
        if i <= 0 or i + 1 >= len(self.points):
            return 0.0
        a, b = _bearing(self.points[i - 1], self.points[i]), _bearing(self.points[i], self.points[i + 1])
        return abs((b - a + 180) % 360 - 180)


def free_stand(geo: AirportGeometry, taken: Iterable[tuple[float, float]], span_m: float, near: list[tuple[float, float]],
               seed: str) -> int | None:
    """A free gate the wings fit, near ``near`` (the airline's own aircraft) when there are any, else any."""
    taken = list(taken)
    stands = []
    for p in geo.airport.parking:
        if not p.kind.startswith("gate") or not fits_stand(p.radius_m, span_m):
            continue
        xy = geo.xy(p.lat, p.lon)
        if any(math.dist(xy, geo.xy(a, b)) < max(p.radius_m, 15.0) for a, b in taken):
            continue
        stands.append(p)
    if not stands:
        return None
    if near:
        points = [geo.xy(a, b) for a, b in near]
        stands.sort(key=lambda p: min(math.dist(geo.xy(p.lat, p.lon), q) for q in points))
        stands = stands[:6]
    return random.Random(zlib.crc32(seed.encode())).choice(stands).index


def taxi_in_path(geo: AirportGeometry, graph: TaxiGraph, lat: float, lon: float, heading: float,
                 spot: int) -> tuple[list[tuple[float, float]], int] | None:
    """From an aircraft rolling out on a runway to parking spot ``spot``: down the runway to the first taxiway off it
    far enough ahead, off, and along the taxiways to the stand. The points (lat, lon) and the index of the one where
    it leaves the runway; None when there's no way there."""
    here = geo.xy(lat, lon)
    hx, hy = math.sin(math.radians(heading)), math.cos(math.radians(heading))
    exits = []
    for node, edges in graph.edges.items():
        if node[0] != "point" or not _on_runway(graph, node):
            continue
        nx, ny = graph.positions[node]
        along = (nx - here[0]) * hx + (ny - here[1]) * hy
        across = abs((nx - here[0]) * hy - (ny - here[1]) * hx)
        if not EXIT_MIN_M <= along <= EXIT_MAX_M or across > 60.0:
            continue
        for edge in edges:
            if edge.runway or _on_runway(graph, edge.to):
                continue
            exits.append((along, node, edge.to))
    for _, on, off in sorted(exits):
        (ax, ay), (bx, by) = graph.positions[on], graph.positions[off]
        out_heading = math.degrees(math.atan2(bx - ax, by - ay)) % 360
        route = graph.route(off, {("parking", spot)}, heading=out_heading)
        if route is None:
            continue
        xy = [graph.positions[on], *(graph.positions[n] for n in route.nodes)]
        points = [(lat, lon)] + [geo.frame.to_latlon(x, y) for x, y in xy]
        return points, 1
    return None


def _on_runway(graph: TaxiGraph, node) -> bool:
    return any(r.contains(graph.positions[node]) for r in graph.geometry.runways)


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111_320.0
    dx = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (b[0] - a[0]) * 111_320.0
    dx = (b[1] - a[1]) * 111_320.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.degrees(math.atan2(dx, dy)) % 360
