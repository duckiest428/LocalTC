"""Real-world weather beyond the METAR (``localtc.metar_data`` has those): an airport's TAF, the SIGMETs and AIRMETs
in force (polygons for the map and the briefing), and the weather radar's latest frame for the map. All free and
keyless: aviationweather.gov (NOAA) and RainViewer (attribution shown with the radar).
"""

import time
from dataclasses import dataclass

from localtc.ops.fetch import Cache, Fetched, http_json

AWC = "https://aviationweather.gov/api/data"
RAINVIEWER = "https://api.rainviewer.com/public/weather-maps.json"
TAF_TTL_S = 30 * 60.0
SIGMET_TTL_S = 15 * 60.0
RADAR_TTL_S = 5 * 60.0
RADAR_ATTRIBUTION = "Weather radar: RainViewer"
TOO_OLD_S = 6 * 3600.0  # a TAF or SIGMET older than this isn't shown for a live flight


@dataclass(frozen=True)
class Taf:
    icao: str
    raw: str
    issued: str  # "1123Z" etc., as the TAF has it
    valid_from: float | None
    valid_to: float | None


@dataclass(frozen=True)
class Sigmet:
    """A SIGMET (or AIRMET): its hazard ("TS", "TURB", "ICE", "VA", "IFR", "MTN OBSCN"), where (a polygon of (lat, lon)),
    between which levels (feet, None not given), from when until when (epoch s), and the text."""

    id: str
    kind: str  # sigmet, airmet
    hazard: str
    qualifier: str
    polygon: tuple[tuple[float, float], ...]
    base_ft: int | None
    top_ft: int | None
    valid_from: float
    valid_to: float
    fir: str
    raw: str

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "hazard": self.hazard, "qualifier": self.qualifier,
                "polygon": [list(p) for p in self.polygon], "base_ft": self.base_ft, "top_ft": self.top_ft,
                "valid_from": self.valid_from, "valid_to": self.valid_to, "fir": self.fir, "raw": self.raw}


class Weather:
    def __init__(self, cache: Cache, fetch=http_json, clock=time.time) -> None:
        self.cache, self.fetch, self.clock = cache, fetch, clock

    def taf(self, icao: str) -> tuple[Taf | None, Fetched | None]:
        icao = icao.upper()
        got = self.cache.get(f"taf:{icao}", TAF_TTL_S,
                             lambda: self.fetch(f"{AWC}/taf", params={"ids": icao, "format": "json"}), source="aviationweather.gov")
        rows = got.data if got is not None and isinstance(got.data, list) else []
        return (parse_taf(rows[0]) if rows else None), got

    def sigmets(self) -> tuple[list[Sigmet], Fetched | None]:
        """The international SIGMETs and the US SIGMETs and AIRMETs in force now."""
        intl = self.cache.get("isigmet", SIGMET_TTL_S, lambda: self.fetch(f"{AWC}/isigmet", params={"format": "json"}),
                              source="aviationweather.gov")
        us = self.cache.get("airsigmet", SIGMET_TTL_S, lambda: self.fetch(f"{AWC}/airsigmet", params={"format": "json"}),
                            source="aviationweather.gov")
        now = self.clock()
        out = []
        for got in (intl, us):
            for row in (got.data if got is not None and isinstance(got.data, list) else []):
                s = parse_sigmet(row)
                if s is not None and s.valid_from - 3600 <= now <= s.valid_to:
                    out.append(s)
        return out, intl or us

    def radar(self) -> dict | None:
        """The radar's latest frame as a tile URL template for the map ({z}/{x}/{y}), with its time and credit."""
        got = self.cache.get("radar", RADAR_TTL_S, lambda: self.fetch(RAINVIEWER), source="RainViewer")
        data = got.data if got is not None else None
        try:
            frame = data["radar"]["past"][-1]
            return {"url": f"{data['host']}{frame['path']}/256/{{z}}/{{x}}/{{y}}/2/1_1.png", "time": frame["time"],
                    "attribution": RADAR_ATTRIBUTION, "max_zoom": 7}
        except (TypeError, KeyError, IndexError):
            return None


def parse_taf(row: dict) -> Taf:
    return Taf(icao=str(row.get("icaoId") or "").upper(), raw=str(row.get("rawTAF") or ""),
               issued=str(row.get("issueTime") or ""), valid_from=_num(row.get("validTimeFrom")),
               valid_to=_num(row.get("validTimeTo")))


def parse_sigmet(row: dict) -> Sigmet | None:
    coords = row.get("coords") or []
    polygon = tuple((float(c["lat"]), float(c["lon"])) for c in coords
                    if isinstance(c, dict) and c.get("lat") is not None and c.get("lon") is not None)
    if len(polygon) < 3:
        return None
    start, end = _num(row.get("validTimeFrom")), _num(row.get("validTimeTo"))
    if start is None or end is None:
        return None
    kind = "airmet" if str(row.get("airSigmetType") or "").upper().startswith("AIRMET") else "sigmet"
    return Sigmet(id=str(row.get("isigmetId") or row.get("airSigmetId") or row.get("seriesId") or f"{start}{polygon[0]}"),
                  kind=kind, hazard=str(row.get("hazard") or "").upper(), qualifier=str(row.get("qualifier") or row.get("severity") or ""),
                  polygon=polygon, base_ft=_int(row.get("base") or row.get("altitudeLow1")),
                  top_ft=_int(row.get("top") or row.get("altitudeHi1")), valid_from=start, valid_to=end,
                  fir=str(row.get("firId") or row.get("icaoId") or ""), raw=str(row.get("rawSigmet") or row.get("rawAirSigmet") or ""))


def _num(v) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None
