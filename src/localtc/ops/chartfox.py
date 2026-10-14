"""ChartFox: flight-simulation charts for the EFB, through ChartFox's own API, as the pilot's own ChartFox account.

Signing in: ChartFox's OAuth client secret can't be in a program people download, so the LocalTC account server
holds it (server/src/chartfox.ts). ``sign_in_url`` gives the page to open in the browser, with a random state;
``collect`` picks the tokens up from the server once the pilot has signed in with ChartFox. The tokens are kept in
the system's credential store (``DataKeys``: "chartfox", "chartfox_refresh"), refreshed through the server.

The charts: an airport's charts by kind (``airport_charts``: General, Ground, SID, STAR, Approach), a chart with its
viewing link and, where ChartFox has it, its georeference (``chart``), and where the aircraft is on it
(``position_on``). Cached: an airport's chart list for a day, a chart for an hour (its viewing link expires).

ChartFox's terms: "Chart data powered by ChartFox" with a link wherever charts are shown (``CREDIT``), its privacy
policy linked where they're embedded, the tokens never shown, no charging for it, and the charts are for
simulation only, never for real navigation (``DISCLAIMER``).
"""

import json
import logging
import math
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from localtc.ops.fetch import Cache, Fetched
from localtc.ops.keys import DataKeys

log = logging.getLogger(__name__)

API = "https://api.chartfox.org"
CREDIT = {"text": "Chart data powered by ChartFox", "url": "https://chartfox.org",
          "privacy": "https://chartfox.org/privacy"}
DISCLAIMER = "For flight simulation only. Not for real-world navigation."
TYPES = {0: "Unknown", 1: "General", 2: "Textual", 3: "Ground", 4: "SID", 5: "STAR", 6: "Approach", 7: "Transition",
         8: "Briefing"}
# Which charts each phase wants first.
PHASE_TYPES = {"PARKED": ("Ground", "SID"), "PUSHBACK": ("Ground",), "TAXI_OUT": ("Ground", "SID"),
               "RUNWAY_HOLD": ("SID",), "TAKEOFF": ("SID",), "DEPARTURE": ("SID",), "CRUISE": ("STAR", "Approach"),
               "ARRIVAL": ("STAR", "Approach"), "APPROACH": ("Approach",), "LANDING": ("Approach", "Ground"),
               "TAXI_IN": ("Ground",)}
CHARTS_TTL_S = 24 * 3600.0
CHART_TTL_S = 3600.0
TIMEOUT_S = 15.0
EARTH_M = 6378137.0


class ChartFoxError(RuntimeError):
    pass


Http = Callable[[str, str, dict | None, dict[str, str]], tuple[int, Any]]


def _http(method: str, url: str, body: dict | None, headers: dict[str, str]) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", "User-Agent": "LocalTC", **({"Content-Type": "application/json"} if data else {}),
        **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except ValueError:
            return exc.code, None


class ChartFox:
    def __init__(self, keys: DataKeys, cache: Cache, *, account_url: str = "https://api.localtc.tech",
                 http: Http = _http, clock: Callable[[], float] = time.time) -> None:
        self.keys, self.cache, self.account_url, self.http, self.clock = keys, cache, account_url.rstrip("/"), http, clock
        self._expires_at = 0.0
        self.state: str | None = None

    # --- signing in --------------------------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return bool(self.keys.get("chartfox"))

    def sign_in_url(self) -> str:
        """The page to open in the browser: the account server sends it on to ChartFox's sign-in."""
        self.state = secrets.token_urlsafe(36)
        return f"{self.account_url}/v1/chartfox/authorize?{urllib.parse.urlencode({'state': self.state})}"

    def collect(self) -> bool:
        """The tokens, once the pilot has signed in (False: not yet)."""
        if not self.state:
            return False
        status, data = self.http("POST", f"{self.account_url}/v1/chartfox/token", {"state": self.state}, {})
        if status == 404:
            return False
        if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
            raise ChartFoxError((data or {}).get("error") or f"ChartFox sign-in failed ({status})")
        self._keep(data)
        self.state = None
        return True

    def _keep(self, data: dict) -> None:
        self.keys.set("chartfox", str(data["access_token"]))
        if data.get("refresh_token"):
            self.keys.set("chartfox_refresh", str(data["refresh_token"]))
        self._expires_at = self.clock() + float(data.get("expires_in") or 3600) - 60

    def disconnect(self) -> None:
        self.keys.set("chartfox", "")
        self.keys.set("chartfox_refresh", "")

    def _refresh(self) -> bool:
        token = self.keys.get("chartfox_refresh")
        if not token:
            return False
        status, data = self.http("POST", f"{self.account_url}/v1/chartfox/refresh", {"refresh_token": token}, {})
        if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
            log.info("ChartFox refresh failed (%s)", status)
            return False
        self._keep(data)
        return True

    def _get(self, path: str) -> Any:
        if not self.connected:
            raise ChartFoxError("Not signed in to ChartFox (EFB > Charts > Connect ChartFox)")
        if self._expires_at and self.clock() > self._expires_at:
            self._refresh()
        for attempt in range(2):
            status, data = self.http("GET", f"{API}{path}", None, {"Authorization": f"Bearer {self.keys.get('chartfox')}"})
            if status == 401 and attempt == 0 and self._refresh():
                continue
            if status == 401:
                self.disconnect()
                raise ChartFoxError("ChartFox signed you out: connect it again")
            if status != 200:
                raise ChartFoxError(f"ChartFox answered {status}")
            return data
        return None

    # --- the charts ---------------------------------------------------------------------------------------------

    def airport_charts(self, icao: str) -> tuple[dict | None, Fetched | None]:
        """An airport's charts by kind: {"General": [{id, name, code, type, runways, georef}], ...}."""
        icao = icao.upper()
        got = self.cache.get(f"chartfox:{icao}", CHARTS_TTL_S, lambda: self._get(f"/v2/airports/{icao}/charts/grouped"),
                             source="ChartFox")
        if got is None or not isinstance(got.data, dict):
            return None, got
        return grouped(got.data), got

    def chart(self, chart_id: str) -> dict | None:
        """One chart: its name, kind, viewing link (it expires: fetched again after an hour) and georeference."""
        got = self.cache.get(f"chartfox:chart:{chart_id}", CHART_TTL_S,
                             lambda: self._get(f"/v2/charts/{urllib.parse.quote(chart_id)}"), source="ChartFox")
        data = got.data if got is not None else None
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = data["data"]
        return one_chart(data) if isinstance(data, dict) else None


def grouped(data: dict) -> dict[str, list[dict]]:
    """ChartFox's grouped answer ({"<type>": [chart, ...]}, maybe under "data") by kind name."""
    rows = data.get("data", data)
    out: dict[str, list[dict]] = {}
    if isinstance(rows, dict):
        for key, charts in rows.items():
            try:
                name = TYPES.get(int(key), str(key))
            except (TypeError, ValueError):
                name = str(key)
            out[name] = [one_chart(c) for c in charts or () if isinstance(c, dict)]
    elif isinstance(rows, list):
        for c in rows:
            if isinstance(c, dict):
                chart = one_chart(c)
                out.setdefault(chart["type"], []).append(chart)
    return out


def one_chart(c: dict) -> dict:
    kind = c.get("type")
    try:
        kind_name = TYPES.get(int(kind), str(kind))
    except (TypeError, ValueError):
        kind_name = str(c.get("type_key") or kind or "Unknown")
    meta = c.get("meta") or {}
    runways = [m.get("value") for m in meta if isinstance(m, dict) and m.get("type") in ("Runways", "runway", 1)] \
        if isinstance(meta, list) else meta.get("runways") if isinstance(meta, dict) else None
    georef = c.get("georefs") or c.get("georef") or None
    if isinstance(georef, list):
        georef = georef[0] if georef else None
    return {"id": str(c.get("id", "")), "name": c.get("name", ""), "code": c.get("code", ""), "type": kind_name,
            "runways": runways or [], "view_url": c.get("view_url") or c.get("url") or "",
            "has_georef": bool(c.get("has_georeferences") or georef), "georef": georef}


def mercator(lat: float, lon: float) -> tuple[float, float]:
    """EPSG:3857 metres."""
    lat = max(-85.0511, min(85.0511, lat))
    return EARTH_M * math.radians(lon), EARTH_M * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def position_on(georef: dict, lat: float, lon: float, height_px: float) -> tuple[float, float] | None:
    """Where (lat, lon) is on a georeferenced chart drawn ``height_px`` high: (x, y) pixels from its top left; None
    without a usable georeference. ChartFox's transform: EPSG:3857, translated by (-tx, -ty), scaled by k for the
    chart's drawn height, rotated by transform_angle."""
    try:
        tx, ty, k = float(georef["tx"]), float(georef["ty"]), float(georef["k"])
        angle = math.radians(float(georef.get("transform_angle") or 0.0))
    except (KeyError, TypeError, ValueError):
        return None
    if not k or not height_px:
        return None
    x, y = mercator(lat, lon)
    x, y = x - tx, y - ty
    k_local = k / height_px
    c, s = math.cos(angle), math.sin(angle)
    return (c * x + s * y) / k_local, (s * x - c * y) / k_local


def for_phase(groups: dict[str, list[dict]], phase: str | None, runway: str | None = None) -> list[dict]:
    """The charts the phase wants first (the ground chart at the gate, the approach on the way in), the runway's
    before the others."""
    order = PHASE_TYPES.get(phase or "", ("Ground",))
    out = []
    for kind in order:
        charts = groups.get(kind, [])
        if runway:
            charts = sorted(charts, key=lambda ch: 0 if any(runway.upper() in str(r).upper() for r in ch.get("runways", ()))
                            or runway.upper() in (ch.get("name", "").upper()) else 1)
        out += charts
    return out
