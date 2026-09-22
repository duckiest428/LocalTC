"""Who controls the sky where the aircraft is: the enroute centres and the approach areas around airports.

The outlines are the VATSIM community's (VATSpy Data Project, SimAware TRACON Project; CC BY-SA 4.0,
see README.md here), built into ``centers.json.gz`` and ``approaches.json.gz`` by
``tools/make_airspace.py``. They set where one controller hands a flight to the next, and the Live
Map draws exactly these, so what the pilot sees is what ATC uses.

Nothing here knows about frequencies: the sim publishes those at airports, and a centre with none
published gets one of its own (see facilities.area_center).
"""

import gzip
import json
from dataclasses import dataclass
from functools import cache, cached_property
from itertools import pairwise
from pathlib import Path

HERE = Path(__file__).parent

Ring = tuple[tuple[float, float], ...]  # (lat, lon) points, closed


@dataclass(frozen=True)
class Area:
    id: str  # "KZLA", "SCT:1234"
    name: str  # "Los Angeles Center", "SoCal Approach"
    kind: str  # "center", "approach" or "departure"
    rings: tuple[Ring, ...]
    label: tuple[float, float]
    airports: tuple[str, ...] = ()  # the airports an approach area serves ("SAN", "CYVR")
    oceanic: bool = False

    @cached_property
    def bbox(self) -> tuple[float, float, float, float]:
        return _bbox(self.rings)

    @cached_property
    def size(self) -> float:
        south, west, north, east = self.bbox
        return (north - south) * (east - west)

    def contains(self, lat: float, lon: float) -> bool:
        south, west, north, east = self.bbox
        if self.wraps and lon < 0:
            lon += 360.0  # its outline is kept in 0..360 so it doesn't split at the date line
        if not (south <= lat <= north and west <= lon <= east):
            return False
        return any(_inside(ring, lat, lon) for ring in self.rings)

    @cached_property
    def wraps(self) -> bool:
        """Drawn across the 180th meridian (Anchorage over the Aleutians, the Pacific oceanic areas)."""
        return any(abs(a[1] - b[1]) > 180 for ring in self.rings for a, b in pairwise(ring))

    def serves(self, icao: str) -> bool:
        """Named for ``icao``: "CYVR" as it is, or "SAN" for KSAN (FAA codes drop the K)."""
        icao = icao.upper()
        return icao in self.airports or (len(icao) == 4 and icao[0] == "K" and icao[1:] in self.airports)


class Airspace:
    def __init__(self, centers: tuple[Area, ...], approaches: tuple[Area, ...]) -> None:
        self.centers = centers
        self.approaches = approaches

    @classmethod
    def load(cls) -> "Airspace":
        return _load()

    def center_at(self, lat: float, lon: float) -> Area | None:
        """The centre working this point. Where outlines overlap (an oceanic area drawn across a domestic
        one), the domestic, then a controlled one over an information area, then the smaller: the more
        specific answer."""
        found = [a for a in self.centers if a.contains(lat, lon)]
        return min(found, key=lambda a: (a.oceanic, "information" in a.name.lower(), a.size), default=None)

    def approach_for(self, icao: str, lat: float, lon: float, *, role: str = "approach") -> Area | None:
        """The approach area an airport's arrivals (or departures) are worked in: the one named for it,
        else the one it lies in. Seattle Approach is named for SEA and still works Boeing Field."""
        named = [a for a in self.approaches if a.serves(icao)]
        if named:
            preferred = [a for a in named if a.kind == role] or named
            return min(preferred, key=lambda a: (not a.contains(lat, lon), a.size))
        around = [a for a in self.approaches if a.contains(lat, lon) and a.kind != "departure"]
        return min(around, key=lambda a: a.size, default=None)

    def near(self, south: float, west: float, north: float, east: float, *, kind: str = "center") -> list[Area]:
        """Areas whose outline box touches this one: what a map of it shows."""
        pool = self.centers if kind == "center" else self.approaches
        return [a for a in pool if _overlaps(a.bbox, (south, west, north, east))]


@cache
def _load() -> Airspace:
    def read(name: str) -> tuple[Area, ...]:
        path = HERE / name
        if not path.exists():
            return ()
        rows = json.loads(gzip.decompress(path.read_bytes()))["areas"]
        return tuple(Area(id=r["id"], name=r["name"], kind=r["kind"], label=(r["label"][0], r["label"][1]),
                          rings=_unwrapped(tuple(tuple((p[0], p[1]) for p in ring) for ring in r["rings"])),
                          airports=tuple(r.get("airports", ())), oceanic=bool(r.get("oceanic")))
                     for r in rows)

    return Airspace(read("centers.json.gz"), read("approaches.json.gz"))


def _unwrapped(rings: tuple[Ring, ...]) -> tuple[Ring, ...]:
    """Rings that jump across the date line, moved to 0..360 longitude so they are one piece."""
    if not any(abs(a[1] - b[1]) > 180 for ring in rings for a, b in pairwise(ring)):
        return rings
    return tuple(tuple((lat, lon + 360.0 if lon < 0 else lon) for lat, lon in ring) for ring in rings)


def _bbox(rings: tuple[Ring, ...]) -> tuple[float, float, float, float]:
    lats = [p[0] for ring in rings for p in ring]
    lons = [p[1] for ring in rings for p in ring]
    return min(lats), min(lons), max(lats), max(lons)


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _inside(ring: Ring, lat: float, lon: float) -> bool:
    """Ray casting: count the edges a line east from the point crosses."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        (lat_i, lon_i), (lat_j, lon_j) = ring[i], ring[j]
        if (lat_i > lat) != (lat_j > lat) and lon < (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i) + lon_i:
            inside = not inside
        j = i
    return inside
