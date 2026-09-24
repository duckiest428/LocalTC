"""The logbook: one line per flight, kept on this computer for everyone, account or not.

``FlightLog`` watches a flight's events (the same ones the recorder writes) and sums it up when the flight
ends: where from and to, which gates and runways, block and air time, distance, the landing, how the
readbacks went. ``Logbook`` keeps those lines in SQLite at ``<data dir>/logbook.db``.

Only these summaries exist here, with the path of each flight's recording, which is what the Logbook's
Replay plays (``localtc.replay.rewatch``). An account (``localtc.account``) syncs the summaries; a flight's
replay (its track and radio transcript) goes to it only when the pilot uploads it. Never audio.
"""

import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from localtc.config import data_dir
from localtc.sim_api import AtcAlert, OwnshipState, PhaseChanged, ReadbackEvaluated
from localtc.sim_api.geo import haversine_nm

MOVING_KT = 3.0  # on the ground: the block starts when the aircraft first moves
MAX_JUMP_NM = 5.0  # a position jump (slew, teleport, a new flight loaded) isn't distance flown
LINK_WITHIN_S = 180.0  # an older line and its recording: started this close together


@dataclass
class FlightRecord:
    id: str
    started_at: str  # ISO 8601, UTC
    ended_at: str
    callsign: str = ""
    aircraft: str = ""
    origin: str = ""
    destination: str = ""
    departure_gate: str = ""
    arrival_gate: str = ""
    departure_runway: str = ""
    arrival_runway: str = ""
    block_min: float | None = None  # first movement to parked
    air_min: float | None = None  # takeoff to touchdown
    distance_nm: float = 0.0  # flown, airborne
    max_alt_ft: int = 0
    landing_vs_fpm: int | None = None  # the vertical speed at touchdown (negative: down)
    readbacks: int = 0
    readbacks_correct: int = 0
    alerts: int = 0
    landed: bool = False
    # The airports' reference points (published data, not where the aircraft was), for the dashboard's map.
    origin_lat: float | None = None
    origin_lon: float | None = None
    destination_lat: float | None = None
    destination_lon: float | None = None
    synced_at: str | None = None  # when an account last took it; None = not synced
    recording: str = ""  # the flight's recording directory on this computer, for its replay ("" = none)
    replay_uploaded_at: str | None = None  # when its replay went to the account; None = not uploaded

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FlightLog:
    """Sums up one flight from its events. ``finish`` gives the record, or None if nothing happened."""

    def __init__(self, started: datetime | None = None) -> None:
        self.started = started or datetime.now(UTC)
        self.t0: float | None = None
        self.last_t = 0.0
        self._prev: OwnshipState | None = None
        self.block_start: float | None = None
        self.block_end: float | None = None
        self.takeoff_t: float | None = None
        self.landing_t: float | None = None
        self.landing_vs: float | None = None
        self.distance_nm = 0.0
        self.max_alt_ft = 0.0
        self.readbacks = self.readbacks_correct = self.alerts = 0

    def feed(self, event: object) -> None:
        t = getattr(event, "t", None)
        if t is None:
            return
        self.t0 = t if self.t0 is None else self.t0
        self.last_t = max(self.last_t, t)
        if isinstance(event, OwnshipState):
            self._own(event)
        elif isinstance(event, PhaseChanged):
            if event.phase == "PARKED" and self.landing_t is not None:
                self.block_end = t
        elif isinstance(event, ReadbackEvaluated):
            self.readbacks += 1
            self.readbacks_correct += event.status == "correct"
        elif isinstance(event, AtcAlert):
            self.alerts += 1

    def _own(self, own: OwnshipState) -> None:
        prev = self._prev
        self._prev = own
        if own.on_ground and own.gs_kt > MOVING_KT and self.block_start is None:
            self.block_start = own.t
        if prev is None:
            return
        if not own.on_ground:
            self.max_alt_ft = max(self.max_alt_ft, own.alt_msl_ft)
            step = haversine_nm(prev.lat, prev.lon, own.lat, own.lon)
            if step < MAX_JUMP_NM:
                self.distance_nm += step
        if prev.on_ground and not own.on_ground and self.takeoff_t is None:
            self.takeoff_t = own.t
            self.block_start = self.block_start if self.block_start is not None else own.t
        elif not prev.on_ground and own.on_ground and self.takeoff_t is not None:
            self.landing_t, self.landing_vs = own.t, prev.vs_fpm  # the last airborne sample: the touchdown rate
            self.block_end = None  # a touch and go, or the real landing: the block ends at the next parking

    def finish(self, engine: Any = None) -> FlightRecord | None:
        if self.t0 is None or (self.block_start is None and self.takeoff_t is None):
            return None  # never moved: not a flight
        st = getattr(engine, "state", None)
        a = st.assignments if st is not None else None
        f = st.flight if st is not None else None
        callsign = f.callsign if f is not None else None
        end = self.block_end if self.block_end is not None else self.last_t

        def minutes(start: float | None, stop: float | None) -> float | None:
            return round((stop - start) / 60.0, 1) if start is not None and stop is not None else None

        return FlightRecord(
            id=str(uuid.uuid4()),
            started_at=_iso(self.started),
            ended_at=_iso(self.started + timedelta(seconds=self.last_t - self.t0)),
            callsign=(callsign.ident if callsign is not None else "") or "",
            aircraft=(f.aircraft_type if f is not None else "") or "",
            origin=(f.origin if f is not None else "") or "",
            destination=(f.destination if f is not None else "") or "",
            departure_gate=(a.departure_gate if a is not None else "") or "",
            arrival_gate=(a.gate if a is not None else "") or "",
            departure_runway=(a.departure_runway if a is not None else "") or "",
            arrival_runway=(a.arrival_runway if a is not None else "") or "",
            block_min=minutes(self.block_start, end),
            air_min=minutes(self.takeoff_t, self.landing_t),
            distance_nm=round(self.distance_nm, 1),
            max_alt_ft=round(self.max_alt_ft),
            landing_vs_fpm=round(self.landing_vs) if self.landing_vs is not None else None,
            readbacks=self.readbacks,
            readbacks_correct=self.readbacks_correct,
            alerts=self.alerts,
            landed=self.landing_t is not None,
            **_airport_points(engine, f.origin if f is not None else None, "origin"),
            **_airport_points(engine, f.destination if f is not None else None, "destination"),
        )


def _airport_points(engine: Any, icao: str | None, prefix: str) -> dict[str, float]:
    geo = engine.geometry(icao) if engine is not None and icao else None
    if geo is None:
        return {}
    return {f"{prefix}_lat": round(geo.airport.lat, 4), f"{prefix}_lon": round(geo.airport.lon, 4)}


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_COLUMNS = [f.name for f in fields(FlightRecord)]
_TYPES = {"origin_lat": "REAL", "origin_lon": "REAL", "destination_lat": "REAL", "destination_lon": "REAL",
          "block_min": "REAL", "air_min": "REAL", "distance_nm": "REAL", "max_alt_ft": "INTEGER",
          "landing_vs_fpm": "INTEGER", "readbacks": "INTEGER", "readbacks_correct": "INTEGER", "alerts": "INTEGER",
          "landed": "INTEGER"}


class Logbook:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "logbook.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            columns = ", ".join(f"{c} {_TYPES.get(c, 'TEXT')}{' PRIMARY KEY' if c == 'id' else ''}" for c in _COLUMNS)
            db.execute(f"CREATE TABLE IF NOT EXISTS flights ({columns})")
            db.execute("CREATE INDEX IF NOT EXISTS flights_started ON flights (started_at)")
            have = {row[1] for row in db.execute("PRAGMA table_info(flights)")}
            for c in _COLUMNS:  # a logbook from an older LocalTC gains the newer columns
                if c not in have:
                    db.execute(f"ALTER TABLE flights ADD COLUMN {c} {_TYPES.get(c, 'TEXT')}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def add(self, record: FlightRecord) -> None:
        values = record.to_dict()
        with closing(self._connect()) as db, db:
            db.execute(f"INSERT OR REPLACE INTO flights ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                       [values[c] for c in _COLUMNS])

    def flights(self, limit: int = 500) -> list[FlightRecord]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM flights ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [_record(row) for row in rows]

    def get(self, flight_id: str) -> FlightRecord | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM flights WHERE id = ?", (flight_id,)).fetchone()
        return _record(row) if row else None

    def delete(self, flight_id: str) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM flights WHERE id = ?", (flight_id,)).rowcount > 0

    def unsynced(self) -> list[FlightRecord]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM flights WHERE synced_at IS NULL ORDER BY started_at").fetchall()
        return [_record(row) for row in rows]

    def mark_synced(self, ids: list[str], when: str | None = None) -> None:
        when = when or _iso(datetime.now(UTC))
        with closing(self._connect()) as db, db:
            db.executemany("UPDATE flights SET synced_at = ? WHERE id = ?", [(when, i) for i in ids])

    def forget_sync(self) -> None:
        """Signed out, or the account was deleted: every flight is local-only again."""
        with closing(self._connect()) as db, db:
            db.execute("UPDATE flights SET synced_at = NULL, replay_uploaded_at = NULL")

    def mark_replay(self, flight_id: str, when: str | None) -> None:
        """Its replay went to the account (``when``), or came off it (None)."""
        with closing(self._connect()) as db, db:
            db.execute("UPDATE flights SET replay_uploaded_at = ? WHERE id = ?", (when, flight_id))

    def link_recordings(self, recordings: Path) -> int:
        """Lines from before the logbook kept the recording's path: matched to a live recording that started
        within a few minutes of the flight. Returns how many were linked."""
        missing = [f for f in self.flights(limit=1_000_000) if not f.recording]
        if not missing or not recordings.is_dir():
            return 0
        starts = []
        for folder in recordings.iterdir():
            if not folder.is_dir() or not folder.name.endswith("_live"):
                continue
            try:
                starts.append((_created(folder), folder))
            except (OSError, ValueError):
                continue  # not a recording, or a broken one
        linked = []
        for f in missing:
            began = datetime.fromisoformat(f.started_at)
            near = [(abs((when - began).total_seconds()), folder) for when, folder in starts]
            best = min(near, default=None, key=lambda pair: pair[0])
            if best is not None and best[0] <= LINK_WITHIN_S:
                linked.append((str(best[1].resolve()), f.id))
        with closing(self._connect()) as db, db:
            db.executemany("UPDATE flights SET recording = ? WHERE id = ?", linked)
        return len(linked)

    def totals(self) -> dict[str, Any]:
        return totals(self.flights(limit=1_000_000))


def totals(flights: list[FlightRecord]) -> dict[str, Any]:
    airports = {icao for f in flights for icao in (f.origin, f.destination) if icao}
    landings = [f.landing_vs_fpm for f in flights if f.landing_vs_fpm is not None]
    readbacks = sum(f.readbacks for f in flights)
    return {
        "flights": len(flights),
        "air_hours": round(sum(f.air_min or 0 for f in flights) / 60.0, 1),
        "block_hours": round(sum(f.block_min or 0 for f in flights) / 60.0, 1),
        "distance_nm": round(sum(f.distance_nm for f in flights)),
        "airports": sorted(airports),
        "landings": sum(f.landed for f in flights),
        "average_landing_fpm": round(sum(landings) / len(landings)) if landings else None,
        "readback_accuracy": round(sum(f.readbacks_correct for f in flights) / readbacks, 3) if readbacks else None,
    }


def _created(folder: Path) -> datetime:
    """When a recording started, from its header (the first line; nothing else is read)."""
    from localtc.replay.reader import Recording

    return datetime.fromisoformat(Recording(folder).header.created).astimezone(UTC)


def _record(row: sqlite3.Row) -> FlightRecord:
    values = dict(row)
    values["landed"] = bool(values["landed"])
    return FlightRecord(**values)
