"""Conditional clearances: every condition ATC puts on a clearance kept as state, and watched.

A condition says what the clearance waits on (the traffic it's behind, a time, a distance), and is, at any moment,
``active``, ``satisfied`` (what it waited on happened), ``expired`` (it no longer applies: the traffic went around
or vanished, its time ran out), ``contradicted`` (the pilot did otherwise: lined up before the traffic had passed)
or ``cancelled`` (ATC took it back). ATC watches each one every tick and says what follows from it: a new
instruction when it expires or is contradicted, nothing when it's met.

The conditions:

- ``behind_landing``: "behind the landing Airbus A320 on short final, line up and wait behind" (ICAO; the FAA never
  conditions a line-up: it holds the departure short, as before).
- ``cross_behind``: "behind the landing ..., cross runway 27R behind" (ICAO); the FAA holds the aircraft short of the
  runway ("hold short runway 27R, landing traffic") and clears it across once the traffic is by (``cross_after``).
- ``no_delay``: "cleared for takeoff, no delay" with an arrival a few miles out; not rolling in time, the clearance
  is cancelled.
- ``wake``: the wake time behind a heavy departure (two minutes, three behind a super) before the takeoff
  clearance, which then says "caution wake turbulence"; landing behind a heavy, the landing clearance says it.
- ``follow``: "number two, follow the Boeing 737 on a four mile final", until it has landed; closing on it, "reduce
  to final approach speed".
- ``vacate``: on final with the landing traffic still on the runway: "continue, traffic vacating", and the landing
  clearance once it's off.
- ``speed``: "maintain 170 knots until five mile final", kept to until then; well off it, said again.
- ``expedite``: "cross runway 09, expedite" with an arrival a few miles out.

Each is in the session snapshot and the ``AtcConditions`` event for the app, the companion and the replay.
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from localtc.atc_core.airport import aircraft_size
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Phrase
from localtc.sim_api import OwnshipState, TrafficTarget

if TYPE_CHECKING:
    from localtc.atc_core.facilities import Facility

STATES = ("active", "satisfied", "expired", "contradicted", "cancelled")
CONDITION_S = 300.0  # a condition still waiting this long has outlived itself
BEHIND_LANDING_NM = 3.0  # an arrival this close on final: line up (ICAO) or cross (ICAO) behind it
CROSS_HOLD_NM = 3.0  # ... and the FAA holds a crossing short of the runway for it
EXPEDITE_NM = 5.0  # an arrival this far out: the crossing is to be quick
NO_DELAY_NM = 2.5  # the takeoff clearance says "no delay" with an arrival this much beyond the departure margin
NO_DELAY_S = 40.0  # ... and is cancelled if the aircraft isn't rolling this long after it
ROLLING_KT = 30.0
WAKE_DEPARTURE_S = {"H": 120.0, "J": 180.0}  # behind a heavy (a super) departing from the same runway
WAKE_LANDING_S = 150.0  # a heavy landed this recently ahead on the same runway: the landing clearance cautions
FOLLOW_CLOSE_NM = 2.5  # closing on the traffic followed inside this: slow down
FOLLOW_CLOSING_KT = 20.0
SPEED_UNTIL_NM = 5.0  # "maintain 170 knots until five mile final"
SPEED_OFF_KT = 15.0  # this far off the assigned speed ...
SPEED_OFF_S = 20.0  # ... this long, and it's said again (once)
LANDED_KT = 80.0  # the traffic followed is on the ground below this: landed


@dataclass
class Condition:
    kind: str
    issued_t: float
    instruction_id: str = ""
    target: int | None = None  # the traffic it's about (its object id)
    described: str = ""  # "the landing Airbus A320"
    runway: str = ""
    until_t: float = math.inf
    value: Any = None  # the speed, the wake time's end, ...
    state: str = "active"
    ended_t: float | None = None
    detail: str = ""
    reminded: bool = False
    off_since: float | None = None

    def to_json(self) -> dict:
        return {"kind": self.kind, "issued_t": self.issued_t, "instruction": self.instruction_id, "target": self.target,
                "traffic": self.described, "runway": self.runway,
                "until_t": None if math.isinf(self.until_t) else self.until_t, "value": self.value,
                "state": self.state, "ended_t": self.ended_t, "detail": self.detail}


@dataclass
class ConditionBook:
    """The flight's conditions: the active ones and the ended ones (kept for the replay and the UI)."""

    items: list[Condition] = field(default_factory=list)
    changed: bool = False

    def add(self, condition: Condition) -> Condition:
        for old in self.items:
            if old.state == "active" and old.kind == condition.kind:
                self.end(old, "cancelled", condition.issued_t, "replaced")
        self.items.append(condition)
        self.items = self.items[-40:]
        self.changed = True
        return condition

    def active(self, kind: str, runway: str | None = None) -> Condition | None:
        return next((c for c in reversed(self.items) if c.state == "active" and c.kind == kind
                     and (runway is None or c.runway == runway)), None)

    def end(self, condition: Condition, state: str, t: float, detail: str = "") -> None:
        if condition.state != "active":
            return
        condition.state, condition.ended_t, condition.detail = state, t, detail
        self.changed = True

    def to_json(self) -> list[dict]:
        return [c.to_json() for c in self.items]


class ConditionsMixin:
    """The engine's side: giving each condition and watching it (``AtcEngine``)."""

    conditions: ConditionBook
    _wake_departures: dict[str, tuple[float, str, str]]  # runway -> (when it lifted off, its category, its type)
    _wake_landings: dict[str, tuple[float, str, str]]

    # --- what the conditions know of the traffic -----------------------------------------------------------------

    def _wake_of(self, target: TrafficTarget) -> str:
        return aircraft_size.wake_category(target.atc_model)

    def _own_wake(self) -> str:
        return aircraft_size.wake_category(self.state.flight.aircraft_type or self._aircraft_title) or "M"

    def _note_wake(self, t: float) -> None:
        """From each traffic picture: which heavies have just lifted off or touched down on which runway (for the wake
        time behind them)."""
        for icao in dict.fromkeys(a for a in (self.state.flight.origin, self.state.flight.destination) if a):
            geo = self.geometry(icao)
            if geo is None:
                continue
            for target in self._traffic.values():
                category = self._wake_of(target)
                if category not in ("H", "J") or target.alt_ft - geo.airport.elev_ft > 600:
                    continue
                runway = geo.runway_at(target.lat, target.lon, 60.0)
                if runway is None:
                    continue
                end = min(runway.ends, key=lambda e: abs(((e.heading_true - target.hdg_true + 540) % 360) - 180))
                kind = self._aircraft(target)
                said = kind.display if kind is not None else "heavy"
                if not target.on_ground and self._climbing(target):
                    self._wake_departures[end.ident] = (t, category, said)
                elif target.on_ground and target.gs_kt > 60 and target.object_id in self._seen_airborne:
                    self._wake_landings[end.ident] = (t, category, said)

    def _arrival_on(self, icao: str | None, runway_name: str, within_nm: float) -> tuple[float, TrafficTarget] | None:
        """The nearest aircraft landing on (or about to cross over) the runway ``runway_name`` ("09/27") at ``icao``,
        within ``within_nm`` on its final: (its miles out, it)."""
        geo = self.geometry(icao)
        if geo is None:
            return None
        found: list[tuple[float, int, TrafficTarget]] = []
        for target in self._traffic.values():
            if target.on_ground or target.alt_ft - geo.airport.elev_ft > 3000 or self._climbing(target):
                continue
            if target.alt_ft - geo.airport.elev_ft < 300 and (on := geo.runway_at(target.lat, target.lon, 60.0)) \
                    and on.name == runway_name:
                found.append((0.0, target.object_id, target))
                continue
            final = geo.final_approach(target.lat, target.lon, target.hdg_true, max_distance_nm=within_nm)
            if final is not None and final.end.runway.name == runway_name:
                found.append((final.distance_nm, target.object_id, target))
        if not found:
            return None
        miles, _, target = min(found)
        return miles, target

    def _described(self, target: TrafficTarget, what: str = "landing") -> Phrase:
        kind = self._aircraft(target)
        if kind is None:
            return Phrase(f"the {what} traffic", f"the {what} traffic")
        return Phrase(f"the {what} {kind.display}", f"the {what} {kind.spoken}")

    def _landed(self, target: TrafficTarget | None) -> bool:
        return target is not None and target.on_ground and target.gs_kt < LANDING_OR_ROLL_KT

    # --- giving them ------------------------------------------------------------------------------------------------

    def _conditional_line_up(self, t: float, facility: "Facility", runway: str, target: TrafficTarget, miles: float,
                             *, answering: bool) -> bool:
        """ICAO: an arrival close in on the departure runway, and nobody on it: "behind the landing A320 on short
        final, line up and wait behind". The FAA doesn't condition a line-up: False, and the departure holds short."""
        if not self.region.icao or miles > BEHIND_LANDING_NM or self._lined_up(runway):
            return False
        behind = self._described(target)
        where = "short final" if miles < 2.0 else f"{max(1, round(miles))} mile final"
        said = "short final" if miles < 2.0 else f"{speech.number_words(max(1, round(miles)))} mile final"
        self.conditions.add(Condition("behind_landing", t, "tower.luaw_behind", target.object_id, behind.display, runway,
                                      until_t=t + CONDITION_S))
        self._takeoff_wait, self._takeoff_hold = runway, "behind"
        self._schedule(t, "tower.luaw_behind", {"runway": runway, "message": Phrase(f"{behind.display} on {where}",
                                                                                     f"{behind.spoken} on {said}")},
                       facility, clearance="line_up", delay=answering,
                       on_issue=lambda: self._assign(departure_runway=runway))
        return True

    def _no_delay(self, t: float, runway: str, miles: float | None) -> Phrase | None:
        """The takeoff clearance with an arrival not far beyond the margin: "no delay", and watched."""
        if miles is None:
            return None
        self.conditions.add(Condition("no_delay", t, "tower.takeoff", runway=runway, until_t=t + NO_DELAY_S * 3,
                                      value=round(miles, 1)))
        return Phrase("no delay", "no delay")

    def _wake_wait(self, runway: str, t: float) -> tuple[float, str] | None:
        """Seconds still to wait behind a heavier aircraft that lifted off this runway, and its type; None: no wait."""
        got = self._wake_departures.get(runway)
        if got is None:
            return None
        when, category, said = got
        if not aircraft_size.heavier(category, self._own_wake()):
            return None
        left = WAKE_DEPARTURE_S.get(category, 120.0) - (t - when)
        return (left, said) if left > 5.0 else None

    def _wake_hold(self, t: float, facility: "Facility", runway: str, *, answering: bool) -> bool:
        """Ready to go behind a heavy just departed: held for the wake time, then cleared with the caution."""
        wait = self._wake_wait(runway, t)
        if wait is None:
            return False
        left, said = wait
        if (c := self.conditions.active("wake", runway)) is not None:
            return True  # told already; the clearance comes when the time is up
        minutes = max(1, math.ceil(left / 60.0))
        self.conditions.add(Condition("wake", t, "tower.hold_wake", runway=runway, until_t=t + left, value=minutes,
                                      described=said))
        self._takeoff_wait = runway
        self._schedule(t, "tower.hold_wake", {"runway": runway, "minutes": minutes,
                                              "message": Phrase(f"departing {said}", f"departing {said}")},
                       facility, delay=answering, expects_readback=False)
        return True

    def _wake_caution(self, runway: str, t: float, *, landing: bool) -> Phrase | None:
        """ "Caution wake turbulence" for a takeoff after the wake time, or a landing behind a heavy."""
        if landing:
            got = self._wake_landings.get(runway)
            if got is None or t - got[0] > WAKE_LANDING_S or not aircraft_size.heavier(got[1], self._own_wake()):
                return None
            return Phrase(f"caution wake turbulence, landing {got[2]}", f"caution wake turbulence, landing {got[2]}")
        c = next((c for c in reversed(self.conditions.items) if c.kind == "wake" and c.runway == runway
                  and c.state in ("active", "satisfied") and t - c.issued_t < 600), None)
        if c is None:
            return None
        self.conditions.end(c, "satisfied", t, "the wake time is up")
        return Phrase("caution wake turbulence", "caution wake turbulence")

    def _follow(self, t: float, target: TrafficTarget, runway: str) -> None:
        self.conditions.add(Condition("follow", t, "tower.sequence", target.object_id,
                                      self._described(target, "").display.replace("the  ", "the "), runway))

    def _speed_until(self, t: float, speed: int, until_nm: float = SPEED_UNTIL_NM) -> None:
        self.conditions.add(Condition("speed", t, "approach.speed_until", value=speed, until_t=t + 1800.0,
                                      detail=f"until {until_nm:.0f} mile final"))

    # --- watching them ------------------------------------------------------------------------------------------

    def _watch_conditions(self, own: OwnshipState) -> bool:
        """Each active condition: met, run out, or done otherwise, and what ATC says about it. True when ATC spoke."""
        t = own.t
        spoke = False
        for c in [c for c in self.conditions.items if c.state == "active"]:
            if t > c.until_t and c.kind not in ("wake", "speed"):
                self.conditions.end(c, "expired", t, "ran out")
                if c.kind == "behind_landing":
                    self._takeoff_hold = None  # tower decides again (``_release``)
                continue
            handler = getattr(self, f"_watch_{c.kind}", None)
            if handler is not None and not spoke:
                spoke = bool(handler(c, own)) or spoke
        if self.conditions.changed:
            self.conditions.changed = False
            self._conditions_changed = True
        return spoke

    def _watch_behind_landing(self, c: Condition, own: OwnshipState) -> bool:
        target = self._traffic.get(c.target) if c.target is not None else None
        if target is None:
            self.conditions.end(c, "expired", own.t, "the traffic is gone")
            self._takeoff_hold = None
            return False
        if target.on_ground:
            self.conditions.end(c, "satisfied", own.t, "the traffic has landed")
            self._takeoff_hold = "occupied"  # lining up: the takeoff clearance once it's off the runway
            return False
        if self._climbing(target) and target.object_id in self._wake_seen_low:
            self.conditions.end(c, "expired", own.t, "the traffic went around")
            self._takeoff_hold = None
            return False
        geo = self.geometry(self.state.flight.origin)
        if geo is not None and target.alt_ft - geo.airport.elev_ft < 500:
            self._wake_seen_low.add(target.object_id)
        if own.on_ground and own.on_runway and self._lined_up(c.runway) and (tuned := self.state.comms.tuned) is not None:
            # On the runway before the traffic it was to line up behind has passed.
            self.conditions.end(c, "contradicted", own.t, "lined up before the landing traffic passed")
            self._pending_alerts.append(self._alert(own.t, "condition", f"lined up on {c.runway} before {c.described} landed"))
            self._schedule(own.t, "tower.hold_position_traffic", {"message": Phrase(
                f"{c.described} is still on final", f"{c.described} is still on final")}, tuned, delay=False,
                expects_readback=False)
            self._takeoff_hold = "occupied"
            return True
        return False

    def _watch_cross_behind(self, c: Condition, own: OwnshipState) -> bool:
        target = self._traffic.get(c.target) if c.target is not None else None
        if target is None or target.on_ground:
            self.conditions.end(c, "satisfied" if target is not None else "expired", own.t,
                                "the traffic has landed" if target is not None else "the traffic is gone")
            return False
        if own.on_runway and own.on_ground and self.tracker.context.runway is not None \
                and self.tracker.context.runway.name == c.runway:
            self.conditions.end(c, "contradicted", own.t, "crossed before the landing traffic passed")
            self._pending_alerts.append(self._alert(own.t, "condition", f"crossed {c.runway} before {c.described} landed"))
        return False

    def _watch_cross_after(self, c: Condition, own: OwnshipState) -> bool:
        """FAA: held short for the landing traffic; once it's landed (or gone), cleared across."""
        target = self._traffic.get(c.target) if c.target is not None else None
        if target is not None and not target.on_ground and not self._climbing(target):
            return False
        tuned = self.state.comms.tuned
        if tuned is None or tuned.controller != "ground":
            return False
        self.conditions.end(c, "satisfied" if target is not None else "expired", own.t,
                            "the traffic has landed" if target is not None else "the traffic is gone")
        self._crossed.add(c.runway)
        self._crossing = c.runway
        self._schedule(own.t, "ground.cross_runway", {"runway": c.value or c.runway.split("/")[0]}, tuned, delay=False)
        return True

    def _watch_no_delay(self, c: Condition, own: OwnshipState) -> bool:
        if own.on_ground and own.gs_kt >= ROLLING_KT:
            self.conditions.end(c, "satisfied", own.t, "rolling")
            return False
        if not own.on_ground:
            self.conditions.end(c, "satisfied", own.t, "airborne")
            return False
        if own.t - c.issued_t < NO_DELAY_S or own.gs_kt > 5:
            return False
        blocked = self._departure_blocked(c.runway, lined_up=True)
        tuned = self.state.comms.tuned
        if blocked is None or tuned is None or tuned.controller != "tower":
            return False  # nothing coming after all: the clearance stands
        _, target, miles = blocked
        self.conditions.end(c, "contradicted", own.t, "not rolling in time")
        self.state.clearances.pop("takeoff", None)
        self._takeoff_wait, self._takeoff_hold = c.runway, "occupied" if self._lined_up(c.runway) else "arrival"
        n = max(1, round(miles))
        self._schedule(own.t, "tower.cancel_takeoff", {"message": Phrase(f"traffic on {n} mile final",
                                                                          f"traffic on a {speech.number_words(n)} mile final")},
                       tuned, delay=False)
        return True

    def _watch_wake(self, c: Condition, own: OwnshipState) -> bool:
        if own.t >= c.until_t and c.state == "active":
            # The time's up: the takeoff clearance (``_release``) says "caution wake turbulence" and ends it.
            self._takeoff_hold = None
        return False

    def _watch_follow(self, c: Condition, own: OwnshipState) -> bool:
        target = self._traffic.get(c.target) if c.target is not None else None
        if target is None:
            self.conditions.end(c, "expired", own.t, "the traffic is gone")
            return False
        if target.on_ground and target.gs_kt < LANDED_KT:
            self.conditions.end(c, "satisfied", own.t, "the traffic has landed")
            return False
        geo = self.geometry(self.state.flight.destination)
        tuned = self.state.comms.tuned
        if geo is None or c.reminded or tuned is None or tuned.controller != "tower" or own.on_ground:
            return False
        gap = _nm(own.lat, own.lon, target.lat, target.lon)
        if gap < FOLLOW_CLOSE_NM and own.gs_kt - target.gs_kt > FOLLOW_CLOSING_KT:
            c.reminded = True
            n = max(1, round(gap))
            self._schedule(own.t, "tower.follow_slow", {"message": Phrase(
                f"traffic {n} mile{'s' if n > 1 else ''} ahead", f"traffic {speech.number_words(n)} mile{'s' if n > 1 else ''} ahead")},
                tuned, delay=False, expects_readback=False)
            return True
        return False

    def _watch_vacate(self, c: Condition, own: OwnshipState) -> bool:
        if "landing" in self.state.clearances:
            self.conditions.end(c, "satisfied", own.t, "the runway's clear: cleared to land")
        elif self._going_around or own.on_ground:
            self.conditions.end(c, "expired", own.t, "went around" if self._going_around else "landed")
        return False

    def _watch_speed(self, c: Condition, own: OwnshipState) -> bool:
        ctx = self.tracker.context
        if self.state.assignments.speed_kt != c.value or own.on_ground or self._going_around:
            self.conditions.end(c, "expired", own.t, "a new speed" if self.state.assignments.speed_kt != c.value else "")
            return False
        if ctx.final is not None and ctx.final.distance_nm <= SPEED_UNTIL_NM:
            self.conditions.end(c, "satisfied", own.t, "at the five mile final")
            self._assign(speed_kt=None)
            return False
        if own.ias_kt - c.value <= SPEED_OFF_KT:
            c.off_since = None  # (slower is the pilot configuring to land: "reduce speed to 180" at 151 on final)
            return False
        c.off_since = c.off_since if c.off_since is not None else own.t
        tuned = self.state.comms.tuned
        if c.reminded or own.t - c.off_since < SPEED_OFF_S or tuned is None or tuned.controller not in ("approach", "tower", "center"):
            return False
        c.reminded = True
        self._schedule(own.t, "approach.speed", {"speed": int(c.value)}, tuned, delay=False)
        return True

    def _watch_expedite(self, c: Condition, own: OwnshipState) -> bool:
        if not own.on_runway and own.t - c.issued_t > 10:
            self.conditions.end(c, "satisfied", own.t, "across")
        return False


LANDING_OR_ROLL_KT = 200.0  # (on the ground at all: landed)


def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 60.0
    dx = (lon2 - lon1) * 60.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)
