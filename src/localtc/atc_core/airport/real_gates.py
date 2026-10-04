"""Real gate names and international gates, from OpenStreetMap, matched onto the sim's stands.

The sim's parking spots carry only a prefix and a number ("GATE 88"); many sceneries number stands nothing
like the airport does, and none say which gates take international flights. OpenStreetMap maps most large
airports' gates (``aeroway=gate`` with ``ref=E9``) and terminals (``aeroway=terminal``, "Terminal 3",
"International Terminal"). Each OSM gate sits at the terminal's door; the sim's stand is 20-80 m out on the
apron, so each OSM gate is matched to the nearest stand it reaches, nearest pairs first, one to one.

This module only parses and matches; the fetch and its cache are ``localtc.gate_data``.
"""

import math
from dataclasses import dataclass, field
from typing import Any

MATCH_M = 90.0  # an OSM gate this far from a stand's centre names it
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
class GateData:
    icao: str
    gates: tuple[RealGate, ...] = ()
    source: str = "OpenStreetMap"

    def to_json(self) -> dict[str, Any]:
        return {"icao": self.icao, "source": self.source,
                "gates": [[g.ref, g.lat, g.lon, int(g.international)] for g in self.gates]}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "GateData":
        return cls(str(data["icao"]), tuple(RealGate(str(r), float(la), float(lo), bool(i)) for r, la, lo, i in data["gates"]),
                   str(data.get("source", "OpenStreetMap")))


@dataclass
class _Terminal:
    international: bool
    ring: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon)


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
    for e in data.get("elements", ()):
        tags = e.get("tags", {})
        if e.get("type") != "node" or tags.get("aeroway") != "gate":
            continue
        ref = _clean_ref(tags.get("ref", "") or tags.get("name", ""))
        if not ref or ref in gates:
            continue
        lat, lon = float(e["lat"]), float(e["lon"])
        international = (tags.get("international") == "yes" or ref[0] in letters and ref[0].isalpha()
                         or any(t.international and _near_ring(lat, lon, t.ring) for t in terminals))
        gates[ref] = RealGate(ref, lat, lon, international)
    return GateData(icao, tuple(sorted(gates.values(), key=lambda g: g.ref)))


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
