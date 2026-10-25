"""An airport's ground as the Live Map draws it close in: the runways at their real width with their numbers at each
threshold, the taxiways (their centrelines, each name labelled along it a few times), the hold-short lines, and
the gates and stands by the names ATC gives them (the airport's real gate names when they're known), which are
taken and which is yours. From the sim's own airport data (``sim_api.Airport``), as ATC works from it.
"""

import math
from typing import Any

from localtc.atc_core.airport.gates import parse as parse_gate
from localtc.sim_api.airport import Airport

M_PER_DEG = 111_320.0
TAXI_KINDS = ("taxi", "path")  # the surfaces aircraft taxi on (not runways, roads, vehicle lanes or closed ones)
LABEL_SPACING_M = 350.0  # a taxiway's name again this far along it
LABELS_PER_TAXIWAY = 6
HOLD_BAR_M = 12.0  # half the width of a hold-short bar


def _offset(lat: float, lon: float, heading_deg: float, metres: float) -> tuple[float, float]:
    h = math.radians(heading_deg)
    return (lat + metres * math.cos(h) / M_PER_DEG,
            lon + metres * math.sin(h) / (M_PER_DEG * max(0.01, math.cos(math.radians(lat)))))


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.cos(math.radians(a[0]))
    return math.hypot((a[0] - b[0]) * M_PER_DEG, (a[1] - b[1]) * M_PER_DEG * k)


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.cos(math.radians(a[0]))
    return math.degrees(math.atan2((b[1] - a[1]) * k, b[0] - a[0])) % 360


def _round(p: tuple[float, float]) -> list[float]:
    return [round(p[0], 6), round(p[1], 6)]


def runways(airport: Airport, use: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Each runway's outline (four corners) and its two numbers at their thresholds; ``use``: each end in use, "arr",
    "dep" or "both" (the ATIS's), shown on its number."""
    use = use or {}
    out = []
    for rw in airport.runways:
        if not rw.length_m or rw.water:  # a seaplane runway: open water, nothing painted
            continue
        half, width = rw.length_m / 2, max(rw.width_m, 23.0) / 2
        start = _offset(rw.lat, rw.lon, rw.heading_true + 180, half)  # the primary end's threshold: landing on it
        end = _offset(rw.lat, rw.lon, rw.heading_true, half)  # flies along heading_true
        corners = [_offset(*start, rw.heading_true - 90, width), _offset(*end, rw.heading_true - 90, width),
                   _offset(*end, rw.heading_true + 90, width), _offset(*start, rw.heading_true + 90, width)]
        inset = min(120.0, half * 0.25)  # the number painted a little in from the threshold
        out.append({"name": f"{rw.primary.ident}/{rw.secondary.ident}", "outline": [_round(c) for c in corners],
                    "ends": [{"ident": rw.primary.ident, "at": _round(_offset(*start, rw.heading_true, inset)),
                              "heading": round(rw.heading_true), "use": use.get(rw.primary.ident, "")},
                             {"ident": rw.secondary.ident, "at": _round(_offset(*end, rw.heading_true + 180, inset)),
                              "heading": round((rw.heading_true + 180) % 360), "use": use.get(rw.secondary.ident, "")}]})
    return out


def taxiways(airport: Airport, closed: tuple[frozenset[str], frozenset[int]] | None = None
             ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """The taxiways as segments ({name, a, b, width, closed}), where each name is labelled ({name, at, heading}), and
    the hold-short bars ({a, b}) across them. ``closed``: the ATIS's closures (names closed along their length, the
    taxi paths closed between two points)."""
    names, pieces = closed or (frozenset(), frozenset())
    points = {p.index: p for p in airport.taxi_points}
    segments, by_name = [], {}
    touching: dict[int, list[tuple[float, float]]] = {}
    for index, path in enumerate(airport.taxi_paths):
        if path.kind not in TAXI_KINDS or path.start not in points or path.end not in points:
            continue
        a, b = points[path.start], points[path.end]
        pa, pb = (a.lat, a.lon), (b.lat, b.lon)
        segments.append({"name": path.name, "a": _round(pa), "b": _round(pb), "width": round(path.width_m or 0.0, 1),
                         "closed": bool(path.name and path.name in names or index in pieces)})
        if path.name:
            by_name.setdefault(path.name, []).append((pa, pb))
        touching.setdefault(path.start, []).append(pb)
        touching.setdefault(path.end, []).append(pa)
    labels = []
    for name, parts in by_name.items():
        placed: list[tuple[float, float]] = []
        for pa, pb in sorted(parts, key=lambda s: -_dist_m(*s)):  # the longest stretches first
            mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
            if _dist_m(pa, pb) < 25 or any(_dist_m(mid, q) < LABEL_SPACING_M for q in placed):
                continue
            placed.append(mid)
            heading = _bearing(pa, pb)
            labels.append({"name": name, "at": _round(mid), "heading": round(heading if heading < 180 else heading - 180)})
            if len(placed) >= LABELS_PER_TAXIWAY:
                break
    holds = []
    for index, p in points.items():
        if not p.is_hold_short or index not in touching:
            continue
        across = _bearing((p.lat, p.lon), touching[index][0]) + 90  # square to the taxiway it's on
        holds.append({"a": _round(_offset(p.lat, p.lon, across, HOLD_BAR_M)),
                      "b": _round(_offset(p.lat, p.lon, across + 180, HOLD_BAR_M))})
    return segments, labels, holds


def gates(airport: Airport, gate_map: dict | None = None) -> list[dict[str, Any]]:
    """The gates and stands: ATC's names for them (the engine's gate map, with the real names and which are taken
    and yours, when there's a flight), else the scenery's."""
    if gate_map:
        mine = gate_map.get("assigned")
        return [{"label": s["label"] or s["name"], "word": s["word"], "lat": round(s["lat"], 6), "lon": round(s["lon"], 6),
                 "occupied": bool(s.get("occupied") or s.get("taken")), "yours": s["index"] == mine,
                 "international": bool(s.get("international"))} for s in gate_map.get("stands", [])]
    out = []
    for spot in airport.parking:
        gate = parse_gate(spot)
        if gate is not None:
            out.append({"label": gate.label, "word": gate.word, "lat": round(spot.lat, 6), "lon": round(spot.lon, 6),
                        "occupied": False, "yours": False, "international": False})
    return out


def layout(airport: Airport, gate_map: dict | None = None, *, use: dict[str, str] | None = None,
           closed: tuple[frozenset[str], frozenset[int]] | None = None) -> dict[str, Any]:
    """``use``: the runway ends in use (``runways``); ``closed``: the closed taxiways (``taxiways``)."""
    segments, labels, holds = taxiways(airport, closed)
    return {"icao": airport.icao, "runways": runways(airport, use), "taxiways": segments, "taxiway_labels": labels,
            "holds": holds, "gates": gates(airport, gate_map)}


def runway_use(landing: tuple[str, ...], departing: tuple[str, ...]) -> dict[str, str]:
    """Each runway end in use: "arr", "dep" or "both"."""
    return {ident: "both" if ident in landing and ident in departing else "arr" if ident in landing else "dep"
            for ident in (*landing, *departing)}
