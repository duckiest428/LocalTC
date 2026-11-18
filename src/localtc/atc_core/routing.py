"""Dynamic routing: the route geometry and each amendment to it decided here, deterministically; the words are the
templates' (or the model's, saying the same).

- **Shortcuts**: a quiet centre gives "proceed direct" a fix further down the route when it saves real miles and the
  line to it is clear of the hazards it knows (``SHORTCUT_SAVES_NM``), once a sector.
- **Weather deviations**: asked for ("request deviation 20 degrees left for weather"), approved with "when able,
  proceed direct" the next fix beyond it; with a convective SIGMET along the route ahead at the flight's level,
  the controller says so and approves deviations either side before being asked.
- **Reroutes**: the route ahead through an active TFR or a restricted, prohibited or danger area at the flight's
  level: "turn left heading 040, vectors around the restricted area, when able proceed direct BLAKO"; once the line
  to that fix is clear of it, "proceed direct BLAKO".
- **The arrival's crossing restriction**: without a STAR, the descent clearance gives the fix 30 to 60 miles out to
  cross at the arrival altitude.

Every amendment is kept (``Amendment``) and sent with the ATC's operations to the maps and the replay.
"""

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from localtc.atc_core.phase import FlightPhase as P
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Phrase
from localtc.sim_api import OpsSnapshot, OwnshipState

if TYPE_CHECKING:
    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation

SHORTCUT_SAVES_NM = 15.0  # (8 had a direct every sector, "for what reason??")
SHORTCUT_MAX_NM = 250.0  # the fix given direct is no further than this
SHORTCUT_EVERY_S = 1800.0
SHORTCUT_MIN_FIXES = 2  # the fix skipped to is at least this many down the route
HAZARD_AHEAD_NM = 150.0  # hazards on the route this far ahead are told
FIX_REACHED_NM = 20.0  # this close to one of the route's fixes in the air: reached, the route's progress past it
OFF_COURSE_NM = 30.0  # this far off the route in the cruise, nothing given to explain it: "say intentions"
OFF_COURSE_AGAIN_S = 600.0
OFF_COURSE_MAX = 3  # times one controller asks
HAZARD_LEVEL_MARGIN_FT = 2000
AVOID_KINDS = ("tfr", "restricted", "prohibited", "danger")
CONVECTIVE = ("TS", "CONVECTIVE", "TC", "CB")
REROUTE_MARGIN_NM = 5.0
DEVIATION_REMIND_S = 900.0
CROSS_FROM_NM, CROSS_TO_NM = 30.0, 60.0


@dataclass
class Hazard:
    id: str
    kind: str  # sigmet, airmet, tfr, restricted, prohibited, danger, ...
    name: str
    polygon: tuple[tuple[float, float], ...]
    floor_ft: int | None = None
    ceiling_ft: int | None = None
    hazard: str = ""  # a SIGMET's: TS, TURB, ICE, ...

    def at_level(self, altitude_ft: float) -> bool:
        low = (self.floor_ft or 0) - HAZARD_LEVEL_MARGIN_FT
        high = (self.ceiling_ft if self.ceiling_ft is not None else 99999) + HAZARD_LEVEL_MARGIN_FT
        return low <= altitude_ft <= high

    def contains(self, lat: float, lon: float) -> bool:
        inside = False
        pts = self.polygon
        for i in range(len(pts)):
            (y1, x1), (y2, x2) = pts[i], pts[i - 1]
            if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
                inside = not inside
        return inside

    def crosses(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """The line from ``a`` to ``b`` (lat, lon) through it."""
        if self.contains(*a) or self.contains(*b):
            return True
        pts = self.polygon
        return any(_segments_cross(a, b, pts[i - 1], pts[i]) for i in range(len(pts)))

    def centre(self) -> tuple[float, float]:
        n = max(1, len(self.polygon))
        return sum(p[0] for p in self.polygon) / n, sum(p[1] for p in self.polygon) / n

    def radius_nm(self) -> float:
        c = self.centre()
        return max((_nm(c[0], c[1], p[0], p[1]) for p in self.polygon), default=0.0)

    def said(self) -> str:
        if self.kind in ("sigmet", "airmet"):
            what = {"TS": "convective weather", "CONVECTIVE": "convective weather", "TURB": "turbulence", "ICE": "icing",
                    "VA": "volcanic ash", "TC": "a tropical cyclone", "MTW": "mountain waves"}.get(self.hazard.upper(),
                                                                                                 "significant weather")
            return what
        return {"tfr": "a temporary flight restriction", "restricted": "restricted airspace",
                "prohibited": "prohibited airspace", "danger": "a danger area"}.get(self.kind, "an airspace restriction")


@dataclass
class Amendment:
    kind: str  # shortcut, deviation, reroute, crossing
    issued_t: float
    fix: str = ""
    heading: int | None = None
    side: str = ""
    degrees: int | None = None
    reason: str = ""
    state: str = "active"  # active, done, cancelled
    line: list[tuple[float, float]] = field(default_factory=list)  # for the maps: from where, to the fix
    reminded: bool = False
    hazard: str = ""

    def to_json(self) -> dict:
        return {"kind": self.kind, "issued_t": self.issued_t, "fix": self.fix, "heading": self.heading, "side": self.side,
                "degrees": self.degrees, "reason": self.reason, "state": self.state, "line": [list(p) for p in self.line],
                "hazard": self.hazard}


def hazards_from(ev: OpsSnapshot) -> list[Hazard]:
    """The areas in an ``OpsSnapshot`` of kind "hazards" (published by the ops service, active now)."""
    try:
        rows = json.loads(ev.data or "[]")
    except ValueError:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            out.append(Hazard(str(r.get("id", "")), str(r.get("kind", "")), str(r.get("name", "")),
                              tuple((float(p[0]), float(p[1])) for p in r.get("polygon", ())),
                              r.get("floor_ft"), r.get("ceiling_ft"), str(r.get("hazard", ""))))
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
    return [h for h in out if len(h.polygon) >= 3]


class RoutingMixin:
    """The engine's side (``AtcEngine``)."""

    hazards: list[Hazard]
    amendments: list[Amendment]
    _shortcut_t: float
    _hazards_told: set[str]

    # --- the route ahead -----------------------------------------------------------------------------------------

    def _route_points(self) -> list[tuple[str, float, float]]:
        fixes = [(f.ident, f.lat, f.lon) for f in self.route.fixes] if self.route else []
        dest = self.geometry(self.state.flight.destination)
        if dest is not None:
            fixes.append((self.state.flight.destination or "", dest.airport.lat, dest.airport.lon))
        return fixes

    def _next_fix(self, own: OwnshipState, points: list[tuple[str, float, float]]) -> int | None:
        """The route's next fix ahead of the aircraft (the first one not behind it), never one before a fix it has
        already had ahead of it in the air: an A350 turned back over the Atlantic (its autopilot gone) was given
        "direct EBMOS" and "direct MOBUB", the route's first fixes, back over Quebec."""
        if not points:
            return None
        floor = self._track_route(own, points)
        best, best_d = None, math.inf
        for i, (_, lat, lon) in enumerate(points):
            if i < floor:
                continue
            d = _nm(own.lat, own.lon, lat, lon)
            ahead = abs(((_bearing(own.lat, own.lon, lat, lon) - own.hdg_true + 540) % 360) - 180) < 100
            if ahead and d < best_d:
                best, best_d = i, d
        return best if best is None or best >= floor else None

    def _track_route(self, own: OwnshipState, points: list[tuple[str, float, float]] | None = None) -> int:
        """The route's progress: the last of its fixes the aircraft has flown within ``FIX_REACHED_NM`` of, in the air
        (only fixes really reached: the nearest "ahead" while turning was 1,400 miles down the route). Kept up every
        second (``_monitor``), so none is flown by unseen; on the ground, 0."""
        if own.on_ground:
            return 0
        points = self._route_points() if points is None else points
        floor = getattr(self, "_route_reached", 0)
        for i in range(floor, len(points)):
            if _nm(own.lat, own.lon, points[i][1], points[i][2]) < FIX_REACHED_NM:
                floor = i
        self._route_reached = floor
        return floor

    def _off_route_nm(self, own: OwnshipState) -> float | None:
        """How far the aircraft is from the rest of its route (the leg before the next fix and every one after), or
        None with no route to go by."""
        points = self._route_points()
        floor = getattr(self, "_route_reached", 0)
        legs = points[floor:]
        if len(legs) < 2:
            return None
        return min(_leg_nm(own.lat, own.lon, a[1], a[2], b[1], b[2]) for a, b in zip(legs, legs[1:]))

    def _off_course(self, own: OwnshipState, facility: "Facility") -> bool:
        """In the cruise, well off the route with nothing given to explain it (no heading, direct, deviation or
        reroute): "say intentions", and again every ``OFF_COURSE_AGAIN_S`` while it stays off."""
        st, t = self.state, own.t
        if facility.controller != "center" or st.pending is not None or P(st.phase) is not P.CRUISE or self._vfr \
                or st.assignments.heading is not None or self._holding() or self.diversion.target is not None \
                or any(a.state == "active" for a in self.amendments) \
                or t - getattr(self, "_off_course_t", -math.inf) < OFF_COURSE_AGAIN_S:
            return False
        off = self._off_route_nm(own)
        if off is None or off < OFF_COURSE_NM:
            return False
        told = self.__dict__.setdefault("_off_course_told", {})
        if told.get(facility.station, 0) >= OFF_COURSE_MAX:
            return False  # asked three times, nothing heard: left (it went on for hours)
        told[facility.station] = told.get(facility.station, 0) + 1
        self._off_course_t = t
        self._schedule(t, "center.off_course", {"distance": int(round(off))}, facility, delay=False,
                       expects_readback=False)
        return True

    def _fix_passed(self, own: OwnshipState, fix: str) -> bool:
        """The route's ``fix`` is behind the aircraft now (or it's over it)."""
        points = self._route_points()
        index = next((i for i, p in enumerate(points) if p[0] == fix), None)
        if index is None:
            return False
        if _nm(own.lat, own.lon, points[index][1], points[index][2]) < 3.0:
            return True
        ahead = self._next_fix(own, points)
        return ahead is not None and ahead > index

    def _hazards_on(self, a: tuple[float, float], b: tuple[float, float], altitude_ft: float,
                    kinds: tuple[str, ...] | None = None) -> list[Hazard]:
        return [h for h in self.hazards if (kinds is None or h.kind in kinds) and h.at_level(altitude_ft) and h.crosses(a, b)]

    # --- shortcuts ---------------------------------------------------------------------------------------------

    def _shortcut(self, own: OwnshipState, facility: "Facility") -> bool:
        """A quiet centre's "proceed direct": a fix further down the route, when it saves real miles."""
        st, t = self.state, own.t
        if facility.controller != "center" or st.pending is not None or t - self._shortcut_t < SHORTCUT_EVERY_S \
                or P(st.phase) not in (P.CRUISE,) or self._workload(facility, t) == "busy" or self._vfr \
                or st.assignments.heading is not None or any(a.state == "active" and a.kind in ("deviation", "reroute")
                                                                for a in self.amendments):
            return False
        points = self._route_points()
        i = self._next_fix(own, points)
        if i is None:
            return False
        level = st.assignments.altitude_ft or own.alt_indicated_ft
        best = None
        # Never the destination itself (SimBrief's route ends with it, as the STAR's last fix: "cleared direct KDEN"
        # with the LAWGR3 filed), nor the top of climb or descent.
        not_fixes = {st.flight.origin, st.flight.destination, "TOC", "TOD"}
        for j in range(i + SHORTCUT_MIN_FIXES, len(points) - 1):
            ident, lat, lon = points[j]
            if ident.upper() in not_fixes or ident.upper() in self.__dict__.setdefault("_directs_given", set()):
                continue  # (never the same one twice: "direct SOTIV", and SOTIV again fifteen minutes on)
            direct = _nm(own.lat, own.lon, lat, lon)
            if direct > SHORTCUT_MAX_NM:
                break
            along = _nm(own.lat, own.lon, points[i][1], points[i][2]) + sum(
                _nm(points[k][1], points[k][2], points[k + 1][1], points[k + 1][2]) for k in range(i, j))
            saves = along - direct
            if saves >= SHORTCUT_SAVES_NM and not self._hazards_on((own.lat, own.lon), (lat, lon), level) \
                    and self._star_start_ok(ident):
                best = (saves, j, ident, lat, lon)
        if best is None:
            return False
        saves, j, ident, lat, lon = best
        self._shortcut_t = t
        self._directs_given.add(ident.upper())
        # The fixes before it are passed by: the route's progress goes on from it (its next fix stayed the one before,
        # and SOTIV saved as many miles again a sector later)
        self._route_reached = max(getattr(self, "_route_reached", 0), j - 1)
        self.amendments.append(Amendment("shortcut", t, fix=ident, reason=f"saves {saves:.0f} nm",
                                         line=[(own.lat, own.lon), (lat, lon)]))
        self._ops_changed = True
        self._schedule(t, "common.direct", {"fix": ident}, facility, delay=False)
        return True

    def _star_start_ok(self, ident: str) -> bool:
        """Not past the start of the filed STAR (its fixes are the arrival's to fly)."""
        if not self.cfg.star:
            return True
        star = [f.ident for f in self.route.procedure(self.cfg.star, "DSC")]
        filed = [f.ident for f in self.route.fixes if (f.via or "").upper() == self.cfg.star.upper()]  # the plan's
        # Where it starts (the fix before its first in the plan, VUTEB for VUTE3E) at most: never a fix on it, its
        # first included (Bahrain and Doha both gave "direct DB400", the VUTE3E's first after VUTEB).
        return ident not in star[1:] and ident not in filed

    # --- hazards ahead -----------------------------------------------------------------------------------------

    def _hazards_ahead(self, own: OwnshipState, facility: "Facility") -> bool:
        """The route ahead through a convective SIGMET (deviations approved) or an active restricted area (vectors
        around it), at the flight's level: said once each."""
        st, t = self.state, own.t
        if facility.controller not in ("center", "departure", "approach") or st.pending is not None or own.on_ground \
                or self._vfr or self._being_vectored(own):
            return False  # (at Fulton County: "turn left heading 148" around a TFR, "fly heading 265" 30 s later)
        points = self._route_points()
        i = self._next_fix(own, points)
        if i is None:
            return False
        level = st.assignments.altitude_ft or own.alt_indicated_ft
        here = (own.lat, own.lon)
        path = [here, *[(p[1], p[2]) for p in points[i:]]]
        travelled = 0.0
        for k in range(len(path) - 1):
            a, b = path[k], path[k + 1]
            for h in self._hazards_on(a, b, level):
                if h.id in self._hazards_told:
                    continue
                if travelled + _nm(a[0], a[1], *_entry(h, a, b)) > HAZARD_AHEAD_NM:
                    continue  # on a long leg, but far down it: "about 840 miles ahead" was said over Florida
                if h.kind in AVOID_KINDS:
                    return self._reroute(own, facility, h, points, i)
                if h.kind in ("sigmet",) and h.hazard.upper() in CONVECTIVE:
                    if any(x.kind == "deviation" and x.state == "active" for x in self.amendments):
                        self._hazards_told.add(h.id)  # deviations already approved: the next SIGMET along it isn't news
                        continue
                    return self._weather_ahead(own, facility, h, points, i, travelled + _nm(a[0], a[1], *_entry(h, a, b)))
            travelled += _nm(a[0], a[1], b[0], b[1])
            if travelled > HAZARD_AHEAD_NM:
                break
        return False

    def _beyond(self, h: Hazard, points: list[tuple[str, float, float]], i: int, level: float) -> tuple[str, float, float] | None:
        """The route's first fix past the hazard: after the first leg through it, the first fix outside it."""
        own = self.state.aircraft
        prev = (own.lat, own.lon) if own is not None else (points[i][1], points[i][2])
        through = False
        for j in range(i, len(points)):
            ident, lat, lon = points[j]
            through = through or h.crosses(prev, (lat, lon))
            if through and not h.contains(lat, lon):
                return ident, lat, lon
            prev = (lat, lon)
        return None

    def _weather_ahead(self, own: OwnshipState, facility: "Facility", h: Hazard, points, i: int, miles: float) -> bool:
        t = own.t
        self._hazards_told.add(h.id)
        beyond = self._beyond(h, points, i, own.alt_indicated_ft)
        fix = beyond[0] if beyond is not None else ""
        n = max(5, int(round(miles / 5.0)) * 5)
        display = f"convective SIGMET along your route, about {n} miles ahead, deviations left or right of course approved" + \
            (f", when able proceed direct {fix}" if fix else "")
        spoken = f"convective sigmet along your route, about {speech.miles(n)} miles ahead, deviations left or right " \
                 f"of course approved" + (f", when able proceed direct {fix}" if fix else "")
        self.amendments.append(Amendment("deviation", t, fix=fix, side="either", reason="convective SIGMET",
                                         hazard=h.id, line=[(own.lat, own.lon)] + ([(beyond[1], beyond[2])] if beyond else [])))
        self._ops_changed = True
        self._schedule(t, "common.info", {"message": Phrase(display, spoken)}, facility, delay=False, expects_readback=False)
        return True

    def _reroute(self, own: OwnshipState, facility: "Facility", h: Hazard, points, i: int) -> bool:
        """Vectors around an active area on the route: a heading to a point beside it, the side the shorter way."""
        t = own.t
        self._hazards_told.add(h.id)
        beyond = self._beyond(h, points, i, own.alt_indicated_ft)
        if beyond is None:
            return False
        cy, cx = h.centre()
        r = h.radius_nm() + REROUTE_MARGIN_NM
        track = _bearing(own.lat, own.lon, beyond[1], beyond[2])
        best = None
        for side in (-90, 90):
            b = math.radians(track + side)
            plat = cy + r / 60.0 * math.cos(b)
            plon = cx + r / 60.0 * math.sin(b) / max(0.1, math.cos(math.radians(cy)))
            length = _nm(own.lat, own.lon, plat, plon) + _nm(plat, plon, beyond[1], beyond[2])
            if not h.crosses((own.lat, own.lon), (plat, plon)) and (best is None or length < best[0]):
                best = (length, side, plat, plon)
        if best is None:
            return False
        _, side, plat, plon = best
        heading = self._magnetic(own, _bearing(own.lat, own.lon, plat, plon))
        turn = self._turn_towards(own.hdg_mag, heading) or ("right" if side > 0 else "left")
        what = h.said()
        name = f" {h.name}" if h.name and h.kind != "tfr" else ""
        self.amendments.append(Amendment("reroute", t, fix=beyond[0], heading=heading, side=turn, hazard=h.id,
                                         reason=f"{what}{name}",
                                         line=[(own.lat, own.lon), (plat, plon), (beyond[1], beyond[2])]))
        self._ops_changed = True
        self._schedule(t, "center.reroute", {"heading": heading, "turn": turn, "fix": beyond[0],
                                             "message": Phrase(f"vectors around {what}{name}", f"vectors around {what}{name}")},
                       facility, delay=False, on_issue=lambda: self._assign(heading=heading))
        return True

    def _watch_amendments(self, own: OwnshipState, facility: "Facility | None") -> bool:
        """A reroute's way clear to its fix: "proceed direct"; a deviation going on a long time: asked about."""
        t = own.t
        for a in [a for a in self.amendments if a.state == "active"]:
            if a.kind == "reroute" and self._being_vectored(own):
                a.state = "cancelled"  # approach has it: no "proceed direct" after the approach clearance
                self._ops_changed = True
            elif a.kind == "reroute":
                h = next((x for x in self.hazards if x.id == a.hazard), None)
                fix = next(((p[1], p[2]) for p in self._route_points() if p[0] == a.fix), None)
                if fix is None:
                    a.state = "cancelled"
                    continue
                if h is None or not h.crosses((own.lat, own.lon), fix):
                    a.state = "done"
                    self._ops_changed = True
                    if facility is not None and facility.controller in ("center", "approach", "departure"):
                        self._schedule(t, "common.direct", {"fix": a.fix}, facility, delay=False,
                                       on_issue=lambda: self._assign(heading=None))
                        return True
            elif a.kind == "deviation" and a.fix and self._fix_passed(own, a.fix):
                # Flown past the fix it was to go direct to (it went through, or round and back on course): over.
                # "Advise when able to proceed direct HRRBR" came with HRRBR 60 miles behind.
                a.state = "done"
                self._ops_changed = True
            elif a.kind == "deviation" and not a.reminded and t - a.issued_t > DEVIATION_REMIND_S and a.fix \
                    and facility is not None and facility.controller in ("center", "approach"):
                a.reminded = True
                self._schedule(t, "center.advise_direct", {"fix": a.fix}, facility, delay=False, expects_readback=False)
                return True
            elif a.kind == "shortcut" and own.t - a.issued_t > 3600:
                a.state = "done"
        return False

    def _deviation_request(self, interp: "Interpretation", facility: "Facility", t: float, own: OwnshipState | None) -> None:
        """ "Request 20 degrees left for weather": approved, with the fix to proceed direct to after it."""
        if own is None or own.on_ground or facility.controller not in ("center", "approach", "departure"):
            self._schedule(t, "common.unable", {}, facility)
            return
        side = interp.values.get("side") or "either"
        degrees = interp.values.get("degrees")
        points = self._route_points()
        i = self._next_fix(own, points)
        fix = ""
        if i is not None:
            # The first fix 40 miles or more on: past the weather, usually.
            for j in range(i, len(points)):
                if _nm(own.lat, own.lon, points[j][1], points[j][2]) >= 40.0:
                    fix = points[j][0]
                    break
        self.amendments.append(Amendment("deviation", t, fix=fix, side=side, degrees=degrees, reason="weather",
                                         line=[(own.lat, own.lon)]))
        self._ops_changed = True
        what = f"{degrees} degrees {side}" if degrees and side != "either" else (side if side != "either" else "left and right of course")
        said = f"{speech.number_words(int(degrees))} degrees {side}" if degrees and side != "either" else what
        then_d = f", when able proceed direct {fix}" if fix else ", advise when back on course"
        then_s = f", when able proceed direct {fix}" if fix else ", advise when back on course"
        self._schedule(t, "common.info", {"message": Phrase(f"deviation {what} approved{then_d}",
                                                            f"deviation {said} approved{then_s}")}, facility,
                       expects_readback=False)

    def _back_on_course(self, interp: "Interpretation", facility: "Facility", t: float, own: OwnshipState | None) -> None:
        for a in self.amendments:
            if a.state == "active" and a.kind == "deviation":
                a.state = "done"
                self._ops_changed = True
        self._schedule(t, "common.roger", {}, facility, expects_readback=False)

    def _crossing_restriction(self, own: OwnshipState, altitude: int) -> str | None:
        """The route fix 30 to 60 miles from the destination, to cross at the arrival altitude (no STAR)."""
        dest = self.geometry(self.state.flight.destination)
        if dest is None or self.cfg.star:
            return None
        for ident, lat, lon in reversed(self._route_points()[:-1]):
            d = _nm(lat, lon, dest.airport.lat, dest.airport.lon)
            if CROSS_FROM_NM <= d <= CROSS_TO_NM and _nm(own.lat, own.lon, lat, lon) > 20.0 \
                    and abs(((_bearing(own.lat, own.lon, lat, lon) - own.hdg_true + 540) % 360) - 180) < 90:
                return ident
        return None

    def routing_json(self) -> list[dict]:
        return [a.to_json() for a in self.amendments[-20:]]

    def hazards_json(self) -> list[dict[str, Any]]:
        return [{"id": h.id, "kind": h.kind, "name": h.name, "hazard": h.hazard} for h in self.hazards]


def _segments_cross(p1, p2, q1, q2) -> bool:
    def orient(a, b, c) -> float:
        return (b[1] - a[1]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[1] - a[1])

    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def _leg_nm(lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance from a point to the leg between two others (flat, around the point: plenty for tens of miles)."""
    k = math.cos(math.radians(lat))
    ax, ay = (lon1 - lon) * 60 * k, (lat1 - lat) * 60
    bx, by = (lon2 - lon) * 60 * k, (lat2 - lat) * 60
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    u = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + u * dx, ay + u * dy)


def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 60.0
    dx = (lon2 - lon1) * 60.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = lat2 - lat1
    dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.degrees(math.atan2(dx, dy)) % 360

def _entry(h: Hazard, a: tuple[float, float], b: tuple[float, float], steps: int = 60) -> tuple[float, float]:
    """Where the leg a-b first goes into the hazard (its centre if no sample of the leg falls inside it)."""
    for k in range(steps + 1):
        lat, lon = a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps
        if h.contains(lat, lon):
            return lat, lon
    return h.centre()
