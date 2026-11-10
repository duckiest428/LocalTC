"""The rest of dynamic routing: vectors to lose height, spacing speeds, a turn for traffic, and the descent stepped for the
arrival flow. As in ``routing``: the geometry and whether a clearance is valid are worked out here, each one kept as an
``Amendment`` or a ``Condition`` and watched. The words are the templates' (or the model's, saying the same).

- **Vectors for descent**: coming down, but well above where it should be with the miles left: a turn off the route to give
  it the room, a heading that's clear of the terrain at its altitude; once it's back on the profile, direct to the next fix.
- **Speed for spacing**: closing on an arrival ahead to the same airport: "reduce to 250 for spacing", until the gap's back.
  Pulling away from the one behind in a busy flow, nothing's said. Kept as a ``spacing`` condition.
- **A turn for traffic**: level traffic ahead, near this flight's level, on a course that comes within ``TRAFFIC_MISS_NM``:
  "turn 20 degrees right for traffic", and direct to the next fix once it's by.
- **Levels for the flow**: a centre with arrivals stacked ahead holds the descent at ``FLOW_LEVEL_FT`` first ("descend FL240
  for the arrival flow"), and clears it the rest of the way when the flow's thinned or its time is up.

``validate`` is what every route decision goes through: no fix that doesn't exist, no line through a hazard at its level, no
altitude below the terrain. Terrain or hazards that aren't known never make something invalid.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from localtc.atc_core.conditions import Condition
from localtc.atc_core.phase import FlightPhase as P
from localtc.atc_core.phraseology import speech
from localtc.atc_core.routing import Amendment
from localtc.atc_core.values import Phrase
from localtc.sim_api import OwnshipState
from localtc.sim_api.geo import bearing_deg, haversine_nm

if TYPE_CHECKING:
    from localtc.atc_core.facilities import Facility

TOO_HIGH_FT = 4000.0  # this far above the profile
BACK_ON_PROFILE_FT = 1500.0
DESCENT_VECTOR_DEG = 40
DESCENT_VECTOR_FROM_NM, DESCENT_VECTOR_TO_NM = 25.0, 90.0
FT_PER_NM = 320.0
VECTOR_EVERY_S = 300.0
SPACING_NM = 8.0  # closing on the one ahead inside this
SPACING_OPEN_NM = 11.0  # ... until it's this far ahead again
SPACING_CLOSING_KT = 25.0
SPACING_KT = 250
SPACING_LEVEL_FT = 5000.0  # in the same stream: an arrival 20,000 ft below isn't one to space behind
SPACING_BELOW_FT = 25000.0
SPACING_EVERY_S = 300.0
TRAFFIC_LOOK_NM = 30.0
TRAFFIC_LEVEL_FT = 900.0
TRAFFIC_MISS_NM = 5.0
TRAFFIC_TURN_DEG = 20
TRAFFIC_EVERY_S = 900.0
TRAFFIC_AFTER_CALL_S = 30.0
TRAFFIC_MAX_S = 420.0
FLOW_LEVEL_FT = 24000
FLOW_DEMAND = 3  # this many ahead, within the arrival's demand area
FLOW_HOLD_S = 240.0
MOUNTAIN_EVERY_S = 900.0


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


class FlowMixin:
    """The engine's side (``AtcEngine``)."""

    amendments: list[Amendment]
    _flow_t: dict[str, float]

    # --- is a route decision valid --------------------------------------------------------------------------------

    def validate(self: Any, own: OwnshipState, *, fix: str | None = None, altitude_ft: int | None = None,
                 heading_true: float | None = None) -> Verdict:
        """Whether a route, altitude or heading can be cleared, deterministically. What isn't known isn't held against it."""
        point = None
        if fix is not None:
            point = next(((p[1], p[2]) for p in self._route_points() if p[0] == fix), None)
            known = self._known_fixes()
            if point is None and known and fix not in known:
                return Verdict(False, f"{fix} isn't on this flight's route or procedures")
            level = altitude_ft or own.alt_indicated_ft
            if point is not None and self._hazards_on((own.lat, own.lon), point, level):
                return Verdict(False, f"the line to {fix} crosses a hazard at this level")
        if self.terrain:
            level = altitude_ft if altitude_ft is not None else int(own.alt_indicated_ft)
            if heading_true is not None:
                floor = self.terrain.corridor_ft(own.lat, own.lon, heading_true, 15.0)
                if floor is not None and level < floor:
                    return Verdict(False, f"terrain: {floor:,} ft needed on that heading")
            elif point is not None:
                floor = self.terrain.route_corridor_ft([(own.lat, own.lon), point])
                if floor is not None and level < floor:
                    return Verdict(False, f"terrain: {floor:,} ft needed on the way to {fix}")
            elif altitude_ft is not None:
                floor = self._minimum_here(own)
                if floor is not None and altitude_ft < floor:
                    return Verdict(False, f"terrain: the minimum here is {floor:,} ft")
        return Verdict(True)

    def _minimum_here(self: Any, own: OwnshipState) -> int | None:
        """The minimum safe altitude here for this phase and region."""
        if not self.terrain:
            return None
        geo = self.geometry(self.state.flight.destination) or self.geometry(self.state.flight.origin)
        near = geo.distance_nm(own.lat, own.lon) if geo is not None else None
        phase = "enroute" if self.state.phase is not None and P(self.state.phase) in (P.CRUISE, P.DEPARTURE) and (near is None or near > 40) else "terminal"
        return self.terrain.minimum_safe_ft(own.lat, own.lon, phase=phase, icao=self.region.icao, airport_nm=near)

    def _safe_heading(self: Any, own: OwnshipState, wanted_true: float, altitude_ft: float, miles: float = 15.0) -> float | None:
        """The nearest heading to ``wanted_true`` clear of the terrain at ``altitude_ft``; None when there isn't one."""
        if not self.terrain:
            return wanted_true
        return self.terrain.safe_heading(own.lat, own.lon, wanted_true, altitude_ft, miles)

    def _route_floor(self: Any, own: OwnshipState) -> int | None:
        """The terrain's minimum along the route still to fly to the arrival, for keeping the flight high until it's safe."""
        if not self.terrain:
            return None
        points = self._route_points()
        i = self._next_fix(own, points)
        if i is None:
            return None
        dest = self.state.flight.destination
        line = [(own.lat, own.lon)] + [(p[1], p[2]) for p in points[i:] if p[0] != dest][:6]
        return self.terrain.route_corridor_ft(line) if len(line) >= 2 else None

    # --- watching ---------------------------------------------------------------------------------------------------

    def _flow_due(self: Any, key: str, t: float, every: float) -> bool:
        if t - self._flow_t.get(key, -math.inf) < every:
            return False
        self._flow_t[key] = t
        return True

    def _flow_watch(self: Any, own: OwnshipState, facility: "Facility | None") -> bool:
        """One of these, when it's due. True when ATC spoke."""
        st = self.state
        if facility is None or st.phase is None or own.on_ground or st.pending is not None or self._going_around \
                or "emergency" in st.flags or self.diversion.target is not None:
            return False
        if facility.controller not in ("center", "approach", "departure"):
            return False
        return self._mountain_warning(own, facility) or self._flow_level_release(own, facility) \
            or self._flow_amendments(own, facility) or self._vectors_for_descent(own, facility) \
            or self._spacing(own, facility) or self._traffic_turn(own, facility)

    def _mountain_warning(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        """Below or near the minimum in mountains: said once in a while, before it's a low altitude alert."""
        if not self.terrain or not self.terrain.mountainous(own.lat, own.lon) or own.vs_fpm > -200:
            return False
        minimum = self._minimum_here(own)
        if minimum is None or own.alt_indicated_ft > minimum + 2000 or not self._flow_due("mountain", own.t, MOUNTAIN_EVERY_S):
            return False
        self._pending_alerts.append(self._alert(own.t, "terrain", f"mountainous terrain, minimum {minimum} ft"))
        self._schedule(own.t, "common.terrain_caution", {"altitude": minimum}, facility, delay=False, expects_readback=False)
        return True

    def _vectors_for_descent(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        st, t = self.state, own.t
        if "descend" not in st.flags or "approach" in st.clearances or self._being_vectored(own) or self._holding() \
                or any(a.state == "active" and a.kind == "vectors_descent" for a in self.amendments):
            return False
        geo = self.geometry(st.flight.destination)
        if geo is None or own.vs_fpm > -500 or P(st.phase) not in (P.ARRIVAL, P.CRUISE):
            return False
        miles = geo.distance_nm(own.lat, own.lon)
        above = own.alt_indicated_ft - (geo.airport.elev_ft + miles * FT_PER_NM)
        if not DESCENT_VECTOR_FROM_NM <= miles <= DESCENT_VECTOR_TO_NM or above < TOO_HIGH_FT:
            return False
        if not self._flow_due("vectors_descent", t, VECTOR_EVERY_S):
            return False
        points = self._route_points()
        i = self._next_fix(own, points)
        fix = points[i][0] if i is not None and points[i][0] != st.flight.destination else ""
        # Away from the airport's side, to lengthen the way there.
        wanted = own.hdg_true + DESCENT_VECTOR_DEG * (-1 if ((bearing_deg(own.lat, own.lon, geo.airport.lat, geo.airport.lon) - own.hdg_true) % 360) < 180 else 1)
        heading_true = self._safe_heading(own, wanted, st.assignments.altitude_ft or own.alt_indicated_ft)
        if heading_true is None or not self.validate(own, heading_true=heading_true,
                                                     altitude_ft=int(own.alt_indicated_ft)).ok:
            return False
        magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180
        heading = int(round((heading_true - magvar) % 360)) % 360 or 360
        turn = "right" if ((heading - own.hdg_mag) % 360) < 180 else "left"
        self.amendments.append(Amendment("vectors_descent", t, fix=fix, heading=heading, side=turn, reason=f"{above:,.0f} ft above the profile"))
        self._ops_changed = True
        self._schedule(t, "common.vectors_descent", {"turn": turn, "heading": heading}, facility, delay=False,
                       on_issue=lambda: self._assign(heading=heading))
        return True

    def _spacing(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        st, t = self.state, own.t
        if self.conditions.active("spacing") is not None or "approach" in st.clearances or P(st.phase) not in (P.ARRIVAL, P.CRUISE):
            return False
        if not 11000 <= own.alt_indicated_ft <= SPACING_BELOW_FT or own.ias_kt <= SPACING_KT + 10 or facility.controller == "departure":
            return False
        ahead = self._closing_ahead(own)
        if ahead is None or not self._flow_due("spacing", t, SPACING_EVERY_S):
            return False
        target, miles = ahead
        described = self._described(target, "arriving")
        self.conditions.add(Condition("spacing", t, "common.speed_spacing", target.object_id, described.display, value=SPACING_KT, until_t=t + 900.0))
        self._schedule(t, "common.speed_spacing", {"speed": SPACING_KT, "message": Phrase(f"{described.display} {round(miles)} miles ahead",
                                                                                           f"{described.spoken} {speech.number_words(round(miles))} miles ahead")},
                       facility, delay=False, on_issue=lambda: self._assign(speed_kt=SPACING_KT))
        return True

    def _closing_ahead(self: Any, own: OwnshipState) -> tuple[Any, float] | None:
        """An arrival ahead to the same airport inside the spacing, that this flight's catching."""
        got = [(d, target) for d, target in self._arrivals_ahead(own)]
        geo = self.geometry(self.state.flight.destination)
        if geo is None:
            return None
        mine = geo.distance_nm(own.lat, own.lon)
        for d, target in reversed(got):
            gap = haversine_nm(own.lat, own.lon, target.lat, target.lon)
            if gap <= SPACING_NM and mine - d > 2.0 and own.gs_kt - target.gs_kt >= SPACING_CLOSING_KT \
                    and abs(target.alt_ft - own.alt_msl_ft) <= SPACING_LEVEL_FT:
                return target, gap
        return None

    def _watch_spacing(self: Any, c: Condition, own: OwnshipState) -> bool:
        """The gap's opened (or it's gone): back to the pilot's own speed."""
        target = self._traffic.get(c.target) if c.target is not None else None
        tuned = self.state.comms.tuned
        gap = haversine_nm(own.lat, own.lon, target.lat, target.lon) if target is not None else math.inf
        if target is not None and not target.on_ground and gap < SPACING_OPEN_NM:
            if own.ias_kt > c.value + 20 and own.t - c.issued_t > 120.0 and not c.reminded:
                c.reminded = True
                self.conditions.end(c, "contradicted", own.t, "still fast", next_instruction="common.speed_spacing")
                if tuned is not None:
                    self._schedule(own.t, "common.speed_spacing_again", {"speed": c.value}, tuned, delay=False)
                    return True
            return False
        self.conditions.end(c, "satisfied" if target is not None else "expired", own.t, "the gap's open" if target is not None else "the traffic is gone",
                            next_instruction="common.speed_resume")
        if tuned is not None and tuned.controller in ("center", "approach"):
            self._schedule(own.t, "common.speed_resume", {}, tuned, delay=False, expects_readback=False,
                           on_issue=lambda: self._assign(speed_kt=None))
            return True
        return False

    def _traffic_turn(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        st, t = self.state, own.t
        if P(st.phase) is not P.CRUISE or facility.controller != "center" or any(a.state == "active" and a.kind == "traffic" for a in self.amendments):
            return False
        conflict = self._conflict_ahead(own)
        if conflict is None:
            return False
        target, _ = conflict
        told = self._traffic_called.get(target.object_id)
        if told is None or t - told < TRAFFIC_AFTER_CALL_S:
            return False  # the traffic called first ("traffic, 12 o'clock, 8 miles"), the turn only if it's still coming
        side = "right" if ((bearing_deg(own.lat, own.lon, target.lat, target.lon) - own.hdg_true) % 360) > 180 else "left"
        wanted = own.hdg_true + (TRAFFIC_TURN_DEG if side == "right" else -TRAFFIC_TURN_DEG)
        heading_true = self._safe_heading(own, wanted, own.alt_indicated_ft)
        if heading_true is None or not self._flow_due("traffic", t, TRAFFIC_EVERY_S):
            return False
        points = self._route_points()
        i = self._next_fix(own, points)
        fix = points[min(i + 1, len(points) - 1)][0] if i is not None else ""
        described = self._described(target, "opposite")
        self.amendments.append(Amendment("traffic", t, fix=fix, side=side, degrees=TRAFFIC_TURN_DEG, reason=described.display, hazard=str(target.object_id)))
        self._ops_changed = True
        said = Phrase(f"{TRAFFIC_TURN_DEG} degrees {side}", f"{speech.number_words(TRAFFIC_TURN_DEG)} degrees {side}")
        self._schedule(t, "common.turn_for_traffic", {"message": said}, facility, delay=False)
        return True

    def _conflict_ahead(self: Any, own: OwnshipState) -> tuple[Any, float] | None:
        """Traffic near this level that will come within ``TRAFFIC_MISS_NM``: (it, the closest it'll come)."""
        for target in self._traffic.values():
            if target.on_ground or abs(target.alt_ft - own.alt_msl_ft) > TRAFFIC_LEVEL_FT:
                continue
            miles = haversine_nm(own.lat, own.lon, target.lat, target.lon)
            if miles > TRAFFIC_LOOK_NM:
                continue
            # In a flat frame around us: nm east and north, and how each is moving.
            b = math.radians(bearing_deg(own.lat, own.lon, target.lat, target.lon))
            x, y = miles * math.sin(b), miles * math.cos(b)
            mine, theirs = math.radians(own.hdg_true), math.radians(target.hdg_true)
            vx = target.gs_kt * math.sin(theirs) - own.gs_kt * math.sin(mine)
            vy = target.gs_kt * math.cos(theirs) - own.gs_kt * math.cos(mine)
            speed2 = vx * vx + vy * vy
            if speed2 < 1.0:
                continue
            when = -(x * vx + y * vy) / speed2
            if when <= 0 or when > 0.25:
                continue
            closest = math.hypot(x + vx * when, y + vy * when)
            if closest < TRAFFIC_MISS_NM:
                return target, closest
        return None

    def _flow_amendments(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        """Those watched for their end: back on the profile, or the traffic by."""
        t = own.t
        for a in [a for a in self.amendments if a.state == "active" and a.kind in ("vectors_descent", "traffic")]:
            over = False
            if a.kind == "vectors_descent":
                geo = self.geometry(self.state.flight.destination)
                if geo is not None:
                    miles = geo.distance_nm(own.lat, own.lon)
                    over = own.alt_indicated_ft - (geo.airport.elev_ft + miles * FT_PER_NM) < BACK_ON_PROFILE_FT
                if self._being_vectored(own):
                    a.state = "cancelled"
                    self._ops_changed = True
                    continue
            else:
                target = next((x for x in self._traffic.values() if str(x.object_id) == a.hazard), None)
                over = target is None or t - a.issued_t >= TRAFFIC_MAX_S or self._conflict_ahead(own) is None and t - a.issued_t > 90.0
            if over:
                a.state = "done"
                self._ops_changed = True
                if a.fix and self.validate(own, fix=a.fix).ok:
                    self._schedule(t, "common.direct", {"fix": a.fix}, facility, delay=False, on_issue=lambda: self._assign(heading=None))
                    return True
        return False

    def _flow_level(self: Any, own: OwnshipState, wanted: int) -> int:
        """The first step of a descent, where the arrival flow says hold it up."""
        if wanted >= FLOW_LEVEL_FT or own.alt_indicated_ft <= FLOW_LEVEL_FT + 1000 or len(self._arrivals_ahead(own)) < FLOW_DEMAND:
            return wanted
        self.amendments.append(Amendment("level", own.t, reason=f"{len(self._arrivals_ahead(own))} arrivals ahead", degrees=wanted))
        self._ops_changed = True
        return FLOW_LEVEL_FT

    def _flow_level_release(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        for a in [a for a in self.amendments if a.state == "active" and a.kind == "level"]:
            if own.t - a.issued_t < FLOW_HOLD_S and len(self._arrivals_ahead(own)) >= FLOW_DEMAND:
                continue
            a.state = "done"
            self._ops_changed = True
            altitude = int(a.degrees or 0)
            if altitude and self.validate(own, altitude_ft=altitude).ok:
                self._schedule(own.t, "common.descend", {"altitude": altitude}, facility, delay=False,
                               on_issue=lambda: self._assign(altitude_ft=altitude))
                return True
        return False
