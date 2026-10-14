"""The real-world data for the flight in hand: fetched in the background around the airports and the aircraft, told
to the ATC (an airport's NOTAMs as ``NoticesReport``), kept for the replay (``OpsSnapshot``), and served to the app
and the companion: the briefing for an airport, and what the maps draw (SIGMETs, TFRs and restricted areas, the
radar). Every item says where it's from, when it was got and until when it's current; what isn't known is
"unavailable", with why, never left out quietly.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from localtc.ops.fetch import Cache, Fetched
from localtc.ops.keys import DataKeys
from localtc.ops.notams import Notams, notices_of
from localtc.ops.restrictions import Area, Restrictions
from localtc.ops.terrain import Terrain
from localtc.ops.weather import Weather
from localtc.sim_api import NoticeItem, NoticesReport, OpsSnapshot

log = logging.getLogger(__name__)

EVERY_S = 60.0  # how often the background loop looks (each source's cache keeps its own pace)
TERRAIN_NM = 40.0  # the terrain fetched this far around the aircraft (and the airports)
NEAR_NM = 250.0  # SIGMETs, TFRs and areas shown this far around the aircraft


@dataclass
class OpsState:
    """What's known now: by airport and for the area around the aircraft."""

    notams: dict[str, list] = field(default_factory=dict)
    notams_meta: dict[str, dict] = field(default_factory=dict)
    sigmets: list = field(default_factory=list)
    sigmets_meta: dict = field(default_factory=dict)
    areas: list[Area] = field(default_factory=list)
    areas_meta: dict = field(default_factory=dict)


def meta(got: Fetched | None, why_none: str = "") -> dict:
    """An item's provenance for the UI: its source, age, expiry and whether it's stale, or why it's unavailable."""
    if got is None:
        return {"available": False, "why": why_none or "not fetched yet"}
    return {"available": got.data is not None, **got.to_json(), "why": got.error or ""}


class OpsData:
    """The sources together (blocking calls: from a thread)."""

    def __init__(self, folder: Path, keys: DataKeys, *, max_age_h: float = 6.0, clock=time.time) -> None:
        self.cache = Cache(folder / "cache.json", clock=clock)
        self.keys = keys
        self.weather = Weather(self.cache, clock=clock)
        self.notam_source = Notams(keys, self.cache, clock=clock)
        self.restrictions = Restrictions(keys, self.cache, clock=clock)
        self.terrain = Terrain(folder / "terrain")
        self.max_age_s = max_age_h * 3600.0
        self.clock = clock

    def briefing(self, icao: str, metar: str = "") -> dict:
        """An airport's briefing: its METAR (given), TAF, NOTAMs (and what ATC works to of them), each with its
        provenance."""
        icao = icao.upper()
        taf, taf_got = self.weather.taf(icao)
        notams, notams_got = self.notam_source.for_airport(icao)
        now = self.clock()
        return {
            "icao": icao, "metar": metar,
            "taf": {"raw": taf.raw if taf else "", "valid_to": taf.valid_to if taf else None, **meta(taf_got)},
            "notams": {"items": [{**n.to_json(), "works_to": [list(x) for x in notices_of(n)], "in_force": n.in_force(now)}
                                 for n in notams],
                       **meta(notams_got, "" if self.notam_source.available else "no FAA NOTAM API key (Settings > Data)")},
        }

    def around(self, lat: float, lon: float) -> dict:
        """What the maps draw around a point: the SIGMETs and AIRMETs, TFRs and restricted areas, and the radar."""
        sigmets, sig_got = self.weather.sigmets()
        tfrs, tfr_got = self.restrictions.tfrs()
        areas, area_got = self.restrictions.airspace(lat, lon)
        near = [s for s in sigmets if _near(s.polygon, lat, lon)]
        shown = [a for a in [*tfrs, *areas] if _near(a.polygon, lat, lon)]
        return {"sigmets": [s.to_json() for s in near], "sigmets_meta": meta(sig_got),
                "areas": [a.to_json() for a in shown], "tfrs_meta": meta(tfr_got),
                "airspace_meta": meta(area_got, "" if self.keys.has("openaip") else "no openAIP key (Settings > Data)"),
                "radar": self.weather.radar()}

    def notices_report(self, icao: str, t: float) -> NoticesReport | None:
        """An airport's real NOTAMs as ATC works to them, or None without them (no key, none known)."""
        notams, got = self.notam_source.for_airport(icao)
        if got is None or got.data is None:
            return None
        now = self.clock()
        items = tuple(NoticeItem(kind=k, subject=s, id=n.id, text=n.text[:200]) for n in notams if n.in_force(now)
                      for k, s in notices_of(n))
        return NoticesReport(t=t, icao=icao.upper(), notices=items, source=got.source, count=len(notams))


def _near(polygon, lat: float, lon: float) -> bool:
    import math

    for plat, plon in polygon:
        d = math.hypot(plat - lat, (plon - lon) * math.cos(math.radians(lat))) * 60
        if d <= NEAR_NM:
            return True
    return False


class OpsService:
    """The background loop for a flight: the origin's and destination's NOTAMs to the ATC, the terrain around the
    aircraft fetched ahead, the data kept for the replay."""

    def __init__(self, data: OpsData, bus, *, flight, cfg) -> None:
        self.data, self.bus, self.flight, self.cfg = data, bus, flight, cfg
        self._told: dict[str, tuple] = {}
        self._terrain_done: set = set()

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self._step()
                except Exception:  # noqa: BLE001 - real-world data never stops the flight
                    log.exception("Real-world data failed")
                await asyncio.sleep(EVERY_S)
        finally:
            await asyncio.to_thread(self.data.cache.save)

    async def _step(self) -> None:
        f = self.flight()  # {"own", "origin", "destination", "places": [(lat, lon) of the airports]}
        own = f.get("own")
        t = own.t if own is not None else 0.0
        if self.cfg.notams:
            for icao in dict.fromkeys(a for a in (f.get("origin"), f.get("destination")) if a):
                report = await asyncio.to_thread(self.data.notices_report, icao, t)
                if report is not None and self._told.get(icao) != report.notices:
                    self._told[icao] = report.notices
                    self.bus.publish(report)
                    self.bus.publish(OpsSnapshot(t=t, kind="notams", key=icao, source=report.source, got=time.time(),
                                                 data=json.dumps([msgspec_dict(n) for n in report.notices])))
                    log.info("Real NOTAMs for %s: %d in force, %d ATC works to", icao, report.count, len(report.notices))
        if self.cfg.terrain:
            places = [(own.lat, own.lon)] if own is not None else []
            places += [p for p in f.get("places", ()) if p not in self._terrain_done]
            for lat, lon in places:
                key = (round(lat, 1), round(lon, 1))
                if key in self._terrain_done:
                    continue
                self._terrain_done.add(key)
                await asyncio.to_thread(self.data.terrain.fetch_around, lat, lon, TERRAIN_NM)


def msgspec_dict(item) -> dict:
    return {f: getattr(item, f) for f in item.__struct_fields__}
