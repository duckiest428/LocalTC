"""Taxi routing over the airport's taxi path graph."""

import heapq
import math
from dataclasses import dataclass

from localtc.atc_core.airport.geometry import AirportGeometry, RunwayEndGeometry

ROUTABLE = {"taxi", "path", "runway", "parking"}
RUNWAY_EDGE_PENALTY = 4.0  # prefer taxiways over rolling along runways

Node = tuple[str, int]  # ("point", index) or ("parking", index)


@dataclass(frozen=True)
class TaxiRoute:
    taxiways: tuple[str, ...]  # compressed names, e.g. ("C", "A", "A1")
    crossings: tuple[str, ...]  # runways crossed on the way (idents)
    hold_short: str | None  # runway to hold short of at the end
    length_m: float
    nodes: tuple[Node, ...]

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


class TaxiGraph:
    def __init__(self, geometry: AirportGeometry) -> None:
        self.geometry = geometry
        airport = geometry.airport
        self.positions: dict[Node, tuple[float, float]] = {
            ("point", p.index): geometry.xy(p.lat, p.lon) for p in airport.taxi_points
        }
        self.positions.update({("parking", s.index): geometry.xy(s.lat, s.lon) for s in airport.parking})
        self.edges: dict[Node, list[_Edge]] = {node: [] for node in self.positions}
        for path in airport.taxi_paths:
            if path.kind not in ROUTABLE:
                continue
            a: Node = ("point", path.start)
            b: Node = ("parking", path.end) if path.kind == "parking" else ("point", path.end)
            if a not in self.positions or b not in self.positions:
                continue
            length = math.dist(self.positions[a], self.positions[b])
            cost = length * (RUNWAY_EDGE_PENALTY if path.kind == "runway" else 1.0)
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

    def shortest_path(self, start: Node, goals: set[Node]) -> list[tuple[Node, _Edge | None]] | None:
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
                if nd < dist.get(edge.to, math.inf):
                    dist[edge.to] = nd
                    prev[edge.to] = (node, edge)
                    counter += 1
                    heapq.heappush(queue, (nd, counter, edge.to))
        return None

    def route(self, start: Node, goals: set[Node], *, hold_short: str | None = None) -> TaxiRoute | None:
        path = self.shortest_path(start, goals)
        if path is None:
            return None
        edges = [edge for _, edge in path if edge is not None]
        names: list[str] = []
        for edge in edges:
            if edge.name and (not names or names[-1] != edge.name):
                names.append(edge.name)
        target = self.geometry.end(hold_short).runway if hold_short and self.geometry.end(hold_short) else None
        crossed = [self._hold_short_runway.get(node) for node, _ in path[1:-1]]
        crossed += [
            next((r for r in self.geometry.runways if edge.runway in r.runway.idents), None) for edge in edges if edge.runway
        ]
        crossings: list[str] = []
        for runway in crossed:
            if runway is not None and runway is not target and runway.name not in crossings:
                crossings.append(runway.name)
        return TaxiRoute(
            taxiways=tuple(names),
            crossings=tuple(crossings),
            hold_short=hold_short,
            length_m=round(sum(e.length for e in edges), 1),
            nodes=tuple(node for node, _ in path),
        )

    def departure_route(self, lat: float, lon: float, end: RunwayEndGeometry) -> TaxiRoute | None:
        """Route to the full-length hold-short point for a runway end."""
        candidates = [h for h in self.geometry.hold_shorts if h.runway is end.runway]
        if not candidates:
            return None
        best = min(candidates, key=lambda h: math.dist(h.xy, end.threshold))
        start = self.nearest_node(lat, lon, kinds=("point", "parking"))
        if start is None:
            return None
        return self.route(start, {("point", best.point.index)}, hold_short=end.ident)

    def parking_route(self, lat: float, lon: float) -> TaxiRoute | None:
        goals = {n for n in self.positions if n[0] == "parking"}
        start = self.nearest_node(lat, lon)
        if start is None or not goals:
            return None
        return self.route(start, goals)
