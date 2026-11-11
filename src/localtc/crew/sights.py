"""What's to be seen out of the window on the way: a famous mountain, a canyon, a great lake, a big city's lights, and
which side of the aircraft it's on, for the cabin's "for those of you on the left side ..." in the cruise.

The cities are the big ones (a million people or more) from Natural Earth's places (``places.tsv.gz``); the landmarks
are a short list of the world's best known, at their centre. Only what the aircraft passes within ``ABEAM_NM`` of, from
the cruise, ahead of the wing or about abeam (not already behind), in daylight for a landmark, by day or by night for a
city ("the lights of Chicago"), and never in cloud.
"""

import math
from dataclasses import dataclass

from localtc.crew.places import places
from localtc.sim_api.geo import bearing_deg, haversine_nm

ABEAM_NM = 40.0  # passing this close: worth a word
AHEAD_DEG = (25.0, 115.0)  # off the nose this much: out of a side window (not straight ahead under the nose, not behind)
CITY_POPULATION = 1_000_000
EVERY_S = 25 * 60.0  # one sight at most this often (a cabin that talks every five minutes is a cabin nobody listens to)
DAY = (7.5, 18.5)  # local hours a landmark can be seen

# (name, lat, lon, what it is: said "the {what}" where that reads better)
LANDMARKS = (
    ("the Grand Canyon", 36.10, -112.10), ("Lake Tahoe", 39.10, -120.04), ("Mount Rainier", 46.85, -121.76),
    ("Mount St. Helens", 46.19, -122.19), ("Mount Hood", 45.37, -121.70), ("Mount Shasta", 41.41, -122.19),
    ("Crater Lake", 42.94, -122.10), ("Pikes Peak", 38.84, -105.04), ("the Great Salt Lake", 41.10, -112.50),
    ("Lake Mead and the Hoover Dam", 36.15, -114.40), ("Death Valley", 36.50, -117.00), ("Yellowstone", 44.60, -110.50),
    ("Niagara Falls", 43.08, -79.07), ("Denali", 63.07, -151.00), ("Mount Fuji", 35.36, 138.73),
    ("Mont Blanc", 45.83, 6.86), ("the Matterhorn", 45.98, 7.66), ("Lake Geneva", 46.45, 6.55),
    ("Mount Etna", 37.75, 14.99), ("Mount Vesuvius", 40.82, 14.43), ("the Strait of Gibraltar", 35.97, -5.50),
    ("the Bosphorus", 41.10, 29.05), ("Mount Ararat", 39.70, 44.30), ("Mount Elbrus", 43.35, 42.44),
    ("Mount Teide", 28.27, -16.64), ("Mount Everest", 27.99, 86.93), ("Kilimanjaro", -3.07, 37.35),
    ("Table Mountain", -33.96, 18.40), ("Victoria Falls", -17.92, 25.86), ("the pyramids of Giza", 29.98, 31.13),
    ("the Dead Sea", 31.50, 35.50), ("Lake Titicaca", -15.90, -69.30), ("Aconcagua", -32.65, -70.01),
    ("Uluru", -25.34, 131.04), ("Aoraki Mount Cook", -43.60, 170.14), ("Eyjafjallajokull", 63.63, -19.62),
)


@dataclass(frozen=True)
class Sight:
    name: str
    side: str  # "left" or "right"
    miles: float
    city: bool  # a city (its lights by night), else a landmark
    key: str


def _candidates() -> list[tuple[str, float, float, bool]]:
    cities = [(p.name, p.lat, p.lon, True) for p in places() if p.population >= CITY_POPULATION]
    return [(n, la, lo, False) for n, la, lo in LANDMARKS] + cities


_ALL: list[tuple[str, float, float, bool]] | None = None


def sight(lat: float, lon: float, hdg_true: float, *, local_hour: float | None, in_cloud: bool,
          seen: set[str]) -> Sight | None:
    """The one to point out now, or None. ``seen``: the ones already said on this flight."""
    global _ALL
    if in_cloud:
        return None
    if _ALL is None:
        _ALL = _candidates()
    day = local_hour is None or DAY[0] <= local_hour <= DAY[1]
    best: Sight | None = None
    box = ABEAM_NM / 60.0
    cos = max(math.cos(math.radians(lat)), 0.1)
    for name, la, lo, city in _ALL:
        if name in seen or abs(la - lat) > box or abs(((lo - lon + 180) % 360) - 180) > box / cos:
            continue
        if not city and not day:
            continue  # a mountain in the dark is nothing to look at
        miles = haversine_nm(lat, lon, la, lo)
        if miles > ABEAM_NM or miles < 2.0:
            continue  # (straight underneath: nobody sees it from a window)
        off = ((bearing_deg(lat, lon, la, lo) - hdg_true + 540) % 360) - 180
        if not AHEAD_DEG[0] <= abs(off) <= AHEAD_DEG[1]:
            continue
        found = Sight(name, "right" if off > 0 else "left", miles, city, name)
        if best is None or (not found.city and best.city) or (found.city == best.city and miles < best.miles):
            best = found  # a landmark before a city, the nearer of two
    return best


def wording(s: Sight, *, night: bool) -> str:
    """ "Ladies and gentlemen, for those of you on the right side of the aircraft, that's Mount Rainier ..." """
    side = f"for those of you on the {s.side} side of the aircraft"
    if s.city and night:
        return f"Ladies and gentlemen, {side}, the lights you can see are {s.name}, about {round(s.miles / 5) * 5 or 5} miles away."
    if s.city:
        return f"Ladies and gentlemen, {side}, that's {s.name} you can see, about {round(s.miles / 5) * 5 or 5} miles off."
    return f"Ladies and gentlemen, {side}, you may be able to see {s.name}, about {round(s.miles / 5) * 5 or 5} miles away."
