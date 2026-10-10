"""The real aircraft around, from free public sources: ADS-B positions (adsb.lol, else adsb.fi: no key, no account)
and each flight's route and type (adsbdb.com), cached on this PC.

Blocking calls (urllib), made off the event loop by the traffic service. Each source is asked no more often than its
fair use allows (one request every few seconds for the whole area).
"""

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SOURCES = (
    ("adsb.lol", "https://api.adsb.lol/v2/lat/{lat:.4f}/lon/{lon:.4f}/dist/{nm:.0f}"),
    ("adsb.fi", "https://opendata.adsb.fi/api/v2/lat/{lat:.4f}/lon/{lon:.4f}/dist/{nm:.0f}"),
)
ROUTE_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
AIRCRAFT_URL = "https://api.adsbdb.com/v0/aircraft/{hex}"
TIMEOUT_S = 8.0
MAX_NM = 250  # the most these sources serve at once
ROUTE_TTL_S = 20 * 3600.0  # a flight number flies the same route all day
UNKNOWN_TTL_S = 3 * 3600.0
STALE_S = 30.0  # a position older than this isn't used
USER_AGENT = "LocalTC (https://localtc.tech)"
BUSY_S = 90.0  # a source that says it's busy (HTTP 429) is left alone this long
CLOCK_SLACK_S = 120.0


@dataclass(frozen=True)
class Flight:
    """One real aircraft as the sources report it."""

    hex: str  # its ICAO 24-bit address: the same aircraft from one report to the next
    callsign: str  # "UAL1234", "N172SP"; "" when it isn't sending one
    registration: str
    type: str  # ICAO type ("B38M"), "" unknown
    category: str  # ADS-B emitter category ("A3": large)
    lat: float
    lon: float
    alt_ft: float | None  # pressure altitude (29.92); None on the ground
    on_ground: bool
    gs_kt: float
    track: float
    vs_fpm: float
    squawk: str
    t: float  # when it was at that position (time.time())

    @property
    def airline(self) -> str:
        """The airline's ICAO code from the callsign ("UAL1234" -> "UAL"), "" for a registration."""
        cs = self.callsign
        return cs[:3] if len(cs) >= 4 and cs[:3].isalpha() and cs[3].isdigit() else ""


def parse(data: dict, now: float | None = None) -> list[Flight]:
    """A readsb-style answer ({"ac": [...], "now": ms}) as flights; the ones without a position left out. Each
    position's time is the source's own clock less its age: an answer a source kept a few seconds before sending it
    (the two sources' differently) was otherwise that much behind, and the aircraft surged back and forth."""
    if now is None:
        now = time.time()
        server = data.get("now")
        if isinstance(server, (int, float)) and server > 0:
            server_s = server / 1000.0 if server > 1e11 else float(server)
            if abs(server_s - now) < CLOCK_SLACK_S:  # (a clock far off on either side: this one's)
                now = server_s
    out = []
    for a in data.get("ac") or data.get("aircraft") or ():
        if a.get("lat") is None or a.get("lon") is None:
            continue
        seen = float(a.get("seen_pos") or a.get("seen") or 0.0)
        if seen > STALE_S:
            continue
        alt = a.get("alt_baro")
        ground = alt == "ground"
        out.append(Flight(
            hex=str(a.get("hex", "")).lower().lstrip("~"), callsign=str(a.get("flight") or "").strip().upper(),
            registration=str(a.get("r") or "").strip().upper(), type=str(a.get("t") or "").strip().upper(),
            category=str(a.get("category") or ""), lat=float(a["lat"]), lon=float(a["lon"]),
            alt_ft=None if ground or alt is None else float(alt), on_ground=ground,
            gs_kt=float(a.get("gs") or 0.0), track=float(a.get("track") or a.get("true_heading") or 0.0),
            vs_fpm=float(a.get("baro_rate") or a.get("geom_rate") or 0.0), squawk=str(a.get("squawk") or ""),
            t=now - seen))
    return out


def _get(url: str, timeout_s: float = TIMEOUT_S, data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


class LiveFeed:
    """The aircraft within ``nm`` of a point. The sources take turns (each asked half as often), and one that says
    it's busy is left alone for a while."""

    def __init__(self, fetch=_get, clock=time.monotonic) -> None:
        self._fetch, self._clock = fetch, clock
        self.source = ""
        self.failures = 0
        self._turn = 0
        self._busy_until: dict[str, float] = {}

    def around(self, lat: float, lon: float, nm: float) -> list[Flight] | None:
        """The flights now, or None when no source answered (no internet, all busy)."""
        nm = min(max(nm, 5.0), MAX_NM)
        now = self._clock()
        order = SOURCES[self._turn % len(SOURCES):] + SOURCES[:self._turn % len(SOURCES)]
        self._turn += 1
        for name, url in sorted(order, key=lambda s: self._busy_until.get(s[0], 0.0) > now):
            if self._busy_until.get(name, 0.0) > now:
                continue
            try:
                flights = parse(self._fetch(url.format(lat=lat, lon=lon, nm=nm)))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    self._busy_until[name] = now + BUSY_S
                    log.info("Traffic: %s is busy, asking the others for %.0f s", name, BUSY_S)
                else:
                    log.info("Traffic: %s didn't answer (%s)", name, exc)
                continue
            except (OSError, ValueError, urllib.error.URLError) as exc:
                log.info("Traffic: %s didn't answer (%s)", name, exc)
                continue
            if not self.source:
                log.info("Traffic: live positions from %s", name)
            self.source, self.failures = name, 0
            return flights
        self.failures += 1
        return None


@dataclass(frozen=True)
class Route:
    origin: str  # ICAO
    destination: str


class FlightBook:
    """Each flight's route (by callsign) and type (by hex), from adsbdb.com, kept on this PC: asked once a day at
    most per flight, and a few at a time so the free service isn't hammered."""

    def __init__(self, path: Path | None = None, fetch=_get, per_call: int = 6) -> None:
        self.path, self._fetch, self.per_call = path, fetch, per_call
        self.routes: dict[str, tuple[float, str, str]] = {}  # callsign -> (when asked, origin, destination)
        self.types: dict[str, tuple[float, str]] = {}  # hex -> (when asked, type)
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.routes = {k: tuple(v) for k, v in data.get("routes", {}).items()}
            self.types = {k: tuple(v) for k, v in data.get("types", {}).items()}
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        if self.path is None:
            return
        now = time.time()
        routes = {k: v for k, v in self.routes.items() if now - v[0] < ROUTE_TTL_S}
        types = {k: v for k, v in self.types.items() if now - v[0] < 30 * 86400}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"routes": routes, "types": types}), encoding="utf-8")
        except OSError:
            pass

    def route(self, callsign: str) -> Route | None:
        got = self.routes.get(callsign)
        return Route(got[1], got[2]) if got and got[1] else None

    def type_of(self, hex_: str) -> str:
        got = self.types.get(hex_)
        return got[1] if got else ""

    def look_up(self, flights: list[Flight]) -> int:
        """Ask for what isn't known yet (airline flights' routes, unknown types), a few per call. How many asked."""
        now, asked = time.time(), 0
        for f in flights:
            if asked >= self.per_call:
                break
            got = self.routes.get(f.callsign)
            if f.airline and (got is None or (not got[1] and now - got[0] > UNKNOWN_TTL_S)):
                asked += 1
                self.routes[f.callsign] = (now, *self._ask_route(f.callsign))
            if not f.type and f.hex and f.hex not in self.types:
                asked += 1
                self.types[f.hex] = (now, self._ask_type(f.hex))
        return asked

    def _ask_route(self, callsign: str) -> tuple[str, str]:
        try:
            r = self._fetch(ROUTE_URL.format(callsign=callsign))["response"]["flightroute"]
            return (r["origin"]["icao_code"] or "").upper(), (r["destination"]["icao_code"] or "").upper()
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            return "", ""

    def _ask_type(self, hex_: str) -> str:
        try:
            return str(self._fetch(AIRCRAFT_URL.format(hex=hex_))["response"]["aircraft"]["icao_type"] or "").upper()
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            return ""
