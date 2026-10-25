"""Taxi routing over the airport's taxi path graph."""

import heapq
import itertools
import math
from dataclasses import dataclass

from localtc.atc_core.airport.aircraft_size import taxiway_width_m
from localtc.atc_core.airport.geometry import AirportGeometry, RunwayEndGeometry

ROUTABLE = {"taxi", "path", "runway", "parking"}
RUNWAY_EDGE_PENALTY = 4.0  # prefer taxiways over rolling along runways
FULL_LENGTH_SLACK_M = 150.0  # hold points this much further from the threshold still give a full-length departure

Node = tuple[str, int]  # ("point", index) or ("parking", index)


@dataclass(frozen=True)
class TaxiRoute:
    taxiways: tuple[str, ...]  # compressed names, e.g. ("C", "A", "A1")
    crossings: tuple[str, ...]  # runways crossed on the way (idents)
    hold_short: str | None  # runway to hold short of at the end
    length_m: float
    nodes: tuple[Node, ...]
    hold_point: str = ""  # the taxiway the hold-short point is on ("D", "C7"), "" when it has no name

    @property
    def via(self) -> str:
        return ", ".join(self.taxiways)


@dataclass(frozen=True)
class _Edge:
    to: Node
    cost: float
    length: float
    name: str
    runway: str


TURN_AROUND_M = 1500.0  # what starting behind the aircraft costs: a long way round is better than a U-turn
CLOSED_PENALTY = 100.0  # a closed taxiway (the ATIS's notice) is used only where there's no other way


class TaxiGraph:
    def __init__(self, geometry: AirportGeometry, *, closed: frozenset[str] = frozenset(), wingspan_m: float = 0.0,
                 closed_paths: frozenset[int] = frozenset()) -> None:
        """``closed``: taxiways closed along their length; ``closed_paths``: the pieces of one closed between two
        points (``closures.closed_paths``)."""
        self.geometry = geometry
        airport = geometry.airport
        self.positions: dict[Node, tuple[float, float]] = {
            ("point", p.index): geometry.xy(p.lat, p.lon) for p in airport.taxi_points
        }
        self.positions.update({("parking", s.index): geometry.xy(s.lat, s.lon) for s in airport.parking})
        self.edges: dict[Node, list[_Edge]] = {node: [] for node in self.positions}
        need_m = taxiway_width_m(wingspan_m)  # the width its wings need (Haneda's lanes had an A350 hitting things)
        for index, path in enumerate(airport.taxi_paths):
            if path.kind not in ROUTABLE:
                continue
            a: Node = ("point", path.start)
            b: Node = ("parking", path.end) if path.kind == "parking" else ("point", path.end)
            if a not in self.positions or b not in self.positions:
                continue
            length = math.dist(self.positions[a], self.positions[b])
            cost = length * (RUNWAY_EDGE_PENALTY if path.kind == "runway" else 1.0) * (
                CLOSED_PENALTY if path.name and path.name in closed or index in closed_paths else 1.0) * _narrow(path, need_m)
            runway = path.runway if path.kind == "runway" else ""
            self.edges[a].append(_Edge(b, cost, length, path.name, runway))
            self.edges[b].append(_Edge(a, cost, length, path.name, runway))
        self._hold_short_runway = {("point", h.point.index): h.runway for h in geometry.hold_shorts}

    def nearest_node(self, lat: float, lon: float, *, kinds: tuple[str, ...] = ("point",)) -> Node | None:
        xy = self.geometry.xy(lat, lon)
        candidates = [
            n for n in self.positions
            if n[0] in kinds and self.edges[n] and not self._on_runway(n)
        ]
        return min(candidates, key=lambda n: math.dist(self.positions[n], xy), default=None)

    def _on_runway(self, node: Node) -> bool:
        return any(r.contains(self.positions[node]) for r in self.geometry.runways)

    def shortest_path(self, start: Node, goals: set[Node], *, heading: float | None = None
                      ) -> list[tuple[Node, _Edge | None]] | None:
        """``heading``: the way the aircraft faces at ``start``; the first stretch taken back the other way costs a
        turn-around (Haneda's taxi in began on T9, behind the aircraft: a U-turn on a taxiway)."""
        dist: dict[Node, float] = {start: 0.0}
        prev: dict[Node, tuple[Node, _Edge]] = {}
        queue: list[tuple[float, int, Node]] = [(0.0, 0, start)]
        counter = 0
        while queue:
            d, _, node = heapq.heappop(queue)
            if node in goals:
                steps: list[tuple[Node, _Edge | None]] = [(node, None)]
                while node in prev:
                    node, edge = prev[node]
                    steps.append((node, edge))
                steps.reverse()  # each step carries the edge used to leave it
                return steps
            if d > dist.get(node, math.inf):
                continue
            for edge in self.edges.get(node, ()):
                nd = d + edge.cost
                if heading is not None and node == start:
                    (ax, ay), (bx, by) = self.positions[node], self.positions[edge.to]
                    bearing = math.degrees(math.atan2(bx - ax, by - ay)) % 360
                    if abs((bearing - heading + 180) % 360 - 180) > 100:
                        nd += TURN_AROUND_M
                if nd < dist.get(edge.to, math.inf):
                    dist[edge.to] = nd
                    prev[edge.to] = (node, edge)
                    counter += 1
                    heapq.heappush(queue, (nd, counter, edge.to))
        return None

    def _along(self, path: list) -> list[tuple[float, float]]:
        """Points every ~15 m along the route's inner stretch (not the stand it starts from nor the hold line
        it ends at): a straight segment can cross a runway without a node on it."""
        nodes = [node for node, _ in path]
        points: list[tuple[float, float]] = []
        for a, b in itertools.pairwise(nodes):
            (ax, ay), (bx, by) = self.positions[a], self.positions[b]
            steps = max(1, int(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 // 15))
            points += [(ax + (bx - ax) * k / steps, ay + (by - ay) * k / steps) for k in range(steps + 1)]
        return points[1:-1]

    def route(self, start: Node, goals: set[Node], *, hold_short: str | None = None,
              heading: float | None = None) -> TaxiRoute | None:
        path = self.shortest_path(start, goals, heading=heading)
        if path is None:
            return None
        edges = [edge for _, edge in path if edge is not None]
        names = _named_runs(edges)
        target = self.geometry.end(hold_short).runway if hold_short and self.geometry.end(hold_short) else None
        crossed = [self._hold_short_runway.get(node) for node, _ in path[1:-1]]
        crossed += [
            next((r for r in self.geometry.runways if edge.runway in r.runway.idents), None) for edge in edges if edge.runway
        ]
        # A taxiway laid over a runway without the sim marking it as one (Denver's WB over the end of 16L/34R)
        # is crossed all the same: any point of the route on a runway's surface counts.
        crossed += [r for r in self.geometry.runways if r is not target
                    and any(r.contains(p) for p in self._along(path))]
        crossings: list[str] = []
        points = [self.positions[node] for node, _ in path]
        along_runways = {r.name for edge in edges if edge.runway for r in self.geometry.runways if edge.runway in r.runway.idents}
        for runway in crossed:
            # A runway taxied along is in the way; one passed by (a hold line on the way, a corner clipped) only
            # when the route goes to its far side.
            if runway is not None and runway is not target and runway.name not in crossings                     and (runway.name in along_runways or _both_sides(runway, points)):
                crossings.append(runway.name)
        # The runway being taxied to is crossed too when the route goes over it to reach a taxiway on the
        # far side: San Diego's B to C1 crosses 09/27 at B6/C6 before lining up on it. Passing over its
        # surface on the way, not stopping at its hold line, is what makes it a crossing.
        if target is not None and target.name not in crossings and any(
                target.contains(self.positions[node]) for node, _ in path[1:-1]):
            crossings.append(target.name)
        return TaxiRoute(
            taxiways=tuple(names),
            crossings=tuple(crossings),
            hold_short=hold_short,
            hold_point=edges[-1].name if edges and hold_short else "",
            length_m=round(sum(e.length for e in edges), 1),
            nodes=tuple(node for node, _ in path),
        )

    def departure_route(self, lat: float, lon: float, end: RunwayEndGeometry) -> TaxiRoute | None:
        """Route to the full-length hold-short point for a runway end."""
        candidates = [h for h in self.geometry.hold_shorts if h.runway is end.runway]
        if not candidates:
            return None
        start = self.nearest_node(lat, lon, kinds=("point", "parking"))
        if start is None:
            return None
        # A correction says which taxiway the holding point is on (Montreal's 06L: C): that one, if it's there.
        wanted = self.geometry.hold_taxiways.get(end.ident)
        on = [h for h in candidates if any(e.name == wanted for e in self.edges.get(("point", h.point.index), ()))] if wanted else []
        if on:
            candidates = on
        # A runway usually has a hold line on each side of its threshold. The one nearest the threshold
        # is not the one to use if reaching it means crossing the runway first: San Diego's C1 is a few
        # metres closer to 27 than B1, and choosing it sent a south-side departure across 09/27 to line
        # up on the runway it had just crossed. Every hold point near the threshold is tried: one this side
        # of the runway wins, and among those the one nearest the threshold, for the full length.
        nearest = min(math.dist(h.xy, end.threshold) for h in candidates)
        near = [h for h in candidates if math.dist(h.xy, end.threshold) <= nearest + FULL_LENGTH_SLACK_M]
        routes = [(r, math.dist(h.xy, end.threshold)) for h in near
                  if (r := self.route(start, {("point", h.point.index)}, hold_short=end.ident))]
        if not routes:
            return None
        best, _ = min(routes, key=lambda rd: (end.runway.name in rd[0].crossings, len(rd[0].crossings), rd[1], rd[0].length_m))
        return best

    def runway_exit(self, lat: float, lon: float, heading_true: float, *, ahead_m: float = 2500.0,
                    toward: tuple[float, float] | None = None, extra_m: float = 1200.0,
                    min_ahead_m: float = 0.0) -> tuple[str, str] | None:
        """The next taxiway off the runway ahead of an aircraft rolling out: (side, taxiway), "left"/"right" as the
        pilot sees it ("vacate left onto E4"), or None when none is named ahead. ``toward`` (lat, lon): where the
        aircraft goes next (its gate, the terminal): an exit on that side is taken over the very next one, when it's
        not much further down the runway (``extra_m``). Vacating left with every gate to the right only made a long
        taxi back across. ``min_ahead_m``: still rolling, not one it can't slow down for."""
        here = self.geometry.xy(lat, lon)
        hx, hy = math.sin(math.radians(heading_true)), math.cos(math.radians(heading_true))
        found: list[tuple[float, str, str]] = []
        for node, edges in self.edges.items():
            if node[0] != "point" or not self._on_runway(node):
                continue
            nx, ny = self.positions[node]
            along = (nx - here[0]) * hx + (ny - here[1]) * hy
            if not min_ahead_m <= along <= ahead_m or abs((nx - here[0]) * hy - (ny - here[1]) * hx) > 60.0:
                continue  # behind, too far on, or not on this runway (beside the aircraft's line)
            for edge in edges:
                if edge.runway or not edge.name or self._on_runway(edge.to):
                    continue
                vx, vy = self.positions[edge.to][0] - nx, self.positions[edge.to][1] - ny
                side = "left" if hx * vy - hy * vx > 0 else "right"
                found.append((along, side, edge.name))
        if not found:
            return None
        found.sort()
        first = found[0]
        if toward is not None:
            tx, ty = self.geometry.xy(*toward)
            want = "left" if hx * (ty - here[1]) - hy * (tx - here[0]) > 0 else "right"
            on_side = next((f for f in found if f[1] == want), None)
            if on_side is not None and on_side[0] - first[0] <= extra_m:
                return on_side[1], on_side[2]
        return first[1], first[2]

    def parking_route(self, lat: float, lon: float, spot: int | None = None, *, kind: str = "",
                      heading: float | None = None, span_m: float = 0.0) -> TaxiRoute | None:
        """To the nearest parking, or to parking spot ``spot`` (a gate ATC assigned). ``kind``: only spots whose
        kind starts with it ("ramp_ga": a GA flight's), when the airport has any. ``heading``: the way the aircraft
        faces, a route ahead of it preferred to one behind."""
        goals = {n for n in self.positions if n[0] == "parking" and (spot is None or n[1] == spot)}
        if kind and spot is None:
            kinds = {s.index: s.kind for s in self.geometry.airport.parking}
            wanted = {n for n in goals if kinds.get(n[1], "").startswith(kind)}
            goals = wanted or goals
        if span_m and spot is None:
            # Big enough for it: a Citation Latitude's 22 m on a light aircraft's tie-down, the nearest GA spot, wasn't.
            radius = {s.index: s.radius_m for s in self.geometry.airport.parking}
            fits = {n for n in goals if radius.get(n[1], 0.0) * 2 >= span_m * FIT}
            goals = fits or goals
        start = self.nearest_node(lat, lon)
        if start is None or not goals:
            return None
        return self.route(start, goals, heading=heading)


def _both_sides(runway, points: list[tuple[float, float]]) -> bool:
    """The route goes from one side of ``runway`` to the other. One that starts beside it (just vacated) and only
    clips its edge doesn't: "hold short 09L" to an aircraft that had just turned off 27R towards its stand, and
    Haneda's "hold short runway 22" the same."""
    across = [runway.along_across(p)[1] for p in points]
    side = runway.half_width
    return min(across) < -side and max(across) > side


# A taxiway narrower than the aircraft needs: used only where there's no other way (lenient: the sim's widths are
# rough, so a little under is a little dearer, well under a long way round).
NARROW_PENALTY, TOO_NARROW_PENALTY, TOO_NARROW = 2.0, 30.0, 0.65
FIT = 0.9  # a parking spot this much of the wingspan across at least (the scenery's radii are generous)


def _narrow(path, need_m: float) -> float:
    if need_m <= 0 or path.width_m <= 0 or path.kind in ("runway", "parking") or path.width_m >= need_m:
        return 1.0
    return TOO_NARROW_PENALTY if path.width_m < need_m * TOO_NARROW else NARROW_PENALTY


STUB_M = 80.0  # a piece of another taxiway this short between two of the same is that taxiway (a link's stub)


def _named_runs(edges) -> list[str]:
    """The taxiways a route follows, in order: each once per stretch. A short piece named for a link between two
    stretches of the same taxiway is that taxiway (Heathrow's "A, A6, A, A5, A, A3, A1" is "A, A1")."""
    runs: list[list] = []  # [name, length]
    for edge in edges:
        if not edge.name:
            continue
        if runs and runs[-1][0] == edge.name:
            runs[-1][1] += edge.length
        else:
            runs.append([edge.name, edge.length])
    i = 1
    while i < len(runs) - 1:
        if runs[i][1] < STUB_M and runs[i - 1][0] == runs[i + 1][0]:
            runs[i - 1][1] += runs[i][1] + runs[i + 1][1]
            del runs[i:i + 2]
        else:
            i += 1
    return [name for name, _ in runs]
