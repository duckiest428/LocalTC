"""Flight phase detection from telemetry and airport geometry only.

What ATC has said never affects the phase; that lives in the session. Every
transition condition must hold for a dwell time measured in event ``t``, so
noisy data doesn't flip phases and replays are deterministic.
"""

import enum

import msgspec

from localtc.atc_core.phase.context import PositionContext
from localtc.sim_api import OwnshipState, PhaseChanged
from localtc.sim_api.geo import haversine_nm


class FlightPhase(enum.StrEnum):
    PARKED = "PARKED"
    TAXI_OUT = "TAXI_OUT"
    RUNWAY_HOLD = "RUNWAY_HOLD"
    TAKEOFF = "TAKEOFF"
    DEPARTURE = "DEPARTURE"
    CRUISE = "CRUISE"
    ARRIVAL = "ARRIVAL"
    APPROACH = "APPROACH"
    LANDING = "LANDING"
    TAXI_IN = "TAXI_IN"


GROUND_PHASES = {FlightPhase.PARKED, FlightPhase.TAXI_OUT, FlightPhase.RUNWAY_HOLD, FlightPhase.TAXI_IN}


class PhaseThresholds(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """All tunables; override any of them in ``[atc.phase]``."""

    stopped_kt: float = 1.0
    taxi_start_kt: float = 3.0
    taxi_start_s: float = 2.0
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
    descent_vs_fpm: float = -400.0
    descent_s: float = 30.0
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

    def reset(self) -> None:
        """Forget the phase; the next tick is classified from scratch (flight loaded, teleport)."""
        self.phase = None
        self._timers.clear()
        self._last = None

    def resume(self) -> None:
        """After a pause: restart dwell timers and forget the last position (a slew during pause isn't a teleport)."""
        self._timers.clear()
        self._last = None

    def update(self, own: OwnshipState, ctx: PositionContext) -> PhaseChanged | None:
        last, self._last = self._last, own
        if last is not None and own.t > last.t:
            implied_kt = haversine_nm(last.lat, last.lon, own.lat, own.lon) / ((own.t - last.t) / 3600)
            if implied_kt > self.th.teleport_kt:
                return self._set(own, self._classify(own, ctx), "position jump")
        if self.phase is None:
            return self._set(own, self._classify(own, ctx), "initial state")

        self._min_agl_in_phase = min(self._min_agl_in_phase, own.alt_agl_ft)
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
        if own.vs_fpm < -th.level_vs_fpm:
            return FlightPhase.ARRIVAL
        return FlightPhase.CRUISE

    def _at_hold_short(self, ctx: PositionContext) -> bool:
        return ctx.hold_short_distance_m is not None and ctx.hold_short_distance_m <= self.th.hold_short_radius_m

    def _takeoff_roll(self, own: OwnshipState, ctx: PositionContext) -> bool:
        return self._held(
            "takeoff", own.on_ground and ctx.aligned_on_runway and own.gs_kt >= self.th.takeoff_gs_kt, own.t, self.th.takeoff_s
        )

    def _arrival_due(self, own: OwnshipState, ctx: PositionContext) -> str | None:
        th = self.th
        if self._held("descent", own.vs_fpm < th.descent_vs_fpm, own.t, th.descent_s):
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
            if self._held("taxi", own.on_ground and own.gs_kt > th.taxi_start_kt, t, th.taxi_start_s):
                return FlightPhase.TAXI_OUT, "started moving"

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
                level = abs(own.alt_indicated_ft - self.cruise_ft) <= th.cruise_alt_tol_ft and abs(own.vs_fpm) < th.level_vs_fpm
                if self._held("cruise", level, t, th.cruise_level_s):
                    return FlightPhase.CRUISE, f"level at {self.cruise_ft:.0f} ft"
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
