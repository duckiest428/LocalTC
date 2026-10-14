"""Temporary flight restrictions and restricted airspace, for the map (the app's and the companion's) and the
briefing: what not to fly into, and from when until when.

- **TFRs** (the United States): the FAA's own list of TFRs and each one's area (tfr.faa.gov, public, no key).
- **Restricted, danger and prohibited areas** worldwide: openAIP (a free key, ``ops.keys``).
"""

import math
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from localtc.ops.fetch import USER_AGENT, Cache, Fetched, http_json
from localtc.ops.keys import DataKeys

TFR_LIST_URL = "https://tfr.faa.gov/tfrapi/exportTfrList"
TFR_DETAIL_URL = "https://tfr.faa.gov/download/detail_{id}.xml"
OPENAIP_URL = "https://api.core.openaip.net/api/airspaces"
TFR_TTL_S = 30 * 60.0
AIRSPACE_TTL_S = 24 * 3600.0  # restricted areas change with the AIRAC cycle, not by the hour
OPENAIP_TYPES = {1: "restricted", 2: "danger", 3: "prohibited", 26: "TRA", 27: "TSA"}
MAX_TFR_DETAILS = 40  # TFRs looked at in detail per fetch (each is one request)


@dataclass(frozen=True)
class Area:
    """An area to keep out of (or to know about): ``kind`` tfr, restricted, danger, prohibited, TRA, TSA; its outline
    (lat, lon), its levels (feet; None: not given), when (epoch s; None: always or not given)."""

    id: str
    kind: str
    name: str
    polygon: tuple[tuple[float, float], ...]
    floor_ft: int | None = None
    ceiling_ft: int | None = None
    start: float | None = None
    end: float | None = None
    source: str = ""
    text: str = ""

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name, "polygon": [list(p) for p in self.polygon],
                "floor_ft": self.floor_ft, "ceiling_ft": self.ceiling_ft, "start": self.start, "end": self.end,
                "source": self.source, "text": self.text}

    def contains(self, lat: float, lon: float) -> bool:
        inside = False
        pts = self.polygon
        for i in range(len(pts)):
            (y1, x1), (y2, x2) = pts[i], pts[i - 1]
            if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
                inside = not inside
        return inside


def _get_text(url: str, timeout_s: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return r.read().decode("utf-8-sig", errors="replace")


class Restrictions:
    def __init__(self, keys: DataKeys, cache: Cache, fetch=http_json, get_text=_get_text, clock=time.time) -> None:
        self.keys, self.cache, self.fetch, self.get_text, self.clock = keys, cache, fetch, get_text, clock

    def tfrs(self) -> tuple[list[Area], Fetched | None]:
        """The US TFRs with their areas (fetched once in a while; the details of each kept a day)."""
        got = self.cache.get("tfrs", TFR_TTL_S, self._tfrs, source="FAA TFR")
        now = self.clock()
        rows = got.data if got is not None and isinstance(got.data, list) else []
        return [a for r in rows if (a := _area(r)) is not None and (a.end is None or a.end >= now)], got

    def _tfrs(self) -> list[dict]:
        listed = self.fetch(TFR_LIST_URL) or []
        out = []
        for item in listed[:MAX_TFR_DETAILS]:
            nid = str(item.get("notam_id") or "")
            if not re.fullmatch(r"\d/\d+", nid):
                continue
            key = f"tfr:{nid}"
            have = self.cache.peek(key)
            if have is None or have.data is None:
                try:
                    xml = self.get_text(TFR_DETAIL_URL.format(id=nid.replace("/", "_")))
                    areas = parse_tfr(xml, nid, str(item.get("type") or ""), str(item.get("description") or ""))
                except Exception:  # noqa: BLE001 - one TFR unreadable: the rest still shown
                    areas = []
                have = Fetched([a.to_json() for a in areas], self.clock(), self.clock() + 86400, "FAA TFR")
                self.cache.put(key, have)
            out += list(have.data or [])
        return out

    def airspace(self, lat: float, lon: float, radius_nm: float = 150.0) -> tuple[list[Area], Fetched | None]:
        """Restricted, danger and prohibited areas around (lat, lon): openAIP, with its key."""
        if not self.keys.has("openaip"):
            return [], None
        cell = f"{round(lat)}:{round(lon)}"  # a degree's cell: nearby asks share it
        got = self.cache.get(f"openaip:{cell}", AIRSPACE_TTL_S, lambda: self._openaip(round(lat), round(lon), radius_nm),
                             source="openAIP")
        rows = got.data if got is not None and isinstance(got.data, list) else []
        return [a for r in rows if (a := _area(r)) is not None], got

    def _openaip(self, lat: float, lon: float, radius_nm: float) -> list[dict]:
        data = self.fetch(OPENAIP_URL, params={"pos": f"{lat},{lon}", "dist": int(radius_nm * 1852), "limit": 1000,
                                                "type": ",".join(str(t) for t in OPENAIP_TYPES)},
                          headers={"x-openaip-api-key": self.keys.get("openaip")})
        out = []
        for item in (data or {}).get("items") or []:
            geometry = item.get("geometry") or {}
            if geometry.get("type") != "Polygon" or not geometry.get("coordinates"):
                continue
            ring = [(float(y), float(x)) for x, y in geometry["coordinates"][0]]
            out.append(Area(id=str(item.get("_id") or item.get("name")), kind=OPENAIP_TYPES.get(item.get("type"), "restricted"),
                            name=str(item.get("name") or ""), polygon=tuple(ring),
                            floor_ft=_limit_ft(item.get("lowerLimit")), ceiling_ft=_limit_ft(item.get("upperLimit")),
                            source="openAIP").to_json())
        return out


def _limit_ft(limit) -> int | None:
    if not isinstance(limit, dict) or limit.get("value") is None:
        return None
    value = float(limit["value"])
    unit = limit.get("unit")  # 0 meter, 1 feet, 6 flight level
    return int(value * 100) if unit == 6 else int(value / 0.3048) if unit == 0 else int(value)


def parse_tfr(xml: str, nid: str, kind: str = "", description: str = "") -> list[Area]:
    """A TFR's areas from its XNOTAM XML: each merged area's points (a circle's turned into points)."""
    root = ET.fromstring(xml)
    start = _iso(root.findtext(".//Not/dateEffective"))
    end = _iso(root.findtext(".//Not/dateExpire"))
    text = " ".join((root.findtext(".//txtDescrPurpose") or root.findtext(".//txtDescrUSNS") or description or "").split())
    out = []
    for i, group in enumerate(root.iter("TFRAreaGroup")):
        ase = group.find("aseTFRArea")
        floor = _num(ase.findtext("valDistVerLower")) if ase is not None else None
        ceiling = _num(ase.findtext("valDistVerUpper")) if ase is not None else None
        name = (ase.findtext("txtName") if ase is not None else "") or f"TFR {nid}"
        for merged in group.iter("abdMergedArea"):
            points = [(p, q) for avx in merged.iter("Avx") if (p := _deg(avx.findtext("geoLat"))) is not None
                      and (q := _deg(avx.findtext("geoLong"))) is not None]
            radius = _num(merged.findtext(".//valRadiusArc"))
            if radius and len(points) <= 2:
                points = _circle(points[0], radius) if points else []
            if len(points) >= 3:
                out.append(Area(id=f"{nid}#{i}", kind="tfr", name=f"TFR {nid} {kind.title()} {name}".strip(),
                                polygon=tuple(points), floor_ft=int(floor) if floor is not None else None,
                                ceiling_ft=int(ceiling) if ceiling is not None else None, start=start, end=end,
                                source="FAA", text=text))
    return out


def _circle(center: tuple[float, float], radius_nm: float, n: int = 36) -> list[tuple[float, float]]:
    lat, lon = center
    return [(lat + radius_nm / 60 * math.cos(math.radians(a)),
             lon + radius_nm / 60 * math.sin(math.radians(a)) / max(0.1, math.cos(math.radians(lat))))
            for a in range(0, 360, 360 // n)]


def _deg(text: str | None) -> float | None:
    m = re.fullmatch(r"\s*([\d.]+)([NSEW])\s*", text or "")
    if not m:
        return None
    v = float(m.group(1))
    return -v if m.group(2) in "SW" else v


def _num(text) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _iso(text: str | None) -> float | None:
    try:
        return datetime.fromisoformat((text or "").strip()).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _area(row: dict) -> Area | None:
    try:
        return Area(id=str(row["id"]), kind=str(row["kind"]), name=str(row.get("name", "")),
                    polygon=tuple((float(a), float(b)) for a, b in row["polygon"]), floor_ft=row.get("floor_ft"),
                    ceiling_ft=row.get("ceiling_ft"), start=row.get("start"), end=row.get("end"),
                    source=str(row.get("source", "")), text=str(row.get("text", "")))
    except (KeyError, TypeError, ValueError):
        return None
