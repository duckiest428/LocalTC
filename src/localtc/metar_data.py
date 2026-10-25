"""Airports' real weather reports (METARs, from aviationweather.gov) and real ATIS (the FAA's digital ATIS, from
atis.info, for US airports; elsewhere the ATIS of a VATSIM controller on there, from VATSIM's public data feed), all
free public APIs, for ATC (``[atc] atis_source``).

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
import urllib.error
import urllib.parse
import urllib.request

from localtc.sim_api import AtisReport, WeatherReport

log = logging.getLogger(__name__)

URL = "https://aviationweather.gov/api/data/metar"
ATIS_URL = "https://atis.info/api/{icao}"  # US airports' digital ATIS; "No results found" (a 404) elsewhere
# Everyone on VATSIM, its controllers' ATIS among them: worldwide, but only where a controller is on. Their runways and
# approaches follow the real airport's (the network's controllers work to the real wind and procedures).
VATSIM_URL = "https://data.vatsim.net/v3/vatsim-data.json"
VATSIM_KEEP_S = 120.0  # the feed (a few MB, new every 15 s) fetched at most this often for all airports together
FAA_PREFIXES = ("K", "P", "TJ", "TI")  # the airports atis.info has: the US's (Hawaii, Alaska, Guam, Puerto Rico ...)
REFRESH_S = 1800.0  # a new METAR comes hourly (and specials between): looked for every half hour
ATIS_REFRESH_S = 300.0  # a real ATIS looked for this often (a new letter comes hourly, specials between: caught soon)
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


def fetch_atis(icao: str, *, timeout_s: float = TIMEOUT_S) -> list[AtisReport] | None:
    """The airport's real ATIS (one, or an arrival and a departure one): the FAA's digital ATIS in the US, else (or
    when it has none) a VATSIM controller's; None where there's neither. Dubai asked atis.info every ten minutes of a
    fifteen-hour flight and got a 404 each time."""
    icao = icao.upper()
    if icao.startswith(FAA_PREFIXES):
        try:
            found = fetch_faa_atis(icao, timeout_s=timeout_s)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            found = None
        if found:
            return found
    return fetch_vatsim_atis(icao, timeout_s=timeout_s)


def fetch_faa_atis(icao: str, *, timeout_s: float = TIMEOUT_S) -> list[AtisReport] | None:
    """The FAA's digital ATIS (atis.info); None where it has none."""
    request = urllib.request.Request(ATIS_URL.format(icao=icao.upper()), headers={"User-Agent": "LocalTC"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        rows = json.loads(response.read() or b"[]")
    if not isinstance(rows, list):
        return None  # {"error": "No results found"}
    kinds = {"arr": "arrival", "dep": "departure"}
    out = [AtisReport(t=0.0, icao=str(r.get("airport") or icao).upper(), letter=str(r.get("code") or ""),
                      text=str(r.get("datis") or ""), kind=kinds.get(str(r.get("type")), "both"), zulu=str(r.get("time") or ""))
           for r in rows if isinstance(r, dict) and r.get("code") and r.get("datis")]
    return out or None


_vatsim: tuple[float, list] = (-1e9, [])  # (when fetched, monotonic; the feed's ATIS list)
_vatsim_lock = threading.Lock()


def _vatsim_atis_list(timeout_s: float) -> list:
    global _vatsim
    with _vatsim_lock:
        if time.monotonic() - _vatsim[0] < VATSIM_KEEP_S:
            return _vatsim[1]
        request = urllib.request.Request(VATSIM_URL, headers={"User-Agent": "LocalTC"})
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            feed = json.loads(response.read() or b"{}")
        _vatsim = (time.monotonic(), [a for a in feed.get("atis") or () if isinstance(a, dict)])
        return _vatsim[1]


def fetch_vatsim_atis(icao: str, *, timeout_s: float = TIMEOUT_S, rows: list | None = None,
                      now_zulu: str = "") -> list[AtisReport] | None:
    """The ATIS of a VATSIM controller on at ``icao`` (OMDB_ATIS; LEPA_A_ATIS and LEPA_D_ATIS, an arrival's and a
    departure's), from the network's public data feed; None when nobody's on there. Its time is now's (it's on the
    air now; ``now_zulu``: the tests'). ``rows``: the feed's ATIS list (the tests')."""
    icao = icao.upper()
    out = []
    for row in rows if rows is not None else _vatsim_atis_list(timeout_s):
        callsign = str(row.get("callsign") or "").upper()
        parts = callsign.split("_")
        lines = row.get("text_atis") or ()
        if parts[0] != icao or parts[-1] != "ATIS" or not lines or not row.get("atis_code"):
            continue
        middle = parts[1] if len(parts) == 3 else ""
        kind = {"A": "arrival", "ARR": "arrival", "D": "departure", "DEP": "departure"}.get(middle, "both")
        out.append(AtisReport(t=0.0, icao=icao, letter=str(row["atis_code"]).upper()[:1],
                              text=" ".join(str(x) for x in lines), kind=kind,
                              zulu=now_zulu or time.strftime("%H%M", time.gmtime()), source="vatsim"))
    return out or None


class MetarStore:
    what = "METAR"

    def __init__(self, *, fetcher=fetch, background: bool = True) -> None:
        self.fetcher = fetcher
        self.background = background
        self._reports: dict[str, tuple[float, WeatherReport]] = {}  # icao: (when fetched, monotonic), report
        self._failed: dict[str, float] = {}
        self._fetching: set[str] = set()
        self._lock = threading.Lock()
        self.refresh_s = REFRESH_S

    def get(self, icao: str, max_age_s: float | None = None):  # -> WeatherReport | list[AtisReport] | None
        """The report held for ``icao`` at once; fetched again in the background when it's older than ``refresh_s``
        (or ``max_age_s``, sooner: the ATIS about to be read out)."""
        icao = icao.upper()
        if not icao.isalnum():
            return None
        now = time.monotonic()
        with self._lock:
            held = self._reports.get(icao)
            fresh = held is not None and now - held[0] < min(self.refresh_s, max_age_s or self.refresh_s)
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
            log.info("%s for %s not fetched: %s", self.what, icao, exc)
            with self._lock:
                self._failed[icao] = time.monotonic()
                self._fetching.discard(icao)
            return
        with self._lock:
            self._fetching.discard(icao)
            if report is None:
                self._failed[icao] = time.monotonic()  # no METAR station there: asked again later
                if self.what == "ATIS":
                    self._reports.pop(icao, None)  # the VATSIM controller went off, and their ATIS with them
                return
            self._reports[icao] = (time.monotonic(), report)
        log.info("%s for %s: %s", "METAR" if isinstance(report, WeatherReport) else "ATIS", icao,
                 report.raw if isinstance(report, WeatherReport)
                 else " / ".join(f"{a.kind} {a.letter}{' (VATSIM)' if a.source == 'vatsim' else ''}" for a in report))


class AtisStore(MetarStore):
    """The airports' real ATIS (``fetch_atis``), the same way: at once from memory, fetched in the background. The
    ATIS changes at least hourly; it's looked for every ``ATIS_REFRESH_S``."""

    what = "ATIS"

    def __init__(self, *, fetcher=fetch_atis, background: bool = True) -> None:
        super().__init__(fetcher=fetcher, background=background)
        self.refresh_s = ATIS_REFRESH_S
