"""A taxiway closed only between two points, as a NOTAM or an ATIS puts it: "TWY E BTN TWY A AND TWY B CLSD", "TWY K BTN
RWY 4R AND TWY A CLSD". The stretch of it from the one to the other is closed; the rest of it is open.

Each end is where the taxiway meets the other one (a point both are on, or the nearest two of theirs), or meets the
runway (its point nearest the centreline). The closed stretch is the way along the taxiway itself between the two.
"""

import heapq
import math

from localtc.atc_core.airport.geometry import AirportGeometry

TAXI_KINDS = ("taxi", "path")
MEET_M = 60.0  # two taxiways' points this close are where they meet, when they share none


def closed_paths(geo: AirportGeometry, segments) -> frozenset[int]:
    """The taxi paths (indexes into ``airport.taxi_paths``) closed by ``segments``: (taxiway, from, to) each, the ends
    a taxiway's name or "RWY 04R". A stretch whose ends can't be found is left out (the whole taxiway isn't closed for
    it)."""
    out: set[int] = set()
    for name, a, b in segments:
        out |= _stretch(geo, name, a, b)
    return frozenset(out)


def _stretch(geo: AirportGeometry, name: str, a: str, b: str) -> set[int]:
    airport = geo.airport
    paths = [(i, p) for i, p in enumerate(airport.taxi_paths) if p.kind in TAXI_KINDS and p.name == name]
    if not paths:
        return set()
    points = {p.index: geo.xy(p.lat, p.lon) for p in airport.taxi_points}
    nodes = {n for _, p in paths for n in (p.start, p.end) if n in points}
    start, end = _meet(geo, points, nodes, a), _meet(geo, points, nodes, b)
    if start is None or end is None or start == end:
        return set()
    edges: dict[int, list[tuple[int, float, int]]] = {}
    for i, p in paths:
        if p.start in points and p.end in points:
            length = math.dist(points[p.start], points[p.end])
            edges.setdefault(p.start, []).append((p.end, length, i))
            edges.setdefault(p.end, []).append((p.start, length, i))
    best: dict[int, float] = {start: 0.0}
    came: dict[int, tuple[int, int]] = {}
    queue = [(0.0, start)]
    while queue:
        d, node = heapq.heappop(queue)
        if node == end:
            break
        if d > best.get(node, math.inf):
            continue
        for nxt, length, i in edges.get(node, ()):
            if d + length < best.get(nxt, math.inf):
                best[nxt] = d + length
                came[nxt] = (node, i)
                heapq.heappush(queue, (d + length, nxt))
    if end not in came:
        return set()
    out, node = set(), end
    while node != start:
        node, i = came[node]
        out.add(i)
    return out


def _meet(geo: AirportGeometry, points: dict, nodes: set[int], ref: str) -> int | None:
    """The point of the closed taxiway where ``ref`` (a taxiway, or "RWY 04R") meets it."""
    if ref.startswith("RWY "):
        ident = ref[4:]
        runway = next((r for r in geo.runways if ident in r.runway.idents
                       or ident.lstrip("0") in (i.lstrip("0") for i in r.runway.idents)), None)
        if runway is None:
            return None
        return min(nodes, key=lambda n: runway.distance_to(points[n]), default=None)
    other = {n for p in geo.airport.taxi_paths if p.kind in TAXI_KINDS and p.name == ref
             for n in (p.start, p.end) if n in points}
    if not other:
        return None
    shared = nodes & other
    if shared:
        return min(shared)
    near = min(((math.dist(points[n], points[o]), n) for n in nodes for o in other), default=None)
    return near[1] if near is not None and near[0] <= MEET_M else None
