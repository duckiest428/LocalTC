"""The turnaround as a timeline: what has to happen before the push, how long each takes and why, what waits on what, and
how that stands against the plan's off-block time.

Twelve milestones (``ORDER``). Each one's duration comes from the flight: the passengers and doors for boarding, the fuel
still to load for the fuelling, the seats for the cleaning, the airline's kind of operation (``PROFILES``), the size of the
airport. The only variation is ``variation``: at most 8 % either way, seeded from the flight, and shown with the reason, so
a flight flown again has the same day. Nothing is delayed at random.

**Against the schedule.** Worked back from the off-block time: the latest each can start and still make it (``latest``),
and when its crew can first be there (``opens``: a truck has a slot for this departure, not a standing invitation). Worked
forward from now: when each will really be done (``eta``) and which one is holding the flight up. A plan too far from the
sim's clock (``crew.turnaround.HELD_TO_S``) isn't a schedule anyone's held to: the durations stand, the windows and the
lateness don't.

**What's seen and what's modelled.** GSX's services are read as GSX has them. What nothing reports (the baggage, the crew's
arrival; every service without GSX) runs on the clock and says so (``source`` "modelled").

``[turnaround] mode``: "realistic" holds a service asked for before its slot or ahead of what it depends on (asked twice,
the crew find a way); "advisory" only says; "off" none of this.
"""

import json
import logging
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from localtc.crew.cabin_data import airline_of, seats
from localtc.crew.turnaround import off_block_in
from localtc.sim_api import (
    AircraftVars,
    AirportData,
    AtcTransmission,
    CabinIncident,
    FlightArrived,
    GroundStatus,
    OwnshipState,
    PhaseChanged,
)

log = logging.getLogger(__name__)

ORDER = ("parked", "deboarding", "cleaning", "catering", "fueling", "baggage", "crew", "boarding", "doors", "clearance",
         "deicing", "ready")
LABELS = {"parked": "Parked, doors available", "deboarding": "Deboarding", "cleaning": "Cabin cleaning", "catering": "Catering",
          "fueling": "Fueling", "baggage": "Baggage and cargo", "crew": "Crew arrival", "boarding": "Boarding",
          "doors": "Doors closed", "clearance": "Pushback clearance", "deicing": "De-icing", "ready": "Ready for departure"}
SAID = {"parked": "the doors", "deboarding": "deboarding", "cleaning": "cleaning", "catering": "catering", "fueling": "fueling",
        "baggage": "the bags", "crew": "the crew", "boarding": "boarding", "doors": "the doors",
        "clearance": "the push clearance", "deicing": "de-icing", "ready": "departure"}
DEPENDS = {"deboarding": ("parked",), "cleaning": ("deboarding",), "catering": ("deboarding",), "fueling": ("parked",),
           "baggage": ("parked",), "crew": (), "boarding": ("cleaning", "catering", "crew"),
           "doors": ("boarding", "baggage", "fueling"), "clearance": ("doors",), "deicing": ("clearance",),
           "ready": ("clearance", "deicing"), "parked": ()}
BEFORE_PUSH = ("deboarding", "cleaning", "catering", "fueling", "baggage", "crew", "boarding", "doors", "clearance")
GSX_SERVICE = {"Deboarding": "deboarding", "Cleaning": "cleaning", "Catering": "catering", "Refueling": "fueling",
               "Boarding": "boarding", "DeIce": "deicing"}
SERVICE_OF = {v: k for k, v in GSX_SERVICE.items()}
GSX_ACTIVE = ("requested", "performing", "completing")
GROUPS = {"ramp": ("deboarding", "cleaning", "catering", "fueling", "baggage", "deicing"),
          "cabin": ("crew", "boarding", "doors"), "flight": ORDER}
WORDS = {"baggage": r"\b(?:bags?|baggage|luggage|cargo|hold)\b", "catering": r"\bcater", "fueling": r"\bfuel",
         "cleaning": r"\bclean", "boarding": r"(?<!de)\bboarding\b", "deboarding": r"\bde-?boarding\b", "crew": r"\bcrew\b",
         "doors": r"\bdoors?\b", "clearance": r"\bclearance\b", "deicing": r"\bde-?ic"}

GRACE_S = 120.0  # past the latest start by this much before anyone says it's late
TIGHT_S = 300.0  # the forecast with less in hand than this: tight
LATE_S = 180.0  # the forecast this far past the off-block time: late
REMIND_EVERY_S = 420.0  # the copilot on the turnaround, no more often than this
REMIND_AFTER_S = 180.0  # ... and not in the first minutes of the flight (it starts with things as they are)
REMINDED = ("cleaning", "catering", "fueling", "boarding")  # what the copilot reminds you of (the doors and the clearance: its own calls)
RATE_FROM = 0.15  # a head count says how fast boarding's going once this much of it is done (the first few aren't the pace)
WAIVE_S = 300.0  # asked again within this long: they come early
VARIATION = 8  # percent
PUBLISH_S = 30.0


@dataclass(frozen=True)
class Profile:
    """An airline's way of turning an aircraft round: passengers a minute per door, how much cleaning and catering it
    does, when its crew report, and how far ahead of need the ramp's slots open."""

    id: str
    label: str
    board_ppm: float = 14.0
    deboard_ppm: float = 20.0
    clean_per_100: float = 6.0
    catering_min: float = 16.0
    report_min: float = 60.0
    early_min: float = 30.0


PROFILES = {
    "full_service": Profile("full_service", "Full service"),
    "low_cost": Profile("low_cost", "Low cost: quick turns", board_ppm=18.0, deboard_ppm=24.0, clean_per_100=3.0, catering_min=6.0,
                        report_min=45.0, early_min=15.0),
    "regional": Profile("regional", "Regional", board_ppm=12.0, deboard_ppm=18.0, clean_per_100=5.0, catering_min=8.0,
                        report_min=45.0, early_min=20.0),
    "cargo": Profile("cargo", "Cargo", board_ppm=0.0, deboard_ppm=0.0, clean_per_100=0.0, catering_min=0.0, report_min=60.0,
                     early_min=60.0),
}
LOW_COST = {"RYR", "EZY", "WZZ", "VLG", "SWA", "NKS", "FFT", "AXM", "IGO", "JST", "TRA", "NOZ", "PGT", "SXS", "VOZ", "TGW", "EJU"}
CARGO = {"FDX", "UPS", "GTI", "ABX", "CLX", "BOX", "DHK", "ATN", "CKS", "PAC", "KZR"}
WIDE_SEATS = 230  # this many seats and up: two doors in use, two belts, a big fuel truck


def profile_for(setting: str, callsign: str, seat_count: int) -> Profile:
    """``[turnaround] profile``, or on "auto" what the airline's known to be (a regional from its seats)."""
    if setting in PROFILES:
        return PROFILES[setting]
    code = airline_of(callsign)[0]
    if code in CARGO:
        return PROFILES["cargo"]
    if code in LOW_COST:
        return PROFILES["low_cost"]
    return PROFILES["regional" if 0 < seat_count <= 100 else "full_service"]


@dataclass(frozen=True)
class Facts:
    """What the timing's worked out from."""

    callsign: str = ""
    aircraft: str = ""
    pax: int = 0
    block_fuel_lb: float = 0.0
    sched_out_s: int = 0
    sched_in_s: int = 0
    seed: int = 0
    gates: int = 0  # at the airport (0: not known yet)


def variation(seed: int, callsign: str, milestone: str) -> float:
    """This flight's day-to-day difference on a milestone: at most ``VARIATION`` percent either way, always the same."""
    return 1.0 + (zlib.crc32(f"{seed}|{callsign}|{milestone}".encode()) % (2 * VARIATION + 1) - VARIATION) / 100.0


def durations(f: Facts, p: Profile, fuel_now_lb: float | None, *, inbound: bool = False) -> dict[str, tuple[float, str]]:
    """Each milestone's minutes and the reason for them, before the day's variation."""
    seat_count = seats(f.aircraft) or 150
    pax = f.pax or round(seat_count * 0.85)
    wide = seat_count >= WIDE_SEATS
    doors = 2 if wide else 1
    site = 1.0
    where = ""
    if f.gates >= 60:
        site, where = 1.08, ", and a big airport's distances"
    elif 0 < f.gates < 8:
        site, where = 1.1, ", and a small airport's one truck"
    out: dict[str, tuple[float, str]] = {}
    if p.board_ppm:
        out["boarding"] = (pax / (p.board_ppm * doors) + 4,
                           f"{pax} passengers through {doors} door{'s' if doors > 1 else ''}, {p.board_ppm:g} a minute a door")
        out["deboarding"] = (pax / (p.deboard_ppm * doors) + 3, f"{pax} passengers, {doors} door{'s' if doors > 1 else ''}")
        out["cleaning"] = (seat_count / 100 * p.clean_per_100 + 3, f"{seat_count} seats, a {p.label.split(':')[0].lower()} clean")
        long_haul = f.sched_in_s - f.sched_out_s >= 4 * 3600 if f.sched_in_s and f.sched_out_s else wide
        out["catering"] = ((p.catering_min * (1.8 if long_haul else 1.0)) * site,
                           ("a long-haul load" if long_haul else "a short-haul load") + where)
    else:
        out["boarding"] = (0.0, "cargo: no passengers")
        out["deboarding"] = (0.0, "cargo: no passengers")
        out["cleaning"] = (0.0, "cargo: no cabin")
        out["catering"] = (0.0, "cargo: crew meals only")
    rate = 2800.0 if wide else 1100.0  # lb a minute
    if f.block_fuel_lb and fuel_now_lb is not None:
        need = max(0.0, f.block_fuel_lb - fuel_now_lb)
        out["fueling"] = (need / rate * site + (5 if need > 200 else 0), f"{need:,.0f} lb to load at {rate:,.0f} a minute{where}")
    else:
        out["fueling"] = ((35.0 if wide else 16.0) * site, f"the usual uplift, the plan gives no block fuel{where}")
    belts = 2 if wide else 1
    bags = round(pax * 1.1)
    out["baggage"] = (bags / (14.0 * belts) + 6, f"about {bags} bags on {belts} belt{'s' if belts > 1 else ''}" if p.board_ppm else "the freight")
    out["crew"] = (0.0, "")
    out["doors"] = (4.0, "the last checks and the paperwork")
    out["clearance"] = (2.0, "a call to ground")
    out["deicing"] = (20.0 if wide else 12.0, "a wide body's wings" if wide else "the wings and tail")
    out["ready"] = (1.0, "the push and the start")
    out["parked"] = (0.0, "")
    if not inbound:
        out["deboarding"] = (0.0, "")
    return out


@dataclass
class Milestone:
    id: str
    state: str = "pending"  # pending, in_progress, done, skipped
    source: str = "modelled"  # gsx, sim, atc, modelled
    started: float | None = None
    done_at: float | None = None
    minutes: float = 0.0
    why: str = ""
    latest: float | None = None  # the latest it can start and the push be on time
    opens: float | None = None  # when its crew can first be there
    eta: float | None = None
    reason: str = ""  # why it'll be done then
    blocked_by: tuple[str, ...] = ()
    late: bool = False
    progress: tuple[int, int] | None = None  # (on board, of how many)

    @property
    def finished(self) -> bool:
        return self.state in ("done", "skipped")


class Turnaround:
    """The tracker: events in, the timeline out. Pure: the same events make the same timeline."""

    def __init__(self, facts: Facts, *, mode: str = "realistic", profile: str = "auto") -> None:
        self.facts, self.mode, self.profile_setting = facts, mode, profile
        self.profile = profile_for(profile, facts.callsign, seats(facts.aircraft))
        self.ms: dict[str, Milestone] = {m: Milestone(m) for m in ORDER}
        self.role = "departure"
        self.changed = True
        self.now = 0.0
        self.zulu: float | None = None
        self.phase = ""
        self.gsx = False
        self.fuel_lb: float | None = None
        self.deice_needed = False
        self.std: float | None = None  # the off-block time, on the event clock (None: not held to one)
        self.crew_at: float | None = None
        self.extra: dict[str, tuple[float, str]] = {}  # a hold-up with a reason: {milestone: (seconds, why)}
        self._asked: dict[str, float] = {}
        self._waived: set[str] = set()
        self._reminded: dict[str, float] = {}
        self._doors: dict[str, float] = {}
        self._inbound = False
        self.ms["deboarding"].state, self.ms["deboarding"].why = "skipped", "no passengers to get off: the flight starts here"

    def refresh(self, facts: Facts) -> None:
        """The callsign and the aircraft, once the sim's said."""
        self.facts = _with(facts, gates=self.facts.gates)
        self.profile = profile_for(self.profile_setting, facts.callsign, seats(facts.aircraft))

    # --- what happens ------------------------------------------------------------------------------------------------

    def observe(self, ev: Any) -> None:  # noqa: C901 - one reading per kind of event
        if isinstance(ev, OwnshipState):
            self._own(ev)
        elif isinstance(ev, GroundStatus):
            if ev.provider == "gsx" and ev.connected and not self.gsx:
                for m in ("cleaning", "catering", "fueling", "boarding"):  # GSX's now: what it says, not the clock
                    if self.ms[m].state == "in_progress" and self.ms[m].source == "modelled":
                        self.ms[m].state, self.ms[m].started = "pending", None
            self.gsx = ev.provider == "gsx" and ev.connected
            for s in ev.services:
                self._service(s, ev.t)
        elif isinstance(ev, AircraftVars):
            self._doors.update({k: float(v) for k, v in ev.values.items() if k.startswith("INTERACTIVE POINT OPEN")})
        elif isinstance(ev, PhaseChanged):
            self.phase = ev.phase
            if ev.phase in ("PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF"):
                self._off_blocks(ev.t, ev.phase)
        elif isinstance(ev, AtcTransmission):
            if ev.instruction_id.startswith(("ground.pushback", "ground.startup")):
                self._finish("clearance", ev.t, "atc")
        elif isinstance(ev, AirportData):
            if ev.airport.parking and not self.facts.gates:
                self.facts = _with(self.facts, gates=sum(1 for p in ev.airport.parking if p.kind.startswith("gate")))
                self.changed = True
        elif isinstance(ev, FlightArrived):
            self._arrived(ev.t)
        elif isinstance(ev, CabinIncident):
            self._incident(ev)

    def _own(self, own: OwnshipState) -> None:
        self.now, self.zulu = own.t, own.zulu_s
        if self.fuel_lb is None and own.on_ground:
            self.fuel_lb = own.fuel_lb
        to_go = off_block_in(self.facts.sched_out_s, own.zulu_s)
        self.std = None if to_go is None else own.t + to_go
        if own.on_ground and not self.ms["parked"].finished and own.gs_kt < 1.0:
            self._finish("parked", own.t, "sim")
        if own.temperature_c is not None and own.temperature_c <= 3.0 \
                and (own.precip or own.in_cloud or (own.visibility_m is not None and own.visibility_m < 1600)):
            self.deice_needed = True
        if self.std is not None and self.crew_at is None and self.role == "departure":
            # A crew reports a set time before the push, give or take a few minutes.
            shift = (zlib.crc32(f"{self.facts.seed}|{self.facts.callsign}|crew".encode()) % 9 - 4) * 60.0
            self.crew_at = self.std - self.profile.report_min * 60.0 + shift
        self.plan(own.t)  # (what's nothing to do known before the clock starts anything)
        self._clocked(own.t)
        self.plan(own.t)

    def _clocked(self, t: float) -> None:
        """What nothing reports runs on the clock: the crew, and without GSX the ramp's services."""
        crew = self.ms["crew"]
        if not crew.finished and (self.crew_at is None or t >= self.crew_at):
            self._finish("crew", t, "modelled")
        modelled = ["baggage"] + ([] if self.gsx else ["cleaning", "catering", "fueling", "boarding"])
        if self.role != "departure" or self.phase in ("PUSHBACK", "TAXI_OUT"):
            return
        for m in modelled:
            ms = self.ms[m]
            if ms.finished:
                continue
            if ms.state == "pending" and all(self.ms[d].finished for d in DEPENDS[m]) and (ms.opens is None or t >= ms.opens):
                if m == "baggage" or self.std is not None:  # (no schedule, no GSX: nothing's going on that we could know of)
                    ms.state, ms.started, ms.source = "in_progress", t, "modelled"
                    self.changed = True
            elif ms.state == "in_progress" and ms.source == "modelled" and ms.started is not None \
                    and t - ms.started >= self._seconds(m):
                self._finish(m, t, "modelled")

    def _service(self, s: Any, t: float) -> None:
        m = GSX_SERVICE.get(s.id)
        if m is None or self.role == "arrival" and m != "deboarding":
            return
        ms = self.ms[m]
        if s.state in GSX_ACTIVE:
            if ms.state != "in_progress" or ms.source != "gsx":
                ms.state, ms.source, ms.started = "in_progress", "gsx", ms.started if ms.source == "gsx" and ms.started else t
                self.changed = True
                if m == "deboarding":
                    self._inbound = True
                if m == "boarding":  # the aircraft's being filled: what wasn't called isn't coming
                    for dep in ("cleaning", "catering"):
                        if self.ms[dep].state == "pending":
                            self.ms[dep].state, self.ms[dep].why = "skipped", "not called before boarding"
            if found := re.search(r"\bpax (\d+)/(\d+)", s.status or ""):
                ms.progress = (int(found[1]), int(found[2]))
        elif s.state == "completed" and ms.state != "done":
            self._finish(m, t, "gsx")
        elif s.state in ("unavailable", "bypassed") and ms.state == "pending" and m != "deboarding":
            ms.state, ms.why, self.changed = "skipped", f"not at this {'stand' if s.state == 'unavailable' else 'turnaround'}", True

    def _finish(self, m: str, t: float, source: str) -> None:
        ms = self.ms[m]
        if ms.state == "done":
            return
        ms.state, ms.done_at, ms.source, ms.late = "done", t, source, False
        ms.started = ms.started if ms.started is not None else t
        self.changed = True

    def _off_blocks(self, t: float, phase: str) -> None:
        for m in BEFORE_PUSH:
            if m == "clearance" or not self.ms[m].finished:
                ms = self.ms[m]
                if m in ("doors", "clearance"):
                    self._finish(m, t, "sim")
                elif ms.state == "in_progress":
                    self._finish(m, t, ms.source)
                elif not ms.finished:
                    ms.state, ms.why = "skipped", "not done before the push"
        if not self.deice_needed and self.ms["deicing"].state == "pending":
            self.ms["deicing"].state, self.ms["deicing"].why = "skipped", "no icing conditions"
        if phase != "PUSHBACK":
            if self.ms["deicing"].state == "pending":
                self.ms["deicing"].state, self.ms["deicing"].why = "skipped", "not done before the taxi"
            self._finish("ready", t, "sim")
        self.changed = True

    def _arrived(self, t: float) -> None:
        """After landing the timeline is the arrival's: the doors and the passengers off."""
        self.role, self.std, self._inbound, self.crew_at = "arrival", None, True, None
        self.ms = {m: Milestone(m, state="pending" if m in ("parked", "deboarding") else "skipped") for m in ORDER}
        self.changed = True

    def _incident(self, ev: Any) -> None:
        """A cabin problem at the gate holds the boarding up, with the reason and a length that comes from it."""
        if ev.stage == "decided" and ev.delay_s:
            m = "deboarding" if self.role == "arrival" else "boarding"
            self.extra[m] = (ev.delay_s, ev.why or "a cabin problem")
            self.changed = True

    # --- the timeline ------------------------------------------------------------------------------------------------

    def _seconds(self, m: str) -> float:
        base = durations(self.facts, self.profile, self.fuel_lb, inbound=self._inbound).get(m, (0.0, ""))[0]
        extra = self.extra.get(m, (0.0, ""))[0]
        return base * 60.0 * variation(self.facts.seed, self.facts.callsign, m) + extra

    def plan(self, now: float) -> None:
        """The latest each can start, when each can start, and when each will be done."""
        table = durations(self.facts, self.profile, self.fuel_lb, inbound=self._inbound)
        needed = {m for m in ORDER if not (m == "deicing" and not self.deice_needed)}
        for m in ORDER:
            ms = self.ms[m]
            ms.minutes = self._seconds(m) / 60.0
            base = table.get(m, (0.0, ""))[1]
            shift = round((variation(self.facts.seed, self.facts.callsign, m) - 1.0) * 100)
            if not ms.finished or ms.state == "done":
                ms.why = base + (f" ({shift:+d}% on the day)" if shift and ms.minutes else "") \
                    + (f"; {self.extra[m][1]}" if m in self.extra else "")
            if m not in needed and ms.state == "pending":
                ms.state, ms.why = "skipped", "no icing conditions"
            elif ms.state == "pending" and ms.minutes <= 0.0 and m in ("cleaning", "catering", "fueling", "boarding", "baggage"):
                ms.state, ms.why = "skipped", "the fuel's already on board" if m == "fueling" else base or "nothing to do"
        self._backward()
        self._forward(now)

    def _backward(self) -> None:
        for ms in self.ms.values():
            ms.latest = ms.opens = None
        if self.std is None or self.role != "departure":
            return
        latest_end: dict[str, float] = {"clearance": self.std}
        for m in reversed(BEFORE_PUSH):
            ms = self.ms[m]
            end = latest_end.get(m, self.std)
            ms.latest = end - ms.minutes * 60.0
            for d in DEPENDS[m]:
                latest_end[d] = min(latest_end.get(d, self.std), ms.latest)
            if m in ("cleaning", "catering", "fueling", "baggage"):
                ms.opens = ms.latest - self.profile.early_min * 60.0
            elif m == "boarding":
                ms.opens = ms.latest - 10.0 * 60.0

    def _forward(self, now: float) -> None:
        for m in ORDER:
            ms = self.ms[m]
            if ms.state == "done":
                ms.eta, ms.reason, ms.blocked_by, ms.late = ms.done_at, "done", (), False
                continue
            if ms.state == "skipped":
                ms.eta, ms.reason, ms.blocked_by, ms.late = None, ms.why, (), False
                continue
            waits = [d for d in DEPENDS[m] if not self.ms[d].finished]
            ms.blocked_by = tuple(waits) if ms.state == "pending" else ()
            if ms.state == "in_progress":
                ms.eta, ms.reason = self._running(ms, now)
            elif m == "crew":
                ms.eta = self.crew_at if self.crew_at is not None else now
                ms.reason = f"they report {self.profile.report_min:.0f} minutes before the push"
            else:
                start, why = now, ms.why
                depended = [(self.ms[d].eta or now, d) for d in waits]
                if depended and max(depended)[0] > start:
                    start, d = max(depended)
                    why = f"after {SAID[d]}, then {ms.why}" if ms.why else f"after {SAID[d]}"
                if ms.opens is not None and ms.opens > start and m not in self._waived and self.mode == "realistic":
                    start, why = ms.opens, f"their slot opens then, and {ms.why}" if ms.why else "their slot opens then"
                ms.eta, ms.reason = start + ms.minutes * 60.0, why
            before = ms.late
            due = ms.latest if ms.state == "pending" else None
            ms.late = self.std is not None and due is not None and now > due + GRACE_S
            if ms.state == "in_progress" and self.std is not None and ms.latest is not None:
                ms.late = (ms.eta or now) > ms.latest + ms.minutes * 60.0 + GRACE_S
            self.changed = self.changed or before != ms.late

    def _running(self, ms: Milestone, now: float) -> tuple[float, str]:
        """How long an under-way service has left: from the head count where there is one (its measured rate), else from
        its usual time and how long it's been going."""
        if ms.progress and ms.progress[1] and ms.started is not None and now - ms.started > 90.0 \
                and ms.progress[0] >= RATE_FROM * ms.progress[1]:
            on, total = ms.progress
            rate = on / max(now - ms.started, 1.0)
            return now + (total - on) / max(rate, 0.02), f"{on} of {total} so far, {rate * 60:.0f} a minute"
        started = ms.started if ms.started is not None else now
        return max(started + ms.minutes * 60.0, now + 60.0), ms.why or "under way"

    def driver(self) -> Milestone | None:
        """What the push is waiting on: the unfinished milestone before it that'll be done last."""
        left = [self.ms[m] for m in BEFORE_PUSH if m not in ("doors", "clearance") and not self.ms[m].finished and self.ms[m].eta is not None]
        return max(left, key=lambda ms: ms.eta) if left else None

    def forecast(self) -> float | None:
        ms = self.ms["clearance"]
        return ms.eta if not ms.finished else ms.done_at

    def status(self) -> tuple[str, float | None]:
        """("on_time", "tight", "late" or "unheld", the seconds in hand: minus when late)."""
        at = self.forecast()
        if self.std is None or at is None or self.role != "departure":
            return "unheld", None
        spare = self.std - at
        return ("late" if spare < -LATE_S else "tight" if spare < TIGHT_S else "on_time"), spare

    # --- what the crews say ------------------------------------------------------------------------------------------

    def clock(self, at: float | None) -> str:
        """An event-clock moment as "2320" Zulu."""
        if at is None or self.zulu is None:
            return "soon"
        z = (self.zulu + (at - self.now)) % 86400
        return f"{int(z // 3600):02d}{int(z % 3600 // 60):02d}"

    def line(self, m: str) -> str:
        """One milestone, with when and why."""
        ms, name = self.ms[m], LABELS[m]
        if ms.state == "done":
            return f"{name}: done."
        if ms.state == "skipped":
            return f"{name}: not needed, {ms.why}."
        if ms.state == "in_progress":
            return f"{name}: under way, done about {self.clock(ms.eta)}. {_cap(ms.reason)}."
        if ms.blocked_by:
            return f"{name}: waiting on {SAID[ms.blocked_by[0]]}, done about {self.clock(ms.eta)}."
        if m == "crew":
            return f"{name}: due about {self.clock(ms.eta)}, {ms.reason}."
        return f"{name}: not started, done about {self.clock(ms.eta)}. {_cap(ms.reason)}."

    def overall(self) -> str:
        state, spare = self.status()
        drive = self.driver()
        why = f" Waiting on {SAID[drive.id]}: {drive.reason}." if drive is not None else ""
        if state == "unheld":
            return f"We're not held to a schedule. Ready to push about {self.clock(self.forecast())}.{why}"
        if state == "late":
            return f"We're about {round(-spare / 60)} minutes behind, push about {self.clock(self.forecast())}.{why}"
        if state == "tight":
            return f"It's tight, {max(0, round(spare / 60))} minutes in hand for a push at {self.clock(self.std)}.{why}"
        return f"On time for a push at {self.clock(self.std)}, with {round(spare / 60)} minutes in hand.{why}"

    def answer(self, group: str, text: str = "") -> str:
        """A crew group asked how it's going: the milestone named, else theirs (the flight deck: the whole picture)."""
        named = [m for m, word in WORDS.items() if re.search(word, text, re.I)]
        if named:
            return " ".join(self.line(m) for m in named)
        if group == "flight":
            return self.overall()
        lines = [self.line(m) for m in GROUPS[group] if not self.ms[m].finished][:3]
        return " ".join(lines) or "All done on our side."

    def reminder(self, t: float) -> tuple[str, str] | None:
        """The copilot's word when the operation's late: (a key to say it once by, the words)."""
        if self.role != "departure" or self.std is None or self.phase not in ("", "PARKED"):
            return None
        first = self._reminded.setdefault("start", t)
        if t - first < REMIND_AFTER_S or t - self._reminded.get("last", -1e9) < REMIND_EVERY_S:
            return None
        for m in REMINDED:
            ms = self.ms[m]
            if ms.late:
                self._reminded["last"] = t
                behind = max(1, round((t - (ms.latest or t)) / 60))
                if ms.state == "in_progress":
                    return m, f"{_cap(SAID[m])} is running late, done about {self.clock(ms.eta)}. {_cap(ms.reason)}."
                wait = f" It's waiting on {SAID[ms.blocked_by[0]]}." if ms.blocked_by else ""
                return m, f"{_cap(SAID[m])} should have started {behind} minutes ago.{wait} At this rate we push about {self.clock(self.forecast())}."
        return None

    def hold(self, service: str, t: float, *, ask: bool = True) -> str | None:
        """Why the ramp can't bring ``service`` now, or None. Asked again soon after, they come anyway (``ask`` False: only
        looking, for a service waiting its turn)."""
        m = GSX_SERVICE.get(service)
        if self.mode != "realistic" or m is None or self.role != "departure" or m in self._waived:
            return None
        ms = self.ms[m]
        reason = None
        if self.std is not None and ms.opens is not None and t < ms.opens:
            reason = f"{LABELS[m]}'s slot for this departure opens about {self.clock(ms.opens)}."
        elif waits := [d for d in DEPENDS[m] if not self.ms[d].finished and self.ms[d].state != "pending" or d == "crew" and not self.ms[d].finished]:
            first = waits[0]
            reason = f"The crew aren't here yet, due about {self.clock(self.ms['crew'].eta)}." if first == "crew" \
                else f"{LABELS[m]} has to wait for {SAID[first]}, done about {self.clock(self.ms[first].eta)}."
        if reason is None:
            return None
        if ask:
            if t - self._asked.get(m, -1e9) <= WAIVE_S and m in self._asked:
                self._waived.add(m)
                self.changed = True
                return None
            self._asked[m] = t
        return reason

    def to_json(self) -> dict:
        state, spare = self.status()
        drive = self.driver()
        return {
            "role": self.role, "mode": self.mode, "profile": self.profile.id, "profile_label": self.profile.label,
            "status": state, "spare_min": None if spare is None else round(spare / 60), "off_block": self.clock(self.std) if self.std else "",
            "forecast": self.clock(self.forecast()), "driver": drive.id if drive is not None else "",
            "milestones": [{"id": m, "label": LABELS[m], "state": ms.state, "late": ms.late, "source": ms.source,
                            "minutes": round(ms.minutes), "latest": self.clock(ms.latest) if ms.latest is not None else "",
                            "opens": self.clock(ms.opens) if ms.opens is not None else "",
                            "eta": self.clock(ms.eta) if ms.eta is not None else "", "reason": ms.reason, "why": ms.why,
                            "blocked_by": list(ms.blocked_by)}
                           for m, ms in self.ms.items()],
        }

    def snapshot(self) -> str:
        return json.dumps(self.to_json())


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _with(f: Facts, **kw: Any) -> Facts:
    import dataclasses

    return dataclasses.replace(f, **kw)


@dataclass
class TurnaroundService:
    """On the bus: the events in, a ``TurnaroundState`` out when something's changed (and now and then for the clocks)."""

    tracker: Turnaround
    bus: Any
    publish_every: float = PUBLISH_S
    make: Callable[..., Any] | None = field(default=None)
    _sent: float = -1e9

    def __post_init__(self) -> None:
        self._inputs = self.bus.subscribe(OwnshipState, GroundStatus, AircraftVars, PhaseChanged, AtcTransmission, AirportData,
                                          FlightArrived, CabinIncident)

    async def run(self) -> None:
        from localtc.sim_api import TurnaroundState

        async for ev in self._inputs:
            try:
                self.tracker.observe(ev)
            except Exception:  # noqa: BLE001 - never in the way of the flight
                log.exception("Turnaround failed on %s", type(ev).__name__)
                continue
            if isinstance(ev, OwnshipState) and self.make is not None and not (self.tracker.facts.callsign and self.tracker.facts.aircraft):
                self.tracker.refresh(self.make())
            if isinstance(ev, OwnshipState) and (self.tracker.changed or ev.t - self._sent >= self.publish_every):
                self.tracker.changed, self._sent = False, ev.t
                self.bus.publish(TurnaroundState(t=ev.t, data=self.tracker.snapshot()))
