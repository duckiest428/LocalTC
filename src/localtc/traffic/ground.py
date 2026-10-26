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
TURN_DEG = 35.0  # turning this much over the next ``TURN_LOOK_M``: taken slowly
TURN_LOOK_M = 60.0
NEAR_STAND_M = 80.0
# Each corner of the taxiways' path is flown as a curve of about this radius (a nosewheel's, at taxi speed), not a
# turn on the spot at the node: "airplanes on the ground turn quite unnaturally"
TURN_RADIUS_M = 35.0
ARC_STEP_DEG = 10.0
ACCEL_KT_S, BRAKE_KT_S = 0.8, 2.0  # an airliner on the taxiway: gently up to speed, firmly but not abruptly down
# Turning: no faster than the turn's radius allows (v = sqrt(a * r), a sideways pull a passenger barely notices), and
# slowed down for it in good time ahead (v^2 = v_turn^2 + 2 * a * d). One speed through every corner, and a corner
# taken at full taxi speed, wasn't how an airliner moves. Looked at over the next ``LOOK_AHEAD_M`` only: a few points
# a second per aircraft.
LATERAL_MS2 = 1.2
TAXI_DECEL_MS2 = 0.7
LOOK_AHEAD_M = 150.0
MIN_TURN_KT = 5.0
MAX_YAW_DEG_S = 12.0  # the nose swings round no faster than this (a nosewheel's steering)
# Rolling out: braked to reach the exit at this speed, not down to a crawl at once and then 2 km of runway at 8 knots
# (a 777 that landed the other way sat on Dubai's runway two minutes, with the user on final)
EXIT_KT = 18.0
ROLLOUT_DECEL_MS2 = 1.2


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
    smooth: bool = True  # its corners rounded (``rounded``); a push back isn't
    last_hdg: float | None = None  # the way its nose pointed a second ago

    def __post_init__(self) -> None:
        if self.smooth and len(self.points) > 2:
            keep = max(self.exit_index, 0) + 1  # up to where it leaves the runway, as it was given
            self.points = self.points[:keep] + rounded(self.points[keep - 1:], TURN_RADIUS_M)[1:]

    def step(self, lat: float, lon: float, dt: float = 1.0) -> tuple[float, float, float, float]:
        """A second on: (lat, lon, heading, speed)."""
        if self.index >= len(self.points):
            self.done = True
            return lat, lon, _bearing(self.points[-2], self.points[-1]) if len(self.points) > 1 else 0.0, 0.0
        target = self.points[self.index]
        ahead = _metres(lat, lon, *target)
        rest = ahead + sum(_metres(*a, *b) for a, b in zip(self.points[self.index:], self.points[self.index + 1:]))
        want = TAXI_KT
        if self.index <= self.exit_index:
            # Still on the runway: braking to make the exit at its speed (v^2 = v_exit^2 + 2ad), then the turn off
            to_exit = ahead + sum(_metres(*a, *b) for a, b in zip(self.points[self.index:self.exit_index],
                                                                  self.points[self.index + 1:self.exit_index + 1]))
            fits = math.sqrt((EXIT_KT * 0.5144) ** 2 + 2 * ROLLOUT_DECEL_MS2 * to_exit) / 0.5144
            want = max(TURN_KT, min(self.gs_kt, fits))
        else:
            want = min(want, self._turn_limit_kt((lat, lon)))
        if rest < NEAR_STAND_M * 3:  # easing in to the end of the line: slowing all the way, not a crawl from 80 m
            want = min(want, math.sqrt((STAND_KT * 0.5144) ** 2 + 2 * 0.4 * max(rest - 10.0, 0.0)) / 0.5144)
        braking = 3.0 if self.index <= self.exit_index else BRAKE_KT_S
        self.gs_kt += max(-braking * dt, min(ACCEL_KT_S * dt, want - self.gs_kt))
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
        if self.last_hdg is not None and self.gs_kt > 0:  # the nose swings round, it doesn't snap to the next leg
            turn = (hdg - self.last_hdg + 180) % 360 - 180
            hdg = (self.last_hdg + max(-MAX_YAW_DEG_S * dt, min(MAX_YAW_DEG_S * dt, turn))) % 360
        self.last_hdg = hdg
        return lat, lon, hdg, self.gs_kt

    def _turn_limit_kt(self, here: tuple[float, float]) -> float:
        """The fastest it can go now and still make every turn in the next ``LOOK_AHEAD_M`` at the speed its radius
        allows, braking at ``TAXI_DECEL_MS2``."""
        pts = [here, *self.points[self.index:]]
        best = TAXI_KT * 0.5144
        gone = 0.0
        for i in range(1, len(pts) - 1):
            leg_in, leg_out = _metres(*pts[i - 1], *pts[i]), _metres(*pts[i], *pts[i + 1])
            gone += leg_in
            if gone > LOOK_AHEAD_M:
                break
            if leg_in < 0.5 or leg_out < 0.5:
                continue
            angle = math.radians(abs((_bearing(pts[i], pts[i + 1]) - _bearing(pts[i - 1], pts[i]) + 180) % 360 - 180))
            if angle < math.radians(3):
                continue
            radius = min(leg_in, leg_out) / angle  # a corner of the rounded path: its arc's radius
            v_turn = max(MIN_TURN_KT * 0.5144, math.sqrt(LATERAL_MS2 * radius))
            best = min(best, math.sqrt(v_turn ** 2 + 2 * TAXI_DECEL_MS2 * max(gone - leg_in, 0.0)))
        return best / 0.5144

    def _turning_ahead(self, here: tuple[float, float]) -> float:
        """How much the path turns in the next ``TURN_LOOK_M``, degrees."""
        total, gone, prev = 0.0, 0.0, None
        a = here
        for b in self.points[self.index:]:
            leg = _metres(*a, *b)
            if leg > 0.5:
                hdg = _bearing(a, b)
                if prev is not None:
                    total += abs((hdg - prev + 180) % 360 - 180)
                prev = hdg
            gone += leg
            if gone > TURN_LOOK_M:
                break
            a = b
        return total

    def _turn_at(self, i: int) -> float:
        if i <= 0 or i + 1 >= len(self.points):
            return 0.0
        a, b = _bearing(self.points[i - 1], self.points[i]), _bearing(self.points[i], self.points[i + 1])
        return abs((b - a + 180) % 360 - 180)


def rounded(points: list[tuple[float, float]], radius_m: float) -> list[tuple[float, float]]:
    """The path with each corner turned as a curve of ``radius_m`` (less where the legs either side are too short for
    it), a point every ``ARC_STEP_DEG`` of the turn. The ends stay where they are."""
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        a, b, c = out[-1], points[i], points[i + 1]
        h1, h2 = _bearing(a, b), _bearing(b, c)
        turn = (h2 - h1 + 180) % 360 - 180
        la, lc = _metres(*a, *b), _metres(*b, *c)
        if abs(turn) < 5.0 or la < 1.0 or lc < 1.0:
            out.append(b)
            continue
        half = math.radians(abs(turn) / 2)
        cut = min(radius_m * math.tan(half), la * 0.5, lc * 0.5)  # how far before and after the node the curve runs
        r = cut / math.tan(half)
        start = _toward(b, h1 + 180, cut)
        out.append(start)
        steps = max(1, int(abs(turn) // ARC_STEP_DEG))
        side = 1.0 if turn > 0 else -1.0
        centre = _toward(start, h1 + 90 * side, r)
        for k in range(1, steps):
            hdg = h1 + turn * k / steps
            out.append(_toward(centre, hdg - 90 * side, r))
        out.append(_toward(b, h2, cut))
    out.append(points[-1])
    return out


def _toward(p: tuple[float, float], bearing: float, metres: float) -> tuple[float, float]:
    b = math.radians(bearing)
    lat = p[0] + metres * math.cos(b) / 111_320.0
    lon = p[1] + metres * math.sin(b) / (111_320.0 * math.cos(math.radians(p[0])))
    return lat, lon


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
