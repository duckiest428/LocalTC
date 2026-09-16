"""Small geodesy helpers shared by the bridge and the ATC core (pure math)."""

import math

EARTH_RADIUS_NM = 3440.065
METERS_PER_NM = 1852.0
METERS_PER_DEG_LAT = 111_320.0


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(min(1.0, h)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two headings, 0-180."""
    return abs((a - b + 180) % 360 - 180)


class LocalFrame:
    """Flat-earth east/north meters around a reference point; accurate to well under a meter within ~20 km."""

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0, self.lon0 = lat0, lon0
        self._m_per_deg_lon = METERS_PER_DEG_LAT * math.cos(math.radians(lat0))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self._m_per_deg_lon, (lat - self.lat0) * METERS_PER_DEG_LAT

    def to_latlon(self, east: float, north: float) -> tuple[float, float]:
        return self.lat0 + north / METERS_PER_DEG_LAT, self.lon0 + east / self._m_per_deg_lon


def unit(heading_deg: float) -> tuple[float, float]:
    """East/north unit vector for a true heading."""
    h = math.radians(heading_deg)
    return math.sin(h), math.cos(h)
