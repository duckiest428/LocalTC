"""The airports' real gates, from OpenStreetMap (Overpass API), cached on disk.

``GateStore.get(icao)`` answers at once from memory or the disk cache. An airport not cached yet is fetched on a
background thread, and ``get`` returns None until it arrives: ATC uses the scenery's names meanwhile. The engine
asks for the destination as soon as the flight plan names it, so the gates are there long before the landing.
An airport OSM has no gates for is cached as empty, so it isn't asked again until the cache is old.
"""

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from localtc.atc_core.airport.real_gates import GateData, parse_overpass

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
QUERY = ('[out:json][timeout:60];nwr["aeroway"="aerodrome"]["icao"="{icao}"]->.a;'
         '(node(around.a:500)["aeroway"="gate"];way(around.a:500)["aeroway"="terminal"];);out tags geom;')
MAX_AGE_S = 60 * 86400  # gates change slowly: a cached airport is fetched again after 60 days
RETRY_S = 600.0  # a failed fetch (offline, Overpass busy) is tried again after this long
TIMEOUT_S = 90.0


def fetch(icao: str, *, timeout_s: float = TIMEOUT_S) -> GateData:
    body = urllib.parse.urlencode({"data": QUERY.format(icao=icao.upper())}).encode()
    request = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": "LocalTC"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return parse_overpass(icao, json.loads(response.read()))


class GateStore:
    def __init__(self, directory: Path, *, fetcher=fetch, background: bool = True) -> None:
        self.directory = directory
        self.fetcher = fetcher
        self.background = background
        self._memory: dict[str, GateData] = {}
        self._failed: dict[str, float] = {}
        self._fetching: set[str] = set()
        self._lock = threading.Lock()

    def _path(self, icao: str) -> Path:
        return self.directory / f"{icao}.json"

    def get(self, icao: str) -> GateData | None:
        icao = icao.upper()
        if not icao.isalnum():
            return None
        with self._lock:
            if icao in self._memory:
                return self._memory[icao]
        path = self._path(icao)
        try:
            if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S:
                data = GateData.from_json(json.loads(path.read_text(encoding="utf-8")))
                with self._lock:
                    self._memory[icao] = data
                return data
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("gate cache for %s unreadable; fetching it again", icao)
        with self._lock:
            if icao in self._fetching or time.monotonic() - self._failed.get(icao, -RETRY_S) < RETRY_S:
                return None
            self._fetching.add(icao)
        if self.background:
            threading.Thread(target=self._load, args=(icao,), name=f"gates-{icao}", daemon=True).start()
            return None
        self._load(icao)
        with self._lock:
            return self._memory.get(icao)

    def _load(self, icao: str) -> None:
        try:
            data = self.fetcher(icao)
        except Exception as exc:  # noqa: BLE001 - offline or Overpass busy: the scenery's names for now
            log.info("real gates for %s not fetched: %s", icao, exc)
            with self._lock:
                self._failed[icao] = time.monotonic()
                self._fetching.discard(icao)
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._path(icao).write_text(json.dumps(data.to_json()), encoding="utf-8")
        except OSError:
            log.warning("gate cache for %s not saved", icao)
        log.info("real gates for %s: %d from %s", icao, len(data.gates), data.source)
        with self._lock:
            self._memory[icao] = data
            self._fetching.discard(icao)
