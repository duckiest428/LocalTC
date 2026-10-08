"""Where the aircraft is, the way a pilot says it: "about 20 miles north-northeast of Charlotte, North Carolina".

From Natural Earth's populated places (public domain; ``tools/make_places.py``), so the copilot answers "what city
are we over?" from the position, never from a guess.
"""

import gzip
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from localtc.sim_api.geo import bearing_deg, haversine_nm

DATA = Path(__file__).with_name("places.tsv.gz")
POINTS = ("north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast",
          "south-southeast", "south", "south-southwest", "southwest", "west-southwest", "west", "west-northwest",
          "northwest", "north-northwest")
OVER_NM = 4.0  # this close: over it
NEAR_NM = 60.0  # a town further than this isn't "near"
BIG = 100_000  # a city this big is named over a small town that's a little nearer


@dataclass(frozen=True)
class Place:
    name: str
    region: str
    country: str
    lat: float
    lon: float
    population: int

    @property
    def full(self) -> str:
        """ "Charlotte, North Carolina" (the region where it says more than the name)."""
        return f"{self.name}, {self.region}" if self.region and self.region != self.name else self.name


@lru_cache(maxsize=1)
def places() -> tuple[Place, ...]:
    out = []
    try:
        lines = gzip.decompress(DATA.read_bytes()).decode("utf-8").splitlines()
    except OSError:
        return ()
    for line in lines:
        parts = line.split("\t")
        if len(parts) == 6:
            out.append(Place(parts[0], parts[1], parts[2], float(parts[3]), float(parts[4]), int(parts[5])))
    return tuple(out)


def compass(deg: float) -> str:
    return POINTS[round((deg % 360) / 22.5) % 16]


def nearest(lat: float, lon: float) -> list[tuple[float, Place]]:
    """The places within ``NEAR_NM``, nearest first (a coarse box first: 7,000 places, many times a flight)."""
    box = NEAR_NM / 60.0
    cos = max(math.cos(math.radians(lat)), 0.1)
    found = [(haversine_nm(lat, lon, p.lat, p.lon), p) for p in places()
             if abs(p.lat - lat) <= box and abs(((p.lon - lon + 180) % 360) - 180) <= box / cos]
    return sorted((x for x in found if x[0] <= NEAR_NM), key=lambda x: x[0])


def where(lat: float, lon: float) -> str | None:
    """ "About 20 miles north-northeast of Charlotte, North Carolina"; "over Orlando, Florida"; None when there's no
    town within 60 miles (over the sea, the desert)."""
    near = nearest(lat, lon)
    if not near:
        return None
    d, p = near[0]
    # A city a little further beats a village: what a pilot would name.
    big = next(((bd, bp) for bd, bp in near if bp.population >= BIG and bd <= max(d * 2.5, 15)), None)
    if big is not None:
        d, p = big
    if d <= OVER_NM:
        return f"over {p.full}"
    miles = max(5, round(d / 5) * 5)
    return f"about {miles} miles {compass(bearing_deg(p.lat, p.lon, lat, lon))} of {p.full}"
