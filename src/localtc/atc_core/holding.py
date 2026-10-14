"""The engine's side of holds (``atc_core.holds`` has the hold itself): when a controller holds the flight, the hold
given and watched as it's flown, the sequence and EFC said again when they change, and the way out of it when its
turn comes or the reason is gone. A hold asked for is given "as requested" and kept until the pilot asks to go on.
"""

from typing import TYPE_CHECKING, Any

from localtc.atc_core.holds import (
    DEMAND_NM,
    HOLD_DEMAND,
    HOLD_FROM_NM,
    HOLD_TO_NM,
    EFC_SLACK_MIN,
    Hold,
    bearing,
    clock,
    delay_minutes,
    entry_for,
    leg_minutes,
    nm,
)
from localtc.atc_core.phase import FlightPhase as P
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Phrase
from localtc.sim_api import OwnshipState

if TYPE_CHECKING:
    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation

HOLD_CHECK_S = 60.0  # how often the controller looks at the arrival demand
HOLD_FROM_DEST_NM, HOLD_TO_DEST_NM = 30.0, 160.0  # a flight this far from the destination may be held
SEQUENCE_SAY_S = 180.0  # a better place in the sequence said no more often than this
LEAVE_POSITION = 2  # number two or better: out of the hold
FIX_CAPTURE_NM = 1.5  # over the fix
REQUESTED_EFC_MIN = 15
WEATHER_EFC_MIN = 20
MIN_DELAY_MIN = 4
CAT3_VIS_SM, NON_PRECISION_VIS_SM, NON_PRECISION_CEILING_FT = 0.1, 1.0, 400
MAX_HOLDS = 2  # a flight is held no more than this many times


class HoldsMixin:
    """``AtcEngine``'s holds."""

    hold: Hold | None
    _hold_checked_t: float
    _holds_given: int

    def _arrivals_ahead(self, own: OwnshipState) -> list[tuple[float, Any]]:
        """The aircraft on their way down to the destination ahead of this one: airborne, within ``DEMAND_NM``,
        nearer than it and heading for it."""
        geo = self.geometry(self.state.flight.destination)
        if geo is None:
            return []
        apt = geo.airport
        mine = nm(own.lat, own.lon, apt.lat, apt.lon)
        ahead = []
        for target in self._traffic.values():
            if target.on_ground:
                continue
            d = nm(target.lat, target.lon, apt.lat, apt.lon)
            if d >= mine or d > DEMAND_NM or target.alt_ft - apt.elev_ft > 25000 or self._climbing(target):
                continue
            towards = abs(((bearing(target.lat, target.lon, apt.lat, apt.lon) - target.hdg_true + 540) % 360) - 180)
            if towards < 100 or d < 8:
                ahead.append((d, target))
        return sorted(ahead, key=lambda x: x[0])

    def _hold_reason(self, own: OwnshipState) -> tuple[str, int] | None:
        """Why this flight would hold now, and its place in the sequence: (reason, position); None: no need."""
        dest = self.state.flight.destination
        geo = self.geometry(dest)
        if geo is not None and geo.runways and all(self._closed(dest, end.ident) for r in geo.runways for end in r.ends):
            return "runway closed", 1
        info = self.current_atis(dest)
        if info is not None and geo is not None:
            w = info.weather
            ils = any(end.has_ils for r in geo.runways for end in r.ends)
            low_vis = w.visibility_sm is not None and w.visibility_sm < (CAT3_VIS_SM if ils else NON_PRECISION_VIS_SM)
            low_cloud = not ils and w.ceiling_ft is not None and w.ceiling_ft < NON_PRECISION_CEILING_FT
            if low_vis or low_cloud:
                return "weather", 1
        ahead = self._arrivals_ahead(own)
        if len(ahead) >= HOLD_DEMAND:
            return "arrival traffic", len(ahead) + 1
        return None

    def _hold_fix(self, own: OwnshipState) -> tuple[str, float, float] | None:
        """Where to hold: the STAR's or the route's last fix 15 to 60 miles out, still ahead."""
        geo = self.geometry(self.state.flight.destination)
        if geo is None or not self.route:
            return None
        apt = geo.airport
        star = list(self.route.procedure(self.cfg.star, "DSC")) if self.cfg.star else []
        candidates = list(reversed(star)) + [f for f in reversed(self.route.fixes) if f not in star]
        for fix in candidates:
            d = nm(fix.lat, fix.lon, apt.lat, apt.lon)
            ahead = abs(((bearing(own.lat, own.lon, fix.lat, fix.lon) - own.hdg_true + 540) % 360) - 180) < 100
            if HOLD_FROM_NM <= d <= HOLD_TO_NM and ahead and nm(own.lat, own.lon, fix.lat, fix.lon) > 3.0:
                return fix.ident, fix.lat, fix.lon
        return None

    def _build_hold(self, own: OwnshipState, fix: tuple[str, float, float], reason: str, position: int, t: float) -> Hold:
        geo = self.geometry(self.state.flight.destination)
        ident, lat, lon = fix
        inbound_true = bearing(lat, lon, geo.airport.lat, geo.airport.lon) if geo is not None else own.hdg_true
        magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180
        inbound_mag = int(round((inbound_true - magvar) % 360)) % 360 or 360
        level = self.state.assignments.altitude_ft or int(round(own.alt_indicated_ft, -2))
        delay = {"weather": WEATHER_EFC_MIN, "requested": REQUESTED_EFC_MIN, "runway closed": WEATHER_EFC_MIN}.get(
            reason, delay_minutes(position))
        delay = max(delay, MIN_DELAY_MIN)
        to_fix_min = nm(own.lat, own.lon, lat, lon) / max(own.gs_kt, 150.0) * 60.0
        efc_in_s = (to_fix_min + delay + EFC_SLACK_MIN) * 60.0
        hold = Hold(ident, lat, lon, inbound_true, inbound_mag, "right", leg_minutes(level), int(level), reason, t,
                    t + efc_in_s, clock(own.zulu_s, efc_in_s), position=position, delay_min=int(delay),
                    entry=entry_for(bearing(own.lat, own.lon, lat, lon), inbound_true, "right"),
                    tas_kt=min(max(own.gs_kt, 180.0), 265.0))
        hold.last_said_position = position
        hold.history.append((t, "cleared"))
        return hold

    @staticmethod
    def _hold_message(hold: Hold) -> Phrase:
        reason = {"arrival traffic": "due to arrival traffic", "weather": "due to weather below minimums",
                  "runway closed": "the runway is closed", "requested": "as requested"}.get(hold.reason, hold.reason)
        if hold.reason == "arrival traffic":
            return Phrase(f"number {hold.position}, delay {hold.delay_min} minutes {reason}",
                          f"number {speech.number_words(hold.position)}, delay {speech.number_words(hold.delay_min)} "
                          f"minutes {reason}")
        return Phrase(reason, reason)

    def _give_hold(self, own: OwnshipState, facility: "Facility", reason: str, position: int, t: float, *,
                   fix: tuple[str, float, float] | None = None, answering: bool = False) -> bool:
        fix = fix or self._hold_fix(own)
        if fix is None:
            return False
        hold = self._build_hold(own, fix, reason, position, t)
        self.hold = hold
        self._holds_given += 1
        self._ops_changed = True
        slots: dict[str, Any] = {"fix": hold.fix, "heading": hold.inbound_mag, "turn": hold.turn,
                                 "altitude": hold.altitude_ft, "message": self._hold_message(hold)}
        if hold.efc_clock:
            slots["efc"] = hold.efc_clock
            template = "common.hold"
        else:
            slots["minutes"] = max(1, int(round((hold.efc_t - t) / 60.0)))
            template = "common.hold_minutes"
        self._schedule(t, template, slots, facility, delay=answering,
                       on_issue=lambda: self._assign(altitude_ft=hold.altitude_ft, heading=None))
        return True

    def _holds(self, own: OwnshipState, facility: "Facility | None") -> bool:
        """The hold: given when there's reason to, watched while it's flown, and left when it's this flight's turn."""
        st, t = self.state, own.t
        if self.hold is not None and self.hold.state not in ("done", "cancelled"):
            return self._watch_hold(own, facility)
        if facility is None or facility.controller not in ("center", "approach") or self._vfr or own.on_ground \
                or st.pending is not None or "approach" in st.clearances or self._going_around:
            return False
        if t - self._hold_checked_t < HOLD_CHECK_S or self._holds_given >= MAX_HOLDS:
            return False
        self._hold_checked_t = t
        if st.phase is None or P(st.phase) not in (P.CRUISE, P.ARRIVAL):
            return False
        geo = self.geometry(st.flight.destination)
        if geo is None:
            return False
        if not HOLD_FROM_DEST_NM <= nm(own.lat, own.lon, geo.airport.lat, geo.airport.lon) <= HOLD_TO_DEST_NM:
            return False
        why = self._hold_reason(own)
        if why is None:
            return False
        return self._give_hold(own, facility, why[0], why[1], t)

    def _watch_hold(self, own: OwnshipState, facility: "Facility | None") -> bool:
        hold, t = self.hold, own.t
        d = nm(own.lat, own.lon, hold.lat, hold.lon)
        if hold.state == "cleared" and d <= FIX_CAPTURE_NM:
            hold.state, hold.entered_t = "entering", t
            hold.history.append((t, "entering"))
            self._ops_changed = True
        elif hold.state == "entering" and d > 3.0:
            hold.state = "holding"
            hold.history.append((t, "holding"))
            self._ops_changed = True
        elif hold.state == "holding" and d <= FIX_CAPTURE_NM and t - hold.history[-1][0] > 90:
            hold.laps += 1
            hold.history.append((t, f"lap {hold.laps}"))
            self._ops_changed = True
        if facility is None or facility.controller not in ("center", "approach") or self.state.pending is not None \
                or hold.state == "leaving":
            return False
        if hold.reason == "requested":
            return False  # the pilot says when (asks to go on, or for direct)
        why = self._hold_reason(own)
        still = why is not None and why[0] == hold.reason
        if hold.reason == "arrival traffic":
            position = why[1] if still else 1
            if position <= LEAVE_POSITION:
                return self._leave_hold(own, facility)
            if position != hold.position:
                better = position < hold.position
                hold.position, hold.delay_min = position, delay_minutes(position)
                self._ops_changed = True
                if better and position < hold.last_said_position and t - hold.issued_t >= SEQUENCE_SAY_S:
                    return self._hold_update(own, facility, revise=False)
        elif not still:
            return self._leave_hold(own, facility)  # the weather's up to the minimums, the runway open again
        if t >= hold.efc_t - 60.0:
            return self._hold_update(own, facility, revise=True)
        return False

    def _hold_update(self, own: OwnshipState, facility: "Facility", *, revise: bool) -> bool:
        hold, t = self.hold, own.t
        if revise:
            extra = max(5, hold.delay_min if hold.reason == "arrival traffic" else WEATHER_EFC_MIN)
            hold.efc_t = t + extra * 60.0
            hold.efc_clock = clock(own.zulu_s, extra * 60.0)
        hold.last_said_position = hold.position
        hold.history.append((t, f"number {hold.position}" + (", new EFC" if revise else "")))
        self._ops_changed = True
        slots: dict[str, Any] = {"message": self._hold_message(hold)}
        if hold.efc_clock:
            self._schedule(t, "common.hold_update", {**slots, "efc": hold.efc_clock}, facility, delay=False)
        else:
            self._schedule(t, "common.hold_update_minutes",
                           {**slots, "minutes": max(1, int(round((hold.efc_t - t) / 60.0)))}, facility, delay=False)
        return True

    def _leave_hold(self, own: OwnshipState, facility: "Facility", *, answering: bool = False) -> bool:
        """Out of the hold: on to the route's next fix after the hold's (or the destination)."""
        hold, t = self.hold, own.t
        names = [p[0] for p in self._route_points()]
        dest = self.state.flight.destination or ""
        nxt = names[names.index(hold.fix) + 1] if hold.fix in names and names.index(hold.fix) + 1 < len(names) else dest
        if nxt == dest and dest:
            nxt = self._airport_name(dest)
        hold.state = "leaving"
        hold.history.append((t, "leaving"))
        self._ops_changed = True

        def done() -> None:
            hold.state = "done"
            self._ops_changed = True

        self._schedule(t, "common.leave_hold", {"fix": nxt or hold.fix}, facility, delay=answering, on_issue=done)
        return True

    def _hold_request(self, interp: "Interpretation", facility: "Facility", t: float, own: OwnshipState | None) -> None:
        """ "Request holding at BLAKO": given there (or at the route's next fix), with an EFC, "as requested"; "ready
        to continue" from the hold: out of it."""
        if own is None or own.on_ground or facility.controller not in ("center", "approach", "departure"):
            self._schedule(t, "common.unable", {}, facility)
            return
        if interp.values.get("leave"):
            if self.hold is not None and self.hold.state not in ("done", "cancelled", "leaving"):
                self._leave_hold(own, facility, answering=True)
            else:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return
        named = str(interp.values.get("fix") or "").upper()
        fix = None
        if named and self.route:
            fix = next(((f.ident, f.lat, f.lon) for f in self.route.fixes if f.ident == named), None)
        if fix is None:
            points = self._route_points()
            i = self._next_fix(own, points)
            fix = points[i] if i is not None and points[i][0] != self.state.flight.destination else None
        if fix is None or not self._give_hold(own, facility, "requested", 1, t, fix=fix, answering=True):
            self._schedule(t, "common.unable", {}, facility)

    def _hold_report(self, interp: "Interpretation", facility: "Facility", t: float, own: OwnshipState | None) -> None:
        if self.hold is not None and self.hold.state in ("cleared", "entering"):
            self.hold.state = "holding"
            self.hold.history.append((t, "holding (reported)"))
            self._ops_changed = True
        self._schedule(t, "common.roger", {}, facility, expects_readback=False)

    def _end_hold(self, t: float, why: str) -> None:
        if self.hold is not None and self.hold.state not in ("done", "cancelled"):
            self.hold.state = "cancelled"
            self.hold.history.append((t, why))
            self._ops_changed = True

    def hold_json(self) -> dict | None:
        return self.hold.to_json() if self.hold is not None else None
