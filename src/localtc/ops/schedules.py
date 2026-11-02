"""The departures due from an airport over the next hours, for the aircraft at its gates before they push back.

From LocalTC's server (server/src/departures.ts), which needs no account or key from the pilot: it asks the OpenSky
Network for the departures from the airport the day before, at the same hours, for everyone flying there. Airlines fly
the same flights day after day, so yesterday's 14:05 is today's: its callsign, where it went, and its aircraft (the type
from the aircraft's address, adsbdb.com).

With no answer, there's no schedule: the gates have the real aircraft as their transponders come on, and nothing is
made up in their place.
"""

import logging
import time
from dataclasses import dataclass

from localtc.ops.fetch import Cache, http_json

log = logging.getLogger(__name__)

SCHEDULE_TTL_S = 30 * 60.0  # an airport's schedule asked for every half hour at most
BLOCK_S = 3 * 3600.0  # ... and the server's answer moves on this often


@dataclass(frozen=True)
class Departure:
    """One departure due: ``callsign`` as its transponder will send it ("BAW7"), ``airline`` (ICAO), ``off_t`` when
    it's due off the gate (epoch s), ``destination`` (ICAO), ``type`` (ICAO, "" not known), ``hex`` the aircraft that
    flew it yesterday, ``source``."""

    callsign: str
    airline: str
    off_t: float
    destination: str = ""
    type: str = ""
    hex: str = ""
    source: str = ""


class Schedules:
    """``departures(icao, now)``: the departures from ``icao`` due from ``now`` on (blocking; off the event loop)."""

    def __init__(self, cache: Cache, *, account_url: str = "https://api.localtc.tech", fetch=http_json, clock=time.time) -> None:
        self.cache, self.account_url, self.fetch, self.clock = cache, account_url.rstrip("/"), fetch, clock

    def departures(self, icao: str, now: float | None = None) -> list[Departure]:
        now = self.clock() if now is None else now
        icao = icao.upper()
        got = self.cache.get(f"departures:{icao}:{int(now // BLOCK_S)}", SCHEDULE_TTL_S, lambda: self._ask(icao),
                             source="OpenSky Network")
        if got is None or not got.data:
            return []
        return [d for d in (_departure(r) for r in got.data) if d is not None and d.off_t >= now - 3600]

    def _ask(self, icao: str) -> list[dict]:
        return (self.fetch(f"{self.account_url}/v1/departures/{icao}") or {}).get("departures") or []


def _departure(row: dict) -> Departure | None:
    try:
        return Departure(callsign=row["callsign"], airline=row["callsign"][:3], off_t=float(row["off_t"]),
                         destination=row.get("destination") or "", hex=row.get("hex") or "",
                         source="OpenSky Network (yesterday's)")
    except (KeyError, TypeError, ValueError):
        return None
