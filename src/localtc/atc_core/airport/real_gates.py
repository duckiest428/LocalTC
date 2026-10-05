"""Real gate names and international gates, from OpenStreetMap, matched onto the sim's stands.

The sim's parking spots carry only a prefix and a number ("GATE 88"); many sceneries number stands nothing
like the airport does, and none say which gates take international flights. OpenStreetMap maps most large
airports' gates (``aeroway=gate`` with ``ref=E9``) and terminals (``aeroway=terminal``, "Terminal 3",
"International Terminal"). Each OSM gate sits at the terminal's door; the sim's stand is 20-80 m out on the
apron, so each OSM gate is matched to the nearest stand it reaches, nearest pairs first, one to one.

Stands mapped as ``aeroway=parking_position`` (Zurich's) count as gates too. And the taxiways (``aeroway=taxiway``
with a ``ref``) name the sim's taxi paths where the scenery left them unnamed: Zurich's has no taxiway names at all,
and ATC could only say "taxi to the apron".

This module only parses and matches; the fetch and its cache are ``localtc.gate_data``.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any

import msgspec

from localtc.sim_api import Airport

MATCH_M = 90.0  # an OSM gate this far from a stand's centre names it
TAXIWAY_MATCH_M = 30.0  # an OSM taxiway this close to the middle of an unnamed taxi path names it ...
TAXIWAY_MATCH_DEG = 35.0  # ... if it runs the same way (a taxiway crossing it doesn't)
PHONETIC = {"alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F", "golf": "G",
            "hotel": "H", "india": "I", "juliet": "J", "kilo": "K", "lima": "L", "mike": "M", "november": "N",
            "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U",
            "victor": "V", "whiskey": "W", "xray": "X", "x-ray": "X", "yankee": "Y", "zulu": "Z"}
INTERNATIONAL_WORDS = ("international", "intl", "int'l", "tom bradley")

# Gates OSM doesn't mark: the concourses that take international arrivals (customs), by gate letter.
INTERNATIONAL_GATES: dict[str, tuple[str, ...]] = {
    "KLAS": ("E",), "KSEA": ("S",), "KSFO": ("A", "G"), "KDEN": ("A",), "KORD": ("M",), "KATL": ("E", "F"),
    "KIAH": ("D",), "KDFW": ("D",), "KMIA": ("E", "J"), "KBOS": ("E",),
}


@dataclass(frozen=True)
class RealGate:
    ref: str  # "E9"
    lat: float
    lon: float
    international: bool = False


@dataclass(frozen=True)
class RealTaxiway:
    ref: str  # "E4"
    line: tuple[tuple[float, float], ...]  # (lat, lon) along it


@dataclass(frozen=True)
class GateData:
    icao: str
    gates: tuple[RealGate, ...] = ()
    source: str = "OpenStreetMap"
    taxiways: tuple[RealTaxiway, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {"icao": self.icao, "source": self.source, "version": 2,
                "gates": [[g.ref, g.lat, g.lon, int(g.international)] for g in self.gates],
                "taxiways": [[w.ref, [[round(la, 6), round(lo, 6)] for la, lo in w.line]] for w in self.taxiways]}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "GateData":
        if data.get("version", 1) < 2:
            raise ValueError("cached before taxiways were kept")
        return cls(str(data["icao"]), tuple(RealGate(str(r), float(la), float(lo), bool(i)) for r, la, lo, i in data["gates"]),
                   str(data.get("source", "OpenStreetMap")),
                   tuple(RealTaxiway(str(r), tuple((float(a), float(b)) for a, b in line)) for r, line in data.get("taxiways", ())))


@dataclass
class _Terminal:
    international: bool
    ring: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon)


def _taxiway_ref(ref: str) -> str:
    """"E4 (from 2026-11-26)" -> "E4", "Romeo" -> "R"; "" for names that aren't a taxiway's ("Inner", "Turn Pad")."""
    ref = re.sub(r"\(.*?\)", "", ref).strip()
    if ref.lower() in PHONETIC:
        return PHONETIC[ref.lower()]
    ref = ref.upper().replace(" ", "")
    return ref if re.fullmatch(r"[A-Z]{1,2}\d{0,2}", ref) else ""


def _clean_ref(ref: str) -> str:
    """"E 9", "Gate E9", "e9" -> "E9"; "" for refs nobody says ("1;2", too long)."""
    ref = ref.upper().replace("GATE", "").replace(" ", "").replace("-", "")
    return ref if ref and len(ref) <= 5 and ref.isalnum() and any(c.isdigit() for c in ref) else ""


def _inside(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    for (a_lat, a_lon), (b_lat, b_lon) in zip(ring, ring[1:] + ring[:1]):
        if (a_lon > lon) != (b_lon > lon) and lat < (b_lat - a_lat) * (lon - a_lon) / (b_lon - a_lon) + a_lat:
            inside = not inside
    return inside


def _near_ring(lat: float, lon: float, ring: list[tuple[float, float]], m: float = 30.0) -> bool:
    k = math.cos(math.radians(lat))
    return _inside(lat, lon, ring) or any(math.hypot((p - lat) * 111_320, (q - lon) * 111_320 * k) <= m for p, q in ring)


def parse_overpass(icao: str, data: dict[str, Any]) -> GateData:
    """Overpass JSON (gate nodes, terminal ways with ``out geom``) -> the airport's gates."""
    icao = icao.upper()
    terminals = []
    for e in data.get("elements", ()):
        tags = e.get("tags", {})
        if e.get("type") == "way" and tags.get("aeroway") == "terminal" and e.get("geometry"):
            words = " ".join(tags.get(k, "") for k in ("name", "long_name", "alt_name", "official_name")).lower()
            terminals.append(_Terminal(any(w in words for w in INTERNATIONAL_WORDS),
                                       [(p["lat"], p["lon"]) for p in e["geometry"]]))
    letters = INTERNATIONAL_GATES.get(icao, ())
    gates: dict[str, RealGate] = {}
    taxiways = []
    for e in data.get("elements", ()):
        tags = e.get("tags", {})
        if e.get("type") == "way" and tags.get("aeroway") == "taxiway" and e.get("geometry"):
            if ref := _taxiway_ref(tags.get("ref", "")):
                taxiways.append(RealTaxiway(ref, tuple((p["lat"], p["lon"]) for p in e["geometry"])))
            continue
        if e.get("type") != "node" or tags.get("aeroway") not in ("gate", "parking_position"):
            continue
        ref = _clean_ref(tags.get("ref", "") or tags.get("name", ""))
        if not ref or ref in gates:
            continue
        lat, lon = float(e["lat"]), float(e["lon"])
        international = (tags.get("international") == "yes" or ref[0] in letters and ref[0].isalpha()
                         or any(t.international and _near_ring(lat, lon, t.ring) for t in terminals))
        gates[ref] = RealGate(ref, lat, lon, international)
    return GateData(icao, tuple(sorted(gates.values(), key=lambda g: g.ref)), taxiways=tuple(taxiways))


def _segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """(metres from p to the segment ab, the segment's bearing in degrees 0-180)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    u = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + u * dx), py - (ay + u * dy)), math.degrees(math.atan2(dx, dy)) % 180


def name_taxiways(airport: Airport, data: GateData, xy) -> Airport | None:
    """The airport with its unnamed taxi paths named after the OSM taxiway each lies along; None when there's nothing
    to name (the scenery names its taxiways, or OSM has none here). ``xy(lat, lon)`` -> metres."""
    if not data.taxiways or any(p.name for p in airport.taxi_paths if p.kind in ("taxi", "path")):
        return None
    points = {p.index: xy(p.lat, p.lon) for p in airport.taxi_points}
    lines = [(w.ref, [xy(la, lo) for la, lo in w.line]) for w in data.taxiways]
    named = []
    for path in airport.taxi_paths:
        a, b = points.get(path.start), points.get(path.end)
        if path.kind not in ("taxi", "path") or a is None or b is None:
            named.append(path)
            continue
        middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        heading = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 180
        best: tuple[float, str] | None = None
        for ref, line in lines:
            for p, q in zip(line, line[1:]):
                distance, bearing = _segment_distance(middle, p, q)
                turn = abs(bearing - heading)
                if distance <= TAXIWAY_MATCH_M and min(turn, 180 - turn) <= TAXIWAY_MATCH_DEG \
                        and (best is None or distance < best[0]):
                    best = (distance, ref)
        named.append(msgspec.structs.replace(path, name=best[1]) if best else path)
    return msgspec.structs.replace(airport, taxi_paths=tuple(named))


def match(spots: list[tuple[int, str, float, float]], data: GateData, xy) -> dict[int, RealGate]:
    """Each stand (index, its own label, lat, lon) named by the OSM gate nearest it: a stand already labelled like an
    OSM gate keeps it; the scenery's other lettered names ("Q15") stay its own; the bare numbers ("88") and the
    odd extra positions ("B248") pair up with the OSM gates nearest first, one to one, within MATCH_M.
    ``xy(lat, lon)`` -> metres."""
    by_ref = {g.ref: g for g in data.gates}
    out: dict[int, RealGate] = {}
    used: set[str] = set()
    for index, label, _, _ in spots:
        if (g := by_ref.get(label.upper().replace(" ", ""))) is not None and g.ref not in used:
            out[index], _ = g, used.add(g.ref)
    pairs = []
    for index, label, lat, lon in spots:
        digits = sum(c.isdigit() for c in label)
        if index in out or any(c.isalpha() for c in label) and digits < 3:
            continue
        here = xy(lat, lon)
        for g in data.gates:
            if g.ref not in used and (d := math.dist(here, xy(g.lat, g.lon))) <= MATCH_M:
                pairs.append((d, index, g))
    for _, index, g in sorted(pairs, key=lambda p: (p[0], p[1])):
        if index not in out and g.ref not in used:
            out[index], _ = g, used.add(g.ref)
    return out


def country(icao: str | None) -> str:
    """The country part of an ICAO code: one letter for the US, Canada, Australia, China and Russia; two elsewhere."""
    icao = (icao or "").upper()
    return icao[:1] if icao[:1] in ("K", "C", "Y", "Z", "U") else icao[:2]


def is_international(origin: str | None, destination: str | None) -> bool:
    return bool(origin and destination) and country(origin) != country(destination)
