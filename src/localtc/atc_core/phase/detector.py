"""Flight phase detection from telemetry and airport geometry only.

What ATC has said never affects the phase; that lives in the session. Every
transition condition must hold for a dwell time measured in event ``t``, so
noisy data doesn't flip phases and replays are deterministic.
"""

import enum

import msgspec

from localtc.atc_core.phase.context import PositionContext
from localtc.sim_api import OwnshipState, PhaseChanged
from localtc.sim_api.geo import METERS_PER_NM, angle_diff, bearing_deg, haversine_nm


class FlightPhase(enum.StrEnum):
    PARKED = "PARKED"
    PUSHBACK = "PUSHBACK"
    TAXI_OUT = "TAXI_OUT"
    RUNWAY_HOLD = "RUNWAY_HOLD"
    TAKEOFF = "TAKEOFF"
    DEPARTURE = "DEPARTURE"
    CRUISE = "CRUISE"
    ARRIVAL = "ARRIVAL"
    APPROACH = "APPROACH"
    LANDING = "LANDING"
    TAXI_IN = "TAXI_IN"


TRACK_BASELINE_M = 4.0  # ground covered before the direction of travel is recomputed
GROUND_PHASES = {FlightPhase.PARKED, FlightPhase.PUSHBACK, FlightPhase.TAXI_OUT, FlightPhase.RUNWAY_HOLD,
                 FlightPhase.TAXI_IN}


class PhaseThresholds(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """All tunables; override any of them in ``[atc.phase]``."""

    stopped_kt: float = 1.0
    taxi_start_kt: float = 3.0
    taxi_start_s: float = 2.0
    pushback_kt: float = 0.4  # moving at all
    pushback_max_kt: float = 8.0  # faster than a tug can push: that is taxiing
    pushback_astern_deg: float = 110.0  # how far the track must be off the nose to be going backwards
    pushback_s: float = 3.0
    hold_stop_s: float = 3.0
    hold_short_radius_m: float = 50.0
    leave_hold_s: float = 2.0
    takeoff_gs_kt: float = 30.0
    takeoff_s: float = 1.0
    rejected_takeoff_kt: float = 15.0
    airborne_s: float = 2.0
    airborne_agl_ft: float = 20.0
    level_vs_fpm: float = 300.0
    cruise_alt_tol_ft: float = 200.0
    cruise_level_s: float = 30.0
    cruise_no_plan_agl_ft: float = 3000.0
    cruise_no_plan_s: float = 60.0
    cruise_other_agl_ft: float = 10000.0  # level this high, away from the planned cruise ...
    cruise_other_s: float = 180.0  # ... for this long is a cruise too
    descent_vs_fpm: float = -400.0
    descent_s: float = 30.0
    descent_max_nm: float = 300.0  # a descent further out than this from the destination isn't the arrival
    tod_nm_per_1000ft: float = 3.0
    tod_min_nm: float = 30.0
    tod_s: float = 5.0
    approach_dist_nm: float = 15.0
    approach_agl_ft: float = 4000.0
    approach_aligned_nm: float = 12.0
    approach_config_nm: float = 20.0
    approach_s: float = 3.0
    landing_dist_nm: float = 2.0
    landing_agl_ft: float = 700.0
    go_around_vs_fpm: float = 500.0
    go_around_s: float = 3.0
    go_around_min_agl_ft: float = 300.0
    rollout_kt: float = 30.0
    rollout_s: float = 3.0
    unexpected_touchdown_s: float = 3.0
    parked_s: float = 20.0
    teleport_kt: float = 1500.0  # implied ground speed between ticks that means a slew/teleport


class PhaseDetector:
    def __init__(
        self,
        thresholds: PhaseThresholds | None = None,
        *,
        cruise_ft: float | None = None,
    ) -> None:
        self.th = thresholds or PhaseThresholds()
        self.cruise_ft = cruise_ft
        self.phase: FlightPhase | None = None
        self.since_t = 0.0
        self._timers: dict[str, float] = {}
        self._last: OwnshipState | None = None
        self._min_agl_in_phase = float("inf")
        self._track_from: tuple[float, float] | None = None
        self._track_deg: float | None = None

    def reset(self) -> None:
        """Forget the phase; the next tick is classified from scratch (flight loaded, teleport)."""
        self.phase = None
        self._timers.clear()
        self._last = None
        self._track_from = self._track_deg = None

    def resume(self) -> None:
        """After a pause: restart dwell timers and forget the last position (a slew during pause isn't a teleport)."""
        self._timers.clear()
        self._last = None
        self._track_from = self._track_deg = None

    def update(self, own: OwnshipState, ctx: PositionContext) -> PhaseChanged | None:
        last, self._last = self._last, own
        if last is not None and own.t > last.t:
            implied_kt = haversine_nm(last.lat, last.lon, own.lat, own.lon) / ((own.t - last.t) / 3600)
            if implied_kt > self.th.teleport_kt:
                return self._set(own, self._classify(own, ctx), "position jump")
        if self.phase is None:
            return self._set(own, self._classify(own, ctx), "initial state")

        self._min_agl_in_phase = min(self._min_agl_in_phase, own.alt_agl_ft)
        self._update_track(own)
        result = self._transition(own, ctx)
        if result is None:
            return None
        phase, reason = result
        return self._set(own, phase, reason)

    # --- helpers --------------------------------------------------------------

    def _held(self, name: str, condition: bool, t: float, seconds: float) -> bool:
        if not condition:
            self._timers.pop(name, None)
            return False
        start = self._timers.setdefault(name, t)
        return t - start >= seconds

    def _set(self, own: OwnshipState, phase: FlightPhase, reason: str) -> PhaseChanged | None:
        previous = self.phase
        self.phase, self.since_t = phase, own.t
        self._timers.clear()
        self._min_agl_in_phase = own.alt_agl_ft
        if previous == phase:
            return None
        return PhaseChanged(t=own.t, previous=previous.value if previous else None, phase=phase.value, reason=reason)

    def _classify(self, own: OwnshipState, ctx: PositionContext) -> FlightPhase:
        th = self.th
        if own.on_ground:
            if own.gs_kt >= th.takeoff_gs_kt and ctx.aligned_on_runway:
                return FlightPhase.TAKEOFF
            if own.gs_kt < th.taxi_start_kt:
                if ctx.aligned_on_runway or self._at_hold_short(ctx):
                    return FlightPhase.RUNWAY_HOLD
                return FlightPhase.PARKED
            return FlightPhase.TAXI_OUT
        if ctx.final is not None and ctx.final.distance_nm <= th.approach_aligned_nm:
            return FlightPhase.APPROACH
        if own.vs_fpm > th.level_vs_fpm:
            return FlightPhase.DEPARTURE
        far = ctx.destination_distance_nm is not None and ctx.destination_distance_nm > th.descent_max_nm
        if own.vs_fpm < -th.level_vs_fpm and not far:
            return FlightPhase.ARRIVAL
        return FlightPhase.CRUISE

    def _at_hold_short(self, ctx: PositionContext) -> bool:
        return ctx.hold_short_distance_m is not None and ctx.hold_short_distance_m <= self.th.hold_short_radius_m

    def _update_track(self, own: OwnshipState) -> None:
        """The direction the aircraft is actually travelling, measured over enough ground to be meaningful.

        At a walking pace one tick covers a few centimetres, so the track has to be taken between
        positions several metres apart rather than between consecutive ticks.
        """
        reference = self._track_from
        if reference is None:
            self._track_from = (own.lat, own.lon)
            return
        if haversine_nm(reference[0], reference[1], own.lat, own.lon) * METERS_PER_NM >= TRACK_BASELINE_M:
            self._track_deg = bearing_deg(reference[0], reference[1], own.lat, own.lon)
            self._track_from = (own.lat, own.lon)

    def _moving_astern(self, own: OwnshipState) -> bool:
        """Being pushed off the gate: creeping along the ground, going the way the tail points."""
        th = self.th
        if self._track_deg is None or not own.on_ground or not (th.pushback_kt <= own.gs_kt <= th.pushback_max_kt):
            return False
        return angle_diff(self._track_deg, own.hdg_true) >= th.pushback_astern_deg

    def _takeoff_roll(self, own: OwnshipState, ctx: PositionContext) -> bool:
        return self._held(
            "takeoff", own.on_ground and ctx.aligned_on_runway and own.gs_kt >= self.th.takeoff_gs_kt, own.t, self.th.takeoff_s
        )

    def _arrival_due(self, own: OwnshipState, ctx: PositionContext) -> str | None:
        th = self.th
        # A long descent means the arrival only near the destination. On a long flight there are plenty of
        # others -- avoiding traffic, a step down to a new cruise level, weather -- and taking one of those
        # for the arrival puts the whole flight in the wrong phase for hours.
        near_enough = ctx.destination_distance_nm is None or ctx.destination_distance_nm <= th.descent_max_nm
        if near_enough and self._held("descent", own.vs_fpm < th.descent_vs_fpm, own.t, th.descent_s):
            return "sustained descent"
        # Top of descent only counts once the climb is over (a short hop starts inside the TOD distance).
        if ctx.destination is not None and ctx.destination_distance_nm is not None and own.vs_fpm < th.level_vs_fpm:
            to_lose = max(0.0, own.alt_indicated_ft - ctx.destination.airport.elev_ft)
            tod_nm = max(th.tod_min_nm, to_lose / 1000 * th.tod_nm_per_1000ft)
            if self._held("tod", ctx.destination_distance_nm <= tod_nm, own.t, th.tod_s):
                return f"within {tod_nm:.0f} nm of {ctx.destination.icao}"
        return None

    def _transition(self, own: OwnshipState, ctx: PositionContext) -> tuple[FlightPhase, str] | None:
        th, t, phase = self.th, own.t, self.phase

        if phase in GROUND_PHASES and self._held("airborne", not own.on_ground and own.alt_agl_ft > 100, t, 5.0):
            return self._classify(own, ctx), "airborne while in a ground phase"
        if phase not in GROUND_PHASES and phase not in (FlightPhase.TAKEOFF, FlightPhase.LANDING):
            if self._held("touchdown", own.on_ground, t, th.unexpected_touchdown_s):
                return FlightPhase.LANDING, "on the ground"

        if phase is FlightPhase.PARKED:
            if self._held("pushback", self._moving_astern(own), t, th.pushback_s):
                return FlightPhase.PUSHBACK, "being pushed back"
            if self._held("taxi", own.on_ground and own.gs_kt > th.taxi_start_kt, t, th.taxi_start_s):
                return FlightPhase.TAXI_OUT, "started moving"

        elif phase is FlightPhase.PUSHBACK:
            moving_off = own.on_ground and own.gs_kt > th.taxi_start_kt and not self._moving_astern(own)
            if self._held("taxi", moving_off, t, th.taxi_start_s):
                return FlightPhase.TAXI_OUT, "taxiing off the gate"
            if self._held("push_done", own.gs_kt < th.stopped_kt, t, th.parked_s):
                return FlightPhase.PARKED, "pushback complete"

        elif phase is FlightPhase.TAXI_OUT:
            if self._takeoff_roll(own, ctx):
                return FlightPhase.TAKEOFF, "takeoff roll"
            holding = own.on_ground and own.gs_kt < th.stopped_kt and (self._at_hold_short(ctx) or ctx.aligned_on_runway)
            if self._held("hold", holding, t, th.hold_stop_s):
                return FlightPhase.RUNWAY_HOLD, "lined up" if ctx.on_runway else "holding short"
            parked = own.gs_kt < th.stopped_kt and (own.parking_brake or not own.engine_running)
            if self._held("parked", parked and not ctx.on_runway, t, th.parked_s):
                return FlightPhase.PARKED, "stopped with brake set"

        elif phase is FlightPhase.RUNWAY_HOLD:
            if self._takeoff_roll(own, ctx):
                return FlightPhase.TAKEOFF, "takeoff roll"
            away = (
                own.gs_kt > th.taxi_start_kt
                and not ctx.on_runway
                and not self._at_hold_short(ctx)
            )
            if self._held("leave_hold", away, t, th.leave_hold_s):
                return FlightPhase.TAXI_OUT, "taxied away from the runway"

        elif phase is FlightPhase.TAKEOFF:
            if self._held("liftoff", not own.on_ground and own.alt_agl_ft > th.airborne_agl_ft, t, th.airborne_s):
                return FlightPhase.DEPARTURE, "airborne"
            if own.on_ground and own.gs_kt < th.rejected_takeoff_kt:
                return (FlightPhase.RUNWAY_HOLD if ctx.on_runway else FlightPhase.TAXI_OUT), "rejected takeoff"

        elif phase is FlightPhase.DEPARTURE:
            if self.cruise_ft is not None:
                # Up in the flight levels the altimeter should be on 29.92; a crew that left the local setting
                # in reads a couple of hundred feet off (30.15 in: FL380 shows 38,230). Either reading counts.
                pressure_ft = own.alt_indicated_ft - (own.altimeter_inhg - 29.92) * 1000 if 25 < own.altimeter_inhg < 33 \
                    else own.alt_indicated_ft
                off = min(abs(own.alt_indicated_ft - self.cruise_ft), abs(pressure_ft - self.cruise_ft))
                steady = abs(own.vs_fpm) < th.level_vs_fpm
                if self._held("cruise", off <= th.cruise_alt_tol_ft and steady, t, th.cruise_level_s):
                    return FlightPhase.CRUISE, f"level at {self.cruise_ft:.0f} ft"
                # Level somewhere else for a good while, high up: a different cruise (ATC's, or a step climb to come).
                if self._held("cruise_other", steady and own.alt_agl_ft > th.cruise_other_agl_ft, t, th.cruise_other_s):
                    return FlightPhase.CRUISE, f"level at {own.alt_indicated_ft:.0f} ft"
            else:
                level = own.alt_agl_ft > th.cruise_no_plan_agl_ft and abs(own.vs_fpm) < th.level_vs_fpm
                if self._held("cruise", level, t, th.cruise_no_plan_s):
                    return FlightPhase.CRUISE, "level"
            if own.alt_agl_ft > 1000 and (reason := self._arrival_due(own, ctx)):
                return FlightPhase.ARRIVAL, reason
            pattern_final = (
                ctx.final is not None
                and ctx.final.distance_nm <= th.landing_dist_nm
                and own.alt_agl_ft < th.landing_agl_ft
                and own.vs_fpm < 0
            )
            if self._held("pattern_final", pattern_final, t, th.approach_s):
                return FlightPhase.LANDING, f"short final runway {ctx.final.end.ident}"

        elif phase is FlightPhase.CRUISE:
            if reason := self._arrival_due(own, ctx):
                return FlightPhase.ARRIVAL, reason

        elif phase is FlightPhase.ARRIVAL:
            reason = None
            dest_nm = ctx.destination_distance_nm
            if dest_nm is not None and dest_nm <= th.approach_dist_nm and own.alt_agl_ft < th.approach_agl_ft:
                reason = f"within {th.approach_dist_nm:.0f} nm below {th.approach_agl_ft:.0f} AGL"
            elif ctx.final is not None and ctx.final.distance_nm <= th.approach_aligned_nm:
                reason = f"aligned with runway {ctx.final.end.ident}"
            elif dest_nm is not None and dest_nm <= th.approach_config_nm and own.gear_down and own.flaps_index > 0:
                reason = "configured for landing"
            if self._held("approach", reason is not None, t, th.approach_s):
                return FlightPhase.APPROACH, reason

        elif phase is FlightPhase.APPROACH:
            final = ctx.final
            short_final = final is not None and final.distance_nm <= th.landing_dist_nm and own.alt_agl_ft < th.landing_agl_ft
            if self._held("landing", short_final, t, th.approach_s):
                return FlightPhase.LANDING, f"short final runway {final.end.ident}"

        elif phase is FlightPhase.LANDING:
            climbing = not own.on_ground and own.vs_fpm > th.go_around_vs_fpm
            if self._min_agl_in_phase < th.go_around_min_agl_ft and self._held("go_around", climbing, t, th.go_around_s):
                return FlightPhase.DEPARTURE, "go-around"
            slow = own.on_ground and own.gs_kt < th.rollout_kt
            if self._held("rollout", slow, t, th.rollout_s):
                return FlightPhase.TAXI_IN, "rollout complete"
            if self._held("exited", own.on_ground and not ctx.on_runway, t, th.rollout_s):
                return FlightPhase.TAXI_IN, "exited the runway"

        elif phase is FlightPhase.TAXI_IN:
            if self._takeoff_roll(own, ctx):
                return FlightPhase.TAKEOFF, "takeoff roll"
            parked = own.gs_kt < th.stopped_kt and (own.parking_brake or not own.engine_running)
            if self._held("parked", parked, t, th.parked_s):
                return FlightPhase.PARKED, "stopped with brake set"

        return None
