"""Achievements: each one met by what the flight's own events show, awarded once (idempotent: the same flight, or its
replay, never earns it twice), with exactly what met it.

A flight's facts are gathered from its events (``FlightFacts``: the clearance read back right, every readback, the
alerts, a hold flown, a go-around, where it arrived and whether at a gate). The flight-quality ones aren't given to a
flight that broke a rule on the way (an incursion, a takeoff or landing without a clearance, a low altitude alert, a
conditional clearance not kept to): the record says which. The milestones come from the logbook (airports, regions,
aircraft types, airlines, streaks) and the personal bests from its flights (the smoothest landing).

A recording can be evaluated the same way (``facts_from_recording``), so a replay shows what that flight earned.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

UNSAFE = {"runway_incursion", "takeoff_without_clearance", "landing_without_clearance", "low_altitude", "condition"}
CLEAN_READBACKS = 5  # a flight with this many readbacks or more, every one right the first time


@dataclass
class FlightFacts:
    flight: str = ""
    callsign: str = ""
    aircraft: str = ""
    origin: str = ""
    destination: str = ""  # planned
    arrived_at: str = ""
    gate: str = ""
    ifr_cleared: bool = False
    readbacks: list[str] = field(default_factory=list)  # each status, in order
    alerts: list[str] = field(default_factory=list)
    holds_flown: int = 0
    went_around: bool = False
    diverted: bool = False
    landing_vs_fpm: int | None = None
    services_done: bool = False

    @property
    def unsafe(self) -> list[str]:
        return sorted({a for a in self.alerts if a in UNSAFE})

    def observe(self, ev) -> None:
        from localtc.sim_api import AtcAlert, AtcOperations, AtcTransmission, FlightArrived, ReadbackEvaluated

        if isinstance(ev, ReadbackEvaluated):
            self.readbacks.append(ev.status)
            if ev.status == "correct" and ev.instruction_id.startswith("clearance.ifr"):
                self.ifr_cleared = True
        elif isinstance(ev, AtcAlert):
            self.alerts.append(ev.kind)
        elif isinstance(ev, AtcTransmission):
            iid = ev.instruction_id or ""
            if iid.startswith("tower.go_around"):
                self.went_around = True
            if iid.startswith("common.divert"):
                self.diverted = True
        elif isinstance(ev, AtcOperations):
            try:
                hold = (json.loads(ev.data or "{}") or {}).get("hold")
            except ValueError:
                hold = None
            if hold and hold.get("state") == "done" and hold.get("laps", 0) >= 1:
                self.holds_flown = max(self.holds_flown, 1)
        elif isinstance(ev, FlightArrived):
            self.arrived_at, self.gate = ev.airport, ev.gate


@dataclass(frozen=True)
class Achievement:
    id: str
    title: str
    description: str
    kind: str  # "flight": met by one flight; "milestone": by the logbook
    clean: bool = False  # not for a flight that broke a rule


ACHIEVEMENTS = (
    Achievement("first_flight", "First flight", "Finish a flight, parked at the other end.", "flight"),
    Achievement("first_readback", "Read back", "Read back an instruction right.", "flight"),
    Achievement("first_ifr_clearance", "Cleared as filed", "Get an IFR clearance and read it back right.", "flight"),
    Achievement("clean_readbacks", "By the book", f"Read back {CLEAN_READBACKS} or more instructions in a flight, every "
                "one right the first time.", "flight", clean=True),
    Achievement("planned_destination", "As planned", "Land at the destination you planned and park.", "flight", clean=True),
    Achievement("gate_arrival", "On stand", "Taxi in to a gate (not just parking) at the end of a flight.", "flight", clean=True),
    Achievement("safe_diversion", "Diverted safely", "Divert with ATC and land safely somewhere else.", "flight", clean=True),
    Achievement("hold_flown", "Holding pattern", "Fly a hold ATC gave you, at least one lap, and leave it when cleared.",
                "flight", clean=True),
    Achievement("missed_approach", "Go around", "Go around, then come back and land.", "flight", clean=True),
    Achievement("turnaround", "Turnaround", "Fuel, cater and board (GSX) before the push.", "flight", clean=True),
    Achievement("airports_10", "Ten airports", "Fly to or from 10 different airports.", "milestone"),
    Achievement("airports_50", "Fifty airports", "Fly to or from 50 different airports.", "milestone"),
    Achievement("regions_5", "Around the world", "Fly in 5 of the world's ICAO regions.", "milestone"),
    Achievement("types_5", "Type ratings", "Fly 5 different aircraft types.", "milestone"),
    Achievement("airlines_5", "Freelancer", "Fly for 5 different airlines (callsigns).", "milestone"),
    Achievement("flights_25", "Twenty-five", "Finish 25 flights.", "milestone"),
    Achievement("streak_3", "Three days running", "Fly on three days in a row.", "milestone"),
    Achievement("streak_7", "A week of it", "Fly on seven days in a row.", "milestone"),
)
BY_ID = {a.id: a for a in ACHIEVEMENTS}


def met_by(facts: FlightFacts) -> dict[str, str]:
    """The flight achievements these facts meet: {id: what met it}."""
    out: dict[str, str] = {}
    landed_parked = bool(facts.arrived_at)
    if landed_parked:
        out["first_flight"] = f"{facts.origin or '?'} to {facts.arrived_at}"
    if "correct" in facts.readbacks:
        out["first_readback"] = "a readback ATC found right"
    if facts.ifr_cleared:
        out["first_ifr_clearance"] = "the IFR clearance read back right"
    if len(facts.readbacks) >= CLEAN_READBACKS and all(s == "correct" for s in facts.readbacks):
        out["clean_readbacks"] = f"{len(facts.readbacks)} readbacks, all right"
    if landed_parked and facts.destination and facts.arrived_at == facts.destination:
        out["planned_destination"] = f"landed at {facts.destination} as planned"
    if landed_parked and facts.gate and facts.gate.lower().startswith(("gate", "stand")):
        out["gate_arrival"] = f"{facts.gate} at {facts.arrived_at}"
    if landed_parked and facts.diverted and facts.destination and facts.arrived_at != facts.destination:
        out["safe_diversion"] = f"diverted to {facts.arrived_at} instead of {facts.destination}"
    if facts.holds_flown:
        out["hold_flown"] = "a hold flown and left when cleared"
    if facts.went_around and landed_parked:
        out["missed_approach"] = f"went around and landed at {facts.arrived_at}"
    if facts.services_done:
        out["turnaround"] = "fuel, catering and boarding done before the push"
    return out


class Achievements:
    """What's been earned (``achievements.json``): {id: {at, flight, detail}}, and the personal bests."""

    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self.earned: dict[str, dict] = {}
        self.bests: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.listeners: list = []
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.earned = {k: v for k, v in data.get("earned", {}).items() if k in BY_ID}
            self.bests = data.get("bests", {})
        except (OSError, ValueError) as exc:
            log.info("Achievements unreadable: %s", exc)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"earned": self.earned, "bests": self.bests}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.info("Achievements not saved: %s", exc)

    def award(self, facts: FlightFacts) -> list[dict]:
        """The flight's achievements, each awarded once; returns the new ones (with what met them)."""
        new = []
        unsafe = facts.unsafe
        with self._lock:
            for aid, detail in met_by(facts).items():
                a = BY_ID[aid]
                if aid in self.earned:
                    continue
                if a.clean and unsafe:
                    log.info("Achievement %s not given: the flight had %s", aid, ", ".join(unsafe))
                    continue
                self.earned[aid] = {"at": self.clock(), "flight": facts.flight, "detail": detail}
                new.append({"id": aid, "title": a.title, "detail": detail})
            if facts.landing_vs_fpm is not None and facts.arrived_at and not unsafe:
                best = self.bests.get("smoothest_landing")
                if best is None or abs(facts.landing_vs_fpm) < abs(best["fpm"]):
                    self.bests["smoothest_landing"] = {"fpm": facts.landing_vs_fpm, "flight": facts.flight,
                                                       "airport": facts.arrived_at, "at": self.clock()}
                    new.append({"id": "best:smoothest_landing", "title": "Smoothest landing yet",
                                "detail": f"{facts.landing_vs_fpm} fpm at {facts.arrived_at}"})
        if new:
            self._save()
            self._changed()
        return new

    def milestones(self, flights: list) -> list[dict]:
        """The logbook's milestones (``logbook.FlightRecord`` list): awarded once each; returns the new ones."""
        done = [f for f in flights if getattr(f, "landed", False)]
        airports = {a for f in done for a in (f.origin, f.destination) if a}
        regions = {a[0] for a in airports if a}
        types = {f.aircraft for f in done if f.aircraft}
        airlines = {f.callsign[:3] for f in done if len(f.callsign) >= 4 and f.callsign[:3].isalpha()}
        days = sorted({_day(f.started_at) for f in done if _day(f.started_at) is not None})
        streak = _longest_streak(days)
        met = {
            "airports_10": (len(airports) >= 10, f"{len(airports)} airports"),
            "airports_50": (len(airports) >= 50, f"{len(airports)} airports"),
            "regions_5": (len(regions) >= 5, f"regions {', '.join(sorted(regions))}"),
            "types_5": (len(types) >= 5, ", ".join(sorted(types))),
            "airlines_5": (len(airlines) >= 5, ", ".join(sorted(airlines))),
            "flights_25": (len(done) >= 25, f"{len(done)} flights"),
            "streak_3": (streak >= 3, f"{streak} days in a row"),
            "streak_7": (streak >= 7, f"{streak} days in a row"),
        }
        new = []
        with self._lock:
            self._progress = {"airports": len(airports), "regions": len(regions), "types": len(types),
                              "airlines": len(airlines), "flights": len(done), "streak": streak}
            for aid, (ok, detail) in met.items():
                if ok and aid not in self.earned:
                    self.earned[aid] = {"at": self.clock(), "flight": "", "detail": detail}
                    new.append({"id": aid, "title": BY_ID[aid].title, "detail": detail})
        if new:
            self._save()
            self._changed()
        return new

    def _changed(self) -> None:
        for listener in list(self.listeners):
            try:
                listener(self.to_json())
            except Exception:  # noqa: BLE001
                pass

    def to_json(self) -> dict:
        progress = getattr(self, "_progress", {})
        goal = {"airports_10": ("airports", 10), "airports_50": ("airports", 50), "regions_5": ("regions", 5),
                "types_5": ("types", 5), "airlines_5": ("airlines", 5), "flights_25": ("flights", 25),
                "streak_3": ("streak", 3), "streak_7": ("streak", 7)}
        items = []
        for a in ACHIEVEMENTS:
            got = self.earned.get(a.id)
            row = {"id": a.id, "title": a.title, "description": a.description, "kind": a.kind, "earned": got is not None,
                   **({"at": got["at"], "detail": got["detail"], "flight": got.get("flight", "")} if got else {})}
            if a.id in goal and progress:
                key, n = goal[a.id]
                row["progress"] = [min(progress.get(key, 0), n), n]
            items.append(row)
        return {"achievements": items, "bests": self.bests,
                "earned": sum(1 for a in ACHIEVEMENTS if a.id in self.earned), "total": len(ACHIEVEMENTS)}


def facts_from_recording(directory: Path, *, flight: str = "", destination: str = "") -> FlightFacts:
    """A recorded flight's facts from its events, the same as the live flight's."""
    from localtc.replay import Recording

    facts = FlightFacts(flight=flight or Path(directory).name, destination=destination)
    try:
        events = Recording(directory).events()
        for ev in events:
            facts.observe(ev)
    except (OSError, ValueError) as exc:
        log.info("No achievements from %s: %s", directory, exc)
    return facts


def _day(iso: str) -> date | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def _longest_streak(days: list[date]) -> int:
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    return best
