"""Fetching from the data services: JSON (or text) over HTTPS, cached on this PC with when it was got and until when
it's good, each service asked no more often than its fair use, and a service that's busy or down left alone a while.

``Cache.get(key, ttl_s, fetch)`` answers from the cache while it's fresh, else fetches (blocking: call it off the
event loop), keeping the old answer if the fetch fails (marked stale, with its age).
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = "LocalTC (https://localtc.tech)"
TIMEOUT_S = 15.0
BUSY_S = 300.0  # a service that said it's busy (429) or failed is left alone this long


@dataclass(frozen=True)
class Fetched:
    """An answer and where it stands: ``got`` when fetched (epoch s), ``expires`` when it's no longer current,
    ``stale`` when the newest fetch failed and this is the last one known, ``error`` why (None when fine)."""

    data: Any
    got: float
    expires: float
    source: str
    stale: bool = False
    error: str | None = None

    def age_s(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.got

    def to_json(self) -> dict:
        return {"got": self.got, "expires": self.expires, "source": self.source, "stale": self.stale, "error": self.error}


def http_json(url: str, *, params: dict | None = None, headers: dict | None = None, data: bytes | None = None,
              timeout_s: float = TIMEOUT_S, method: str | None = None) -> Any:
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read()
    return json.loads(body) if body.strip() else None


class Cache:
    """On-disk JSON cache of each answer, by key ("taf:EGLL"), with the times it was got and expires."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, Fetched] = {}
        self._busy: dict[str, float] = {}  # source -> until when it's left alone
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._items = {k: Fetched(v["data"], v["got"], v["expires"], v["source"]) for k, v in raw.items()}
            except (OSError, ValueError, KeyError, TypeError):
                log.info("Data cache at %s unreadable: starting empty", path)

    def peek(self, key: str) -> Fetched | None:
        with self._lock:
            return self._items.get(key)

    def put(self, key: str, value: Fetched) -> None:
        with self._lock:
            self._items[key] = value

    def get(self, key: str, ttl_s: float, fetch: Callable[[], Any], *, source: str) -> Fetched | None:
        now = self.clock()
        have = self.peek(key)
        if have is not None and now < have.expires and not have.stale:
            return have
        if now < self._busy.get(source, 0.0):
            return _stale(have, "busy") if have is not None else None
        try:
            data = fetch()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                self._busy[source] = now + BUSY_S
            log.info("%s: %s not fetched (HTTP %s)", source, key, exc.code)
            return _stale(have, f"HTTP {exc.code}") if have is not None else Fetched(None, now, now, source, True, f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            self._busy[source] = now + BUSY_S / 5
            log.info("%s: %s not fetched (%s)", source, key, exc)
            return _stale(have, str(exc)) if have is not None else Fetched(None, now, now, source, True, str(exc))
        got = Fetched(data, now, now + ttl_s, source)
        self.put(key, got)
        return got

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            out = {k: {"data": v.data, "got": v.got, "expires": v.expires, "source": v.source}
                   for k, v in self._items.items() if not v.stale and v.data is not None}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(out), encoding="utf-8")
        except OSError as exc:
            log.info("Data cache not saved: %s", exc)


def _stale(have: Fetched, why: str) -> Fetched:
    return Fetched(have.data, have.got, have.expires, have.source, True, why)
