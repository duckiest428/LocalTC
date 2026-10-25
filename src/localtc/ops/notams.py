"""Real NOTAMs for an airport, from the FAA's NOTAM API (free: a client id and secret from api.faa.gov, ``ops.keys``).
The FAA's system carries the international NOTAMs too, so it answers for Heathrow as for Seattle.

Each NOTAM is kept as it was issued (its text, its validity), and the ones ATC can work to are read into the same
notices LocalTC's ATIS uses (``atc_core.atis.operations.Notice``: a runway or taxiway closed, an ILS or its glide
path, approach lights or a navaid out, birds), marked as real. They never mix with the notices LocalTC draws for
an airport without real ones: an airport has real NOTAMs or drawn ones, never both.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from localtc.atc_core.atis.operations import read_notices
from localtc.ops.fetch import Cache, Fetched, http_json
from localtc.ops.keys import DataKeys

FAA_URL = "https://external-api.faa.gov/notamapi/v1/notams"
NOTAM_TTL_S = 30 * 60.0


@dataclass(frozen=True)
class Notam:
    id: str  # "A1234/26"
    icao: str
    text: str  # the E) line, or the domestic text
    start: float | None  # epoch s; None: in force now
    end: float | None  # None: permanent or until further notice
    raw: str = ""

    def in_force(self, now: float) -> bool:
        return (self.start is None or self.start <= now) and (self.end is None or now <= self.end)

    def to_json(self) -> dict:
        return {"id": self.id, "icao": self.icao, "text": self.text, "start": self.start, "end": self.end}


def notices_of(notam: Notam) -> list[tuple[str, str, str]]:
    """What ATC can work to in a NOTAM, as (kind, subject, detail) of ``Notice`` (``read_notices``): [] when it's
    nothing of the kind (most are: obstacles, a stand, the fuel farm)."""
    return read_notices(notam.text)


class Notams:
    def __init__(self, keys: DataKeys, cache: Cache, fetch=http_json, clock=time.time) -> None:
        self.keys, self.cache, self.fetch, self.clock = keys, cache, fetch, clock

    @property
    def available(self) -> bool:
        return self.keys.has("faa")

    def for_airport(self, icao: str) -> tuple[list[Notam], Fetched | None]:
        """The airport's NOTAMs in force now (or about to be, within the day)."""
        if not self.available:
            return [], None
        icao = icao.upper()
        got = self.cache.get(f"notams:{icao}", NOTAM_TTL_S, lambda: self._fetch(icao), source="FAA NOTAM API")
        now = self.clock()
        rows = got.data if got is not None and isinstance(got.data, list) else []
        out = [n for r in rows if (n := _notam(r)) is not None and (n.end is None or n.end >= now)
               and (n.start is None or n.start <= now + 86400)]
        return out, got

    def _fetch(self, icao: str) -> list[dict]:
        rows, page = [], 1
        while page <= 5:
            data = self.fetch(FAA_URL, params={"icaoLocation": icao, "responseFormat": "geoJson", "pageSize": 200,
                                               "pageNum": page},
                              headers={"client_id": self.keys.get("faa_id"), "client_secret": self.keys.get("faa_secret")})
            items = (data or {}).get("items") or []
            for item in items:
                n = (((item.get("properties") or {}).get("coreNOTAMData") or {}).get("notam") or {})
                rows.append({"id": n.get("number") or n.get("id") or "", "icao": n.get("icaoLocation") or n.get("location") or icao,
                             "text": n.get("text") or "", "start": n.get("effectiveStart") or "",
                             "end": n.get("effectiveEnd") or "", "raw": n.get("icaoMessage") or ""})
            if page >= int((data or {}).get("totalPages") or 1):
                break
            page += 1
        return rows


def _notam(row: dict) -> Notam | None:
    text = str(row.get("text") or "")
    raw = str(row.get("raw") or "")
    if raw and (m := re.search(r"\bE\)\s*(.+?)(?:\s[FG]\)|$)", raw, re.S)):
        text = m.group(1).strip()
    if not text:
        return None
    return Notam(id=str(row.get("id") or ""), icao=str(row.get("icao") or "").upper(), text=text,
                 start=_when(row.get("start")), end=_when(row.get("end")), raw=raw)


def _when(text) -> float | None:
    s = str(text or "").strip()
    if not s or s.upper() in ("PERM", "UFN"):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%y%m%d%H%M"):
        try:
            return datetime.strptime(s.rstrip("EST").strip() if fmt == "%y%m%d%H%M" else s, fmt).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None
