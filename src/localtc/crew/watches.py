"""More of what the copilot watches for and says by itself (mixed into ``crew.monitor.Monitor``).

- **Turbulence**, from how the load factor (G FORCE) varies over the last 20 seconds: light, moderate, severe, with
  hysteresis so a bump doesn't flap it. "Moderate turbulence." once, "Smooth again." when it's been calm a minute.
  Chatty adds the seatbelt sign; quiet hears only severe.
- **Wind shear** below 1,500 feet: the headwind (airspeed less groundspeed) changing 15 knots in a few seconds, with the
  airspeed falling or a strong sink. A safety call: "Wind shear, wind shear!"
- **Ice**: the sim's structural ice. "Picking up ice", and the anti-ice when it's off.
- **Weather ahead**: the sim gives no weather radar and no weather along the route, so the copilot says only what the
  destination's and the alternate's ATIS say (gusts, low visibility, a low ceiling, freezing precipitation), when a new
  one comes out. It never claims to see storms ahead.
- **Step climbs**: the SimBrief plan's own steps (a cruise fix planned higher than the one before), or without them
  every 5 percent of the weight burned (about 2,000 feet of optimum altitude). Suggested a few miles before; asked of ATC
  by the copilot when it works the radio, else offered ("want me to ask?"). Not when already up there, or ATC said
  "unable" lately.
- **Arrival restrictions**: the STAR's altitudes and speeds from the sim's navdata (``ArrivalData``), checked at each
  fix while "descend via" is the clearance (vectors or a plain "descend and maintain" cancel it). A missed one is said,
  and a heads-up comes before one the descent won't make.
- **Field in sight?** on the way in to a visual approach, within 10 miles and below the clouds: the pilot's "yes" goes
  to approach, which then clears the visual. At minimums, "approach lights in sight" only when the weather allows it.
- **The autobrake and reversers**: the setting confirmed before landing (a word when it's off on a short runway); after
  touchdown "reversers" (or "no reversers"), "autobrake engaged" or "autobrake off". Only for an aircraft whose
  autobrake positions are known (its profile), and reversers only where the sim says the engines are jets.
- **Wrong taxiway**: off the route ground gave by more than a taxiway's width for a few seconds, once the aircraft
  had joined it: "We're on B, cleared via G." And a turn that's leaving the route: "Not this one, we're cleared via G."
- **Diversion**: on an emergency, an engine failure or fuel below the reserve, the nearest suitable airports (runway
  long enough for the weight, an ILS and a tower counting in their favour, their weather when it's known): "Nearest
  suitable is Denver, 62 miles northeast, runway 16R, 12,000 feet, ILS. Want me to ask for it?" The pilot decides.
- **ATC's instruction repeated** (``[crew] repeat_atc``, chatty only, off by default): the key part of what ATC just
  said, in the moment before the readback; dropped if the pilot reads back first.
"""

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from localtc.atc_core import region as regions
from localtc.atc_core.airport.classes import bearing_words
from localtc.atc_core.phraseology import speech
from localtc.sim_api import ArrivalData, AtcTransmission, OwnshipState, RequestAirportData, RequestArrival
from localtc.sim_api.geo import haversine_nm

SAFETY, ROUTINE, CHATTER = 3, 2, 1  # as crew.monitor

TURB_WINDOW_S = 20.0
TURB_LEVELS = (("severe", 0.35, 1.0), ("moderate", 0.15, 0.5), ("light", 0.06, 0.25))  # (name, std g, peak g)
TURB_CALM_S = 60.0
SHEAR_WINDOW_S = 5.0
SHEAR_KT = 15.0
SHEAR_AGL = 1500.0
ICE_PCT = 5.0
STEP_AHEAD_NM = 20.0
STEP_BURN = 0.05  # of the weight, for 2,000 feet of optimum altitude (no steps in the plan)
UNABLE_QUIET_S = 1200.0
RESTRICTION_NM = 2.5  # this close to a fix is passing it
RESTRICTION_FT = 300.0
ARRIVAL_ASK_NM = 300.0
FT_PER_NM_3DEG = 318.0
SIGHT_NM = 10.0
TAXI_OFF_M = 45.0
TAXI_JOINED_M = 20.0
TAXI_OFF_S = 6.0
DIVERT_NM = 150.0
DIVERT_WAIT_S = 15.0
SHORT_RUNWAY_FT = 7000


@dataclass
class Restriction:
    fix: str
    lat: float
    lon: float
    altitude: str  # "", at, above, below, between
    alt1_ft: int
    alt2_ft: int
    speed_kt: int
    closest_nm: float = 1e9
    alt_there: float = 0.0
    ias_there: float = 0.0
    passed: bool = False

    def words(self) -> str:
        if self.altitude == "at":
            return f"the {_alt(self.alt1_ft)}"
        if self.altitude == "above":
            return f"the at or above {_alt(self.alt1_ft)}"
        if self.altitude == "below":
            return f"the at or below {_alt(self.alt1_ft)}"
        if self.altitude == "between":
            return f"the between {_alt(self.alt2_ft)} and {_alt(self.alt1_ft)}"
        return ""

    def ceiling(self) -> int | None:
        """The highest the aircraft may be at the fix, or None (only a floor there)."""
        return self.alt1_ft if self.altitude in ("at", "below", "between") else None

    def met(self, alt: float) -> bool:
        if self.altitude == "at":
            return abs(alt - self.alt1_ft) <= RESTRICTION_FT
        if self.altitude == "above":
            return alt >= self.alt1_ft - RESTRICTION_FT
        if self.altitude == "below":
            return alt <= self.alt1_ft + RESTRICTION_FT
        if self.altitude == "between":
            return self.alt2_ft - RESTRICTION_FT <= alt <= self.alt1_ft + RESTRICTION_FT
        return True


def _alt(feet: int) -> str:
    return speech.altitude_display(int(feet))


class Turbulence:
    """The load factor over the last ``TURB_WINDOW_S``: how far it strays from its mean (a steady turn doesn't count,
    its g is steady). ``add`` returns the new level when it changes for good: up at once two samples running, down
    only after ``TURB_CALM_S`` of calm."""

    def __init__(self) -> None:
        self.samples: deque[tuple[float, float]] = deque()
        self.level = "smooth"
        self._up: tuple[str, int] | None = None
        self._calm_since: float | None = None

    def add(self, t: float, g: float) -> str | None:
        self.samples.append((t, g))
        while self.samples and t - self.samples[0][0] > TURB_WINDOW_S:
            self.samples.popleft()
        if len(self.samples) < 6:
            return None
        values = [s[1] for s in self.samples]
        mean = sum(values) / len(values)
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        peak = max(abs(v - mean) for v in values)
        now = next((name for name, s, p in TURB_LEVELS if std >= s or peak >= p), "smooth")
        rank = ("smooth", "light", "moderate", "severe")
        if rank.index(now) > rank.index(self.level):
            self._calm_since = None
            count = self._up[1] + 1 if self._up is not None and self._up[0] == now else 1
            self._up = (now, count)
            if count >= 2:
                self.level, self._up = now, None
                return now
            return None
        self._up = None
        if rank.index(now) < rank.index(self.level):
            self._calm_since = self._calm_since if self._calm_since is not None else t
            if t - self._calm_since >= TURB_CALM_S:
                self.level, self._calm_since = now, None
                return now
        else:
            self._calm_since = None
        return None

    def reset(self) -> None:
        self.samples.clear()
        self.level, self._up, self._calm_since = "smooth", None, None


class WindShear:
    """The headwind (indicated airspeed less groundspeed: thrust changes both alike, wind only the first) changing by
    ``SHEAR_KT`` within ``SHEAR_WINDOW_S``, with the airspeed falling as much or a strong sink."""

    def __init__(self) -> None:
        self.samples: deque[tuple[float, float, float]] = deque()  # (t, headwind, ias)

    def add(self, t: float, ias: float, gs: float, vs: float, agl: float, airborne: bool, departing: bool) -> bool:
        self.samples.append((t, ias - gs, ias))
        while self.samples and t - self.samples[0][0] > SHEAR_WINDOW_S:
            self.samples.popleft()
        if not airborne or not (30 < agl <= SHEAR_AGL) or len(self.samples) < 3:
            return False
        head, airspeed = ias - gs, ias
        for _, h0, a0 in self.samples:
            if abs(head - h0) >= SHEAR_KT and (airspeed - a0 <= -SHEAR_KT or vs < -1200 or (departing and vs < 0)):
                return True
        return False


class WatchMixin:
    """The watches above. ``self`` is the Monitor."""

    def _init_watches(self: Any, alternate: str = "", repeat_atc: bool = False) -> None:
        self.alternate = alternate.upper()
        self.repeat_atc = repeat_atc
        self.turbulence = Turbulence()
        self.shear = WindShear()
        self.requests: list[Any] = []  # sim commands for the PM to send: the arrival's restrictions, airport layouts
        self.arrival_asked = False
        self.restrictions: list[Restriction] = []
        self.step_weight: float | None = None  # the weight at the last cruise level (the fallback's reference)
        self.step_level = 0
        self.step_asked: dict[int, float] = {}  # level -> when it was suggested
        self.unable_t = -1e9
        self.taxi_joined: tuple[Any, ...] | None = None  # the route (as given) the aircraft got onto
        self.taxi_off_since: float | None = None
        self.hdg_hist: deque[tuple[float, float]] = deque()
        self.divert_due: tuple[float, str] | None = None  # (offer by then, why)
        self.divert_offered: list[Any] = []
        self.weather_said: dict[str, tuple[str, ...]] = {}
        self.autobrake_seen: bool = False

    # --- events ------------------------------------------------------------------------------------------------------

    def _watch_atc(self: Any, ev: AtcTransmission) -> None:
        iid = ev.instruction_id or ""
        if "unable" in iid:
            self.unable_t = ev.t
        st = self.engine.state if self.engine is not None else None
        if self.repeat_atc and self.verbosity == "chatty" and st is not None and st.pending is not None:
            key = _instruction(ev.text)
            if key:
                self.queue = [q for q in self.queue if not q.before_readback]
                self._call(f"repeat:{ev.t:.0f}", CHATTER, ev.t, key, before_readback=True)

    def _watch_arrival(self: Any, data: ArrivalData) -> None:
        """The STAR's restrictions: its common part, and the transitions on the plan's route or to the runway."""
        plan = getattr(self.engine, "route", None)
        route = {f.ident.upper(): f for f in getattr(plan, "fixes", ())}
        st = self.engine.state if self.engine is not None else None
        runway = (st.assignments.arrival_runway or "") if st is not None else ""
        out: list[Restriction] = []
        seen: set[str] = set()
        for leg in data.legs:
            if not (leg.altitude or leg.speed_kt) or leg.fix in seen:
                continue
            if leg.transition and not (leg.fix.upper() in route or (runway and leg.transition.upper() == f"RW{runway}".upper())):
                continue
            fix = route.get(leg.fix.upper())
            lat = leg.lat if leg.lat is not None else (fix.lat if fix is not None else None)
            lon = leg.lon if leg.lon is not None else (fix.lon if fix is not None else None)
            if lat is None or lon is None:
                continue
            if leg.altitude and not 500 <= leg.alt1_ft <= 45000:
                continue  # not a believable restriction: better none than a wrong one
            seen.add(leg.fix)
            out.append(Restriction(leg.fix, lat, lon, leg.altitude, leg.alt1_ft, leg.alt2_ft, leg.speed_kt))
        self.restrictions = out
        if not out and data.name:
            self._call(f"star_none:{data.name}", CHATTER, data.t,
                       f"The {data.name} arrival has no restrictions in the sim's navdata, so I can't check them.")

    def _watch_emergency(self: Any, t: float, why: str) -> None:
        """An emergency, an engine failure, fuel below the reserve: where to go, once the airports around are known."""
        if self.divert_due is not None or self._said(f"divert:{why}") or self.engine is None:
            return
        self.f.said[f"divert:{why}"] = t
        self.divert_due = (t + DIVERT_WAIT_S, why)
        own = self.c.own
        known = getattr(getattr(getattr(self.engine, "tracker", None), "context_builder", None), "airports", {})
        nearby = getattr(getattr(self.engine, "diversion", None), "nearby", ())
        if own is None:
            return
        for a in sorted(nearby, key=lambda a: haversine_nm(own.lat, own.lon, a.lat, a.lon))[:8]:
            if a.icao and a.icao not in known and haversine_nm(own.lat, own.lon, a.lat, a.lon) <= DIVERT_NM:
                self.requests.append(RequestAirportData(icao=a.icao))

    # --- each position -----------------------------------------------------------------------------------------------

    def _watch(self: Any, own: OwnshipState, prev: OwnshipState, t: float) -> None:
        s = self.c.systems
        airborne = not own.on_ground
        agl = self._agl(own)
        departing = self.f.takeoff_t is not None and t - self.f.takeoff_t < 180
        if airborne and agl > 1500 and s is not None:
            self._watch_turbulence(t, s.g_force)
        elif own.on_ground:
            self.turbulence.reset()
        if self.shear.add(t, own.ias_kt, own.gs_kt, own.vs_fpm, agl, airborne, departing):
            self._call("windshear", SAFETY, t, "Wind shear, wind shear!", again_s=60)
        if airborne and s is not None and s.ice_pct >= ICE_PCT:
            self._call("ice", ROUTINE, t, "Picking up ice." + ("" if s.anti_ice else " Anti-ice on?"), again_s=900)
        self._watch_steps(own, t)
        self._watch_restrictions(own, t)
        self._watch_sight(own, t, agl)
        self._watch_brakes(own, prev, t, agl)
        self._watch_taxi(own, t)
        self._watch_diversion(own, t)
        self._watch_weather(t)

    def _watch_turbulence(self: Any, t: float, g: float) -> None:
        level = self.turbulence.add(t, g)
        if level in ("moderate", "severe"):
            if level == "severe":
                self._call(f"turb:severe:{t:.0f}", SAFETY, t, "Severe turbulence!")
            else:
                self._call(f"turb:moderate:{t:.0f}", ROUTINE, t, ["Moderate turbulence.", "Getting bumpy, moderate turbulence."])
            self._call(f"turb:belts:{t:.0f}", CHATTER, t + 1, "Seatbelt sign on for the cabin?")
            self.f.said["turb_called"] = t
        elif level in ("smooth", "light") and "turb_called" in self.f.said:
            self.f.said.pop("turb_called")
            self._call(f"turb:smooth:{t:.0f}", ROUTINE, t, ["Smooth again.", "That's smoothed out."])

    # --- step climbs -----------------------------------------------------------------------------------------------

    def _steps(self: Any) -> list[tuple[str, float, float, int]]:
        route = getattr(self.engine, "route", None)
        fixes = list(getattr(route, "fixes", ())) if route else []
        out, level = [], None
        for f in fixes:
            if f.stage != "CRZ" or f.alt_ft <= 0:
                continue
            if level is not None and f.alt_ft >= level + 900:
                out.append((f.ident, f.lat, f.lon, int(round(f.alt_ft, -3))))
            level = f.alt_ft if level is None else max(level, f.alt_ft)
        return out

    def _watch_steps(self: Any, own: OwnshipState, t: float) -> None:
        st = self.engine.state if self.engine is not None else None
        if st is None or self.phase != "CRUISE" or own.on_ground:
            if self.phase != "CRUISE":
                self.step_weight = None
            return
        level = int(round(self._altitude(own), -3))
        if abs(own.vs_fpm) > 300:
            return  # changing level: not now
        if t - self.unable_t < UNABLE_QUIET_S:
            return
        assigned = st.assignments.altitude_ft or level
        steps = self._steps()
        want: tuple[int, str] | None = None
        if steps:
            for ident, lat, lon, alt in steps:
                if alt <= max(level, assigned) + 500:
                    continue  # already there (or cleared there)
                if haversine_nm(own.lat, own.lon, lat, lon) <= STEP_AHEAD_NM:
                    want = (alt, "Step climb point ahead")
                break
        elif own.gross_weight_lb and (self.c.systems is None or self.c.systems.jet or level >= 25000):
            if self.step_weight is None or level > self.step_level:
                self.step_weight, self.step_level = own.gross_weight_lb, level
            burned = 1 - own.gross_weight_lb / self.step_weight
            up = level + 2000
            if burned >= STEP_BURN and up <= self.c.profile.ceiling_ft and assigned < up:
                want = (up, "We're light enough to go higher")
        if want is None:
            return
        alt, why = want
        if alt in self.step_asked:
            return
        self.step_asked[alt] = t
        if not steps:
            self.step_weight = own.gross_weight_lb  # the next suggestion after another 5 percent
        said = _alt(alt)
        if self.hands and self.radio_mode() == "full":
            self._call(f"step:{alt}", ROUTINE, t, f"{why}, asking for {said}.", radio=self._radio_words(f"request climb {said}"))
        elif self._call(f"step:{alt}", ROUTINE, t, f"{why}, suggest {said}. Want me to ask?"):
            self.offer = self._offer("step", str(alt), t + 45.0)

    # --- the arrival's restrictions --------------------------------------------------------------------------------

    def _watch_restrictions(self: Any, own: OwnshipState, t: float) -> None:
        st = self.engine.state if self.engine is not None else None
        if st is None or own.on_ground:
            return
        star, dest = getattr(self.engine.cfg, "star", None), st.flight.destination
        if star and dest and not self.arrival_asked and self.phase in ("CRUISE", "ARRIVAL"):
            geo = self.engine.geometry(dest)
            if geo is not None and haversine_nm(own.lat, own.lon, geo.airport.lat, geo.airport.lon) <= ARRIVAL_ASK_NM:
                self.arrival_asked = True
                self.requests.append(RequestArrival(icao=dest, name=star))
        if not self.restrictions:
            return
        via = getattr(self.engine, "_via_floor", None) is not None and st.assignments.heading is None
        alt = self._altitude(own)
        upcoming = None
        for r in self.restrictions:
            if r.passed:
                continue
            d = haversine_nm(own.lat, own.lon, r.lat, r.lon)
            if d < r.closest_nm:
                r.closest_nm, r.alt_there, r.ias_there = d, alt, own.ias_kt
            elif r.closest_nm <= RESTRICTION_NM and d > r.closest_nm + 0.5:
                r.passed = True
                if via and not r.met(r.alt_there) and r.altitude:
                    self._call(f"restriction:{r.fix}", ROUTINE, t, f"Missed {r.words()} at {r.fix}.")
                elif via and r.speed_kt and r.ias_there > r.speed_kt + 10:
                    self._call(f"restriction_speed:{r.fix}", ROUTINE, t, f"Missed the {r.speed_kt} knots at {r.fix}.")
                continue
            if upcoming is None:
                upcoming = (r, d)
        if via and upcoming is not None:
            r, d = upcoming
            top = r.ceiling()
            if top is not None and d > RESTRICTION_NM and alt > top + RESTRICTION_FT:
                minutes = d / max(own.gs_kt, 120.0) * 60
                projected = alt + min(own.vs_fpm, 0.0) * minutes
                if (alt - top) / FT_PER_NM_3DEG > d - 1 and projected > top + 500:
                    self._call(f"restriction_high:{r.fix}", ROUTINE, t, f"We're high for {r.words()} at {r.fix}.")

    # --- field in sight, approach lights ---------------------------------------------------------------------------

    def _destination_weather(self: Any) -> Any:
        st = self.engine.state if self.engine is not None else None
        info = self._atis(st.flight.destination) if st is not None else None
        return info.weather if info is not None else None

    def _watch_sight(self: Any, own: OwnshipState, t: float, agl: float) -> None:
        st = self.engine.state if self.engine is not None else None
        if st is None or own.on_ground or self.phase not in ("ARRIVAL", "APPROACH") or self._cleared("approach"):
            return
        approach = (st.assignments.approach or "").upper()
        info = self._atis(st.flight.destination)
        visual = "VISUAL" in approach or (not approach and info is not None and (info.approach or "").upper() == "VISUAL")
        geo = self.engine.geometry(st.flight.destination)
        if not visual or geo is None:
            return
        if haversine_nm(own.lat, own.lon, geo.airport.lat, geo.airport.lon) > SIGHT_NM or own.in_cloud:
            return
        if own.visibility_m is not None and own.visibility_m < 4800:
            return
        w = info.weather if info is not None else None
        if w is not None and w.ceiling_ft is not None and agl >= w.ceiling_ft:
            return
        if self._call("field_sight", ROUTINE, t, ["Field in sight?", "Do you have the field?"]):
            self.offer = self._offer("sight", st.flight.destination or "", t + 30.0)

    def _lights_plausible(self: Any, dh: int) -> bool:
        """At minimums: could the approach lights be in view? Out of cloud, with the visibility and the ceiling for it."""
        own = self.c.own
        if own is None or own.in_cloud or (own.visibility_m is not None and own.visibility_m < 800):
            return False
        w = self._destination_weather()
        if w is not None and ((w.ceiling_ft is not None and w.ceiling_ft < dh) or (w.visibility_sm is not None and w.visibility_sm < 0.5)):
            return False
        return True

    # --- the autobrake and the reversers -------------------------------------------------------------------------

    def _watch_brakes(self: Any, own: OwnshipState, prev: OwnshipState, t: float, agl: float) -> None:
        s, p = self.c.systems, self.c.profile
        st = self.engine.state if self.engine is not None else None
        if s is None:
            return
        name = p.autobrake_name(s.autobrake)
        n = self.f.approaches
        if not own.on_ground and self.phase in ("APPROACH", "LANDING") and self._agl(prev) > 2500 >= agl and name:
            length = self._landing_length_ft()
            if name == "off":
                if length and length < (p.short_runway_ft or SHORT_RUNWAY_FT):
                    self._call(f"autobrake:{n}", ROUTINE, t, f"Autobrake's off, and the runway's only {length:,} feet.")
            else:
                self._call(f"autobrake:{n}", ROUTINE, t, f"Autobrake set to {name}.")
        if self.f.touchdown_t is None or not own.on_ground or self.phase == "TAXI_IN":
            return
        since = t - self.f.touchdown_t
        reversers = p.reversers if p.reversers is not None else s.jet
        if reversers and 3.0 <= since <= 12.0 and own.gs_kt > 60:
            if s.reverser_pct >= 50:
                self._call(f"reversers:{n}", ROUTINE, t, "Reversers.")
            elif since >= 6.0:
                self._call(f"reversers:{n}", ROUTINE, t, "No reversers.")
        if name and name != "off" and own.gs_kt > 30:
            if s.autobrake_active and since >= 2.0:
                self.autobrake_seen = True
                self._call(f"autobrake_on:{n}", ROUTINE, t, "Autobrake engaged.")
            elif self.autobrake_seen and not s.autobrake_active:
                self._call(f"autobrake_off:{n}", ROUTINE, t, "Autobrake off.")
        if own.gs_kt < 20:
            self.autobrake_seen = False

    def _landing_length_ft(self: Any) -> int:
        st = self.engine.state if self.engine is not None else None
        geo = self.engine.geometry(st.flight.destination) if st is not None else None
        runway = st.assignments.arrival_runway if st is not None else None
        end = geo.end(runway) if geo is not None and runway else None
        return round(end.runway.runway.length_m * 3.28084) if end is not None else 0

    # --- the taxi route --------------------------------------------------------------------------------------------

    def _watch_taxi(self: Any, own: OwnshipState, t: float) -> None:
        self.hdg_hist.append((t, own.hdg_true))
        while self.hdg_hist and t - self.hdg_hist[0][0] > 3.0:
            self.hdg_hist.popleft()
        path = getattr(self.engine, "_taxi_path", None)
        st = self.engine.state if self.engine is not None else None
        if path is None or st is None or not own.on_ground or self.phase not in ("TAXI_OUT", "TAXI_IN") or own.on_runway:
            self.taxi_off_since = None
            return
        here = st.flight.destination if self.phase == "TAXI_IN" else st.flight.origin
        geo = self.engine.geometry(path[0]) if path[0] == here else None  # not the route at the airport left behind
        points = path[1]
        if geo is None or len(points) < 2:
            return
        if self.taxi_joined is not None and self.taxi_joined is not points:
            self.taxi_joined = None  # a new route
        xy = geo.xy(own.lat, own.lon)
        d, seg = _to_polyline(xy, points)
        if d <= TAXI_JOINED_M:
            self.taxi_joined = points
        if self.taxi_joined is not points or own.gs_kt < 4:
            self.taxi_off_since = None
            return
        cleared = [w for w in (st.assignments.taxi_route or ()) if w]
        via = f", cleared via {' '.join(cleared)}" if cleared else ""
        if d > TAXI_OFF_M:
            self.taxi_off_since = self.taxi_off_since if self.taxi_off_since is not None else t
            if t - self.taxi_off_since >= TAXI_OFF_S:
                on = _taxiway_at(geo, xy)
                where = f"We're on {on}{via}." if on and on not in cleared else f"We're off the taxi route{via}."
                self._nag(f"wrong_taxiway:{id(points)}", ROUTINE, t, where, every_s=90)
            return
        self.taxi_off_since = None
        # Turning off it: on the route now, turning, and a few seconds on that turn ahead is well off it.
        if len(self.hdg_hist) >= 2 and d < 15:
            dt = self.hdg_hist[-1][0] - self.hdg_hist[0][0]
            turn = ((self.hdg_hist[-1][1] - self.hdg_hist[0][1] + 180) % 360) - 180
            if dt > 0.5 and abs(turn / dt) >= 4.0:
                ahead_m = max(own.gs_kt * 0.514 * 6, 40.0)
                h = math.radians(own.hdg_true + turn / dt * 2)
                ahead = (xy[0] + ahead_m * math.sin(h), xy[1] + ahead_m * math.cos(h))
                d_ahead, _ = _to_polyline(ahead, points)
                course = _segment_heading(points, seg)
                if d_ahead > 50 and course is not None and abs(((own.hdg_true - course) + 180) % 360 - 180) > 30:
                    self._nag(f"wrong_taxiway:{id(points)}", ROUTINE, t, f"Not this turn{via or ', it is off our route'}.",
                              every_s=90)

    # --- a diversion ---------------------------------------------------------------------------------------------

    def _watch_diversion(self: Any, own: OwnshipState, t: float) -> None:
        if self.divert_due is None or own.on_ground:
            return
        due, why = self.divert_due
        options_of = getattr(self.engine, "_diversion_options", None)
        options = options_of(own) if options_of is not None else []
        if not options and t < due:
            return
        self.divert_due = None
        if not options:
            self._call(f"divert_none:{why}", ROUTINE, t, f"No suitable airport within {DIVERT_NM:.0f} miles that I know of.")
            return
        dest = self.engine.state.flight.destination
        top = [o for o in options if o.icao != dest][:2] or options[:1]
        best = top[0]
        words = f"Nearest suitable is {self._describe_divert(best)}"
        if len(top) > 1:
            words += f". Then {top[1].name}, {round(top[1].distance_nm)} miles"
        self.divert_offered = top
        if self._call(f"divert:{why}:{best.icao}", ROUTINE, t, words + ". Want me to ask for it?"):
            self.offer = self._offer("divert", best.icao, t + 60.0)

    def _describe_divert(self: Any, d: Any) -> str:
        parts = [f"{d.name}, {round(d.distance_nm)} miles {d.bearing}", f"runway {d.runway}, {d.runway_ft:,} feet"]
        if d.ils:
            parts.append("ILS")
        w = getattr(getattr(self.engine, "weather", None), "samples", {}).get(d.icao)
        if w is not None:  # only an airport's own observation; one borrowed from somewhere else isn't its weather
            bad = []
            if w.visibility_sm is not None and w.visibility_sm < 3:
                bad.append(f"visibility {w.visibility_sm:g} miles")
            if w.ceiling_ft is not None and w.ceiling_ft < 1000:
                bad.append(f"ceiling {w.ceiling_ft:,} feet")
            parts.append(", ".join(bad) if bad else "weather good")
        return ", ".join(parts)

    def radio_request(self: Any, kind: str, value: str) -> str:
        """The copilot's words on the radio for an offer the pilot said yes to ("" when there's nothing to ask)."""
        if kind == "step":
            return self._radio_words(f"request climb {_alt(int(value))}")
        if kind == "divert":
            named = next((o.name for o in self.divert_offered if o.icao == value), value)
            return self._radio_words(f"request diversion to {named}")
        if kind == "sight":
            return self._radio_words("field in sight")
        return ""

    def _radio_words(self: Any, request: str) -> str:
        st = self.engine.state if self.engine is not None else None
        if st is None:
            return ""
        tuned = st.comms.tuned
        cs = st.flight.callsign
        callsign = speech.callsign_display(cs) if hasattr(cs, "is_airline") else str(cs or "")
        return ", ".join(x for x in (tuned.station if tuned is not None else "", callsign, request) if x)

    # --- the weather ahead -------------------------------------------------------------------------------------------

    def _watch_weather(self: Any, t: float) -> None:
        """The destination's and the alternate's ATIS: what's worth knowing in it, when a new one comes out."""
        st = self.engine.state if self.engine is not None else None
        if st is None or self.phase not in ("CRUISE", "ARRIVAL", "DEPARTURE"):
            return
        for icao, role in ((st.flight.destination, "destination"), (self.alternate, "alternate")):
            info = self._atis(icao) if icao else None
            if info is None:
                continue
            hazards = _hazards(info.weather)
            key = (info.letter, *hazards)
            if self.weather_said.get(icao) == key:
                continue
            before = self.weather_said.get(icao)
            self.weather_said[icao] = key
            if not hazards or (before is not None and before[1:] == key[1:]):
                continue
            with regions.speaking(regions.region_for(icao)):
                name = self._name(icao)
            lead = f"{name}'s weather" if role == "destination" else f"Our alternate {name}"
            self._call(f"wx:{icao}:{info.letter}", ROUTINE, t, f"{lead}: {', '.join(hazards)}.")

    def _offer(self: Any, kind: str, value: str, until: float) -> Any:
        from localtc.crew.monitor import Offer

        return Offer(kind, value, until)


def _hazards(w: Any) -> tuple[str, ...]:
    """What in a weather report a crew plans around: gusts, low visibility, a low ceiling, freezing precipitation."""
    out = []
    if w.gust_kt and w.gust_kt >= 20:
        out.append(f"gusting {w.gust_kt}")
    if w.visibility_sm is not None and w.visibility_sm < 3:
        out.append(f"visibility {w.visibility_sm:g} miles" if w.visibility_sm >= 1 else "low visibility")
    if w.ceiling_ft is not None and w.ceiling_ft < 1000:
        out.append(f"ceiling {w.ceiling_ft:,} feet")
    if w.precip and w.temperature_c is not None and w.temperature_c <= 2:
        out.append(f"{w.precip} near freezing, icing likely")
    elif w.precip_rate_mm is not None and w.precip_rate_mm >= 7.6:
        out.append(f"heavy {w.precip or 'rain'}")
    return tuple(out)


def _instruction(text: str) -> str:
    """The key part of what ATC said: after the callsign, the first two parts of the first sentence."""
    body = text.split(", ", 1)[1].strip() if ", " in text else text
    body = body.split(". ")[0].rstrip(".")
    parts = [p.strip() for p in body.split(", ") if p.strip()]
    keep = [p for p in parts[:2] if not p.lower().startswith(("good", "so long", "see ya", "thanks", "radar contact"))]
    out = ", ".join(keep)
    return (out[0].upper() + out[1:] + ".") if out else ""


def _to_polyline(xy: tuple[float, float], points: list[tuple[float, float]]) -> tuple[float, int]:
    """Metres from ``xy`` to the route, and which segment is nearest."""
    best, seg = 1e12, 0
    for i in range(len(points) - 1):
        (ax, ay), (bx, by) = points[i], points[i + 1]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        u = 0.0 if length2 == 0 else max(0.0, min(1.0, ((xy[0] - ax) * dx + (xy[1] - ay) * dy) / length2))
        d = math.hypot(xy[0] - ax - u * dx, xy[1] - ay - u * dy)
        if d < best:
            best, seg = d, i
    return best, seg


def _segment_heading(points: list[tuple[float, float]], seg: int) -> float | None:
    (ax, ay), (bx, by) = points[seg], points[seg + 1]
    if math.hypot(bx - ax, by - ay) < 1:
        return None
    return math.degrees(math.atan2(bx - ax, by - ay)) % 360


def _taxiway_at(geo: Any, xy: tuple[float, float]) -> str:
    """The named taxiway the aircraft is on (within 25 m of its centreline), or ""."""
    airport = geo.airport
    nodes = getattr(geo, "_taxi_nodes", None)
    if nodes is None:
        nodes = {p.index: geo.xy(p.lat, p.lon) for p in airport.taxi_points}
        try:
            geo._taxi_nodes = nodes
        except AttributeError:
            pass
    best, name = 25.0, ""
    for path in airport.taxi_paths:
        if path.kind not in ("taxi", "path") or not path.name or path.start not in nodes or path.end not in nodes:
            continue
        d, _ = _to_polyline(xy, [nodes[path.start], nodes[path.end]])
        if d < best:
            best, name = d, path.name
    return name
