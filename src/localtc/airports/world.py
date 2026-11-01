"""The world's airline airports, for the Live Map's airports around you: OurAirports' large and medium airports with
airline service (public domain; ``tools/make_world_airports.py``), with their codes and size. Nothing is asked of the
sim, so they show before a flight too.
"""

import gzip
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("world.tsv.gz")
MAX_SHOWN = 350  # never more dots than this at once (the biggest first)


@dataclass(frozen=True)
class WorldAirport:
    icao: str
    iata: str
    large: bool
    lat: float
    lon: float
    name: str
    city: str
    country: str


@lru_cache(maxsize=1)
def world() -> tuple[WorldAirport, ...]:
    out = []
    for line in gzip.decompress(DATA.read_bytes()).decode("utf-8").splitlines():
        icao, iata, size, lat, lon, name, city, country = (line.split("\t") + [""] * 8)[:8]
        out.append(WorldAirport(icao, iata, size == "L", float(lat), float(lon), name, city, country))
    return tuple(out)


def within(south: float, west: float, north: float, east: float, *, limit: int = MAX_SHOWN) -> list[WorldAirport]:
    """The airports in a map's view (west past east: across the date line), the large ones first when there are more
    than ``limit``."""
    def inside(a: WorldAirport) -> bool:
        lon_ok = west <= a.lon <= east if west <= east else (a.lon >= west or a.lon <= east)
        return south <= a.lat <= north and lon_ok

    found = [a for a in world() if inside(a)]
    if len(found) > limit:
        found = sorted(found, key=lambda a: not a.large)[:limit]
    return found
