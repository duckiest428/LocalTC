"""Airports' real weather reports (METARs), from aviationweather.gov's public API, for ATC.

MSFS gives add-ons only the weather where the aircraft is, so the destination's weather was unknown until the
aircraft got there: "the weather at the destination?" got "information not available", and its runway was chosen from
the wind at FL300. With the sim's live weather, the real METAR is what the sim is flying in; ATC uses it for an
airport until the aircraft samples the sim's own weather there.

``MetarStore.get(icao)`` answers at once from memory; a report not fetched yet, or older than ``REFRESH_S``, is fetched
on a background thread, and ``get`` returns what it has meanwhile (or None). LOCALTC_METAR="" turns it off (the tests).
"""

import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request

from localtc.sim_api import WeatherReport

log = logging.getLogger(__name__)

URL = "https://aviationweather.gov/api/data/metar"
REFRESH_S = 1800.0  # a new METAR comes hourly (and specials between): looked for every half hour
RETRY_S = 600.0  # a failed fetch (offline, the service busy) is tried again after this long
TIMEOUT_S = 20.0
HPA_PER_INHG = 33.8639


def fetch(icao: str, *, timeout_s: float = TIMEOUT_S) -> WeatherReport | None:
    query = urllib.parse.urlencode({"ids": icao.upper(), "format": "json"})
    request = urllib.request.Request(f"{URL}?{query}", headers={"User-Agent": "LocalTC"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read()
    rows = json.loads(body) if body.strip() else []
    return parse(rows[0]) if rows else None


def parse(row: dict) -> WeatherReport:
    """One aviationweather.gov METAR row (``format=json``) as a ``WeatherReport`` (its ``t`` set when published)."""
    raw = str(row.get("rawOb") or "")
    wdir = row.get("wdir")
    speed = float(row.get("wspd") or 0.0)
    direction = float(wdir) if isinstance(wdir, (int, float)) and speed > 0 else None  # "VRB", or calm
    visib = row.get("visib")
    visibility = None
    if isinstance(visib, (int, float)):
        visibility = float(visib)
    elif isinstance(visib, str) and (m := re.match(r"(\d+(?:\.\d+)?)", visib)):
        visibility = float(m.group(1))
    altim = row.get("altim")
    altimeter = round(float(altim) / HPA_PER_INHG, 2) if isinstance(altim, (int, float)) and altim > 800 else None
    observed = re.search(r"\b\d{2}(\d{4})Z\b", raw)
    return WeatherReport(
        t=0.0, icao=str(row.get("icaoId") or "").upper(), raw=raw, observed=f"{observed.group(1)}Z" if observed else "",
        wind_dir_true=direction, wind_kt=speed, gust_kt=float(row["wgst"]) if row.get("wgst") else None,
        visibility_sm=visibility, altimeter_inhg=altimeter,
        temperature_c=float(row["temp"]) if row.get("temp") is not None else None,
        dewpoint_c=float(row["dewp"]) if row.get("dewp") is not None else None,
    )


class MetarStore:
    def __init__(self, *, fetcher=fetch, background: bool = True) -> None:
        self.fetcher = fetcher
        self.background = background
        self._reports: dict[str, tuple[float, WeatherReport]] = {}  # icao: (when fetched, monotonic), report
        self._failed: dict[str, float] = {}
        self._fetching: set[str] = set()
        self._lock = threading.Lock()

    def get(self, icao: str) -> WeatherReport | None:
        icao = icao.upper()
        if not icao.isalnum():
            return None
        now = time.monotonic()
        with self._lock:
            held = self._reports.get(icao)
            fresh = held is not None and now - held[0] < REFRESH_S
            if fresh or icao in self._fetching or now - self._failed.get(icao, -RETRY_S) < RETRY_S:
                return held[1] if held else None
            self._fetching.add(icao)
        if self.background:
            threading.Thread(target=self._load, args=(icao,), name=f"metar-{icao}", daemon=True).start()
        else:
            self._load(icao)
        with self._lock:
            held = self._reports.get(icao)
            return held[1] if held else None

    def _load(self, icao: str) -> None:
        try:
            report = self.fetcher(icao)
        except Exception as exc:  # noqa: BLE001 - offline or the service busy: no report for now
            log.info("METAR for %s not fetched: %s", icao, exc)
            with self._lock:
                self._failed[icao] = time.monotonic()
                self._fetching.discard(icao)
            return
        with self._lock:
            self._fetching.discard(icao)
            if report is None:
                self._failed[icao] = time.monotonic()  # no METAR station there: asked again later
                return
            self._reports[icao] = (time.monotonic(), report)
        log.info("METAR for %s: %s", icao, report.raw)
