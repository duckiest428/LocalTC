"""The copilot speaks first: callouts, reminders, relays and checks from the sim's state and from what ATC says.

Every call has a trigger (a threshold in the sim, an ATC instruction, a timer or a checklist), a key it's said once
under (or again only after its cooldown), and a priority:

- **safety** (``SAFETY``): said at once, over anything: config on the roll, a runway ahead without a clearance, gear not
  down at 1,000 feet, not cleared to land, stall and overspeed, an engine failure.
- **routine** (``ROUTINE``): the standard callouts, the relays and the reminders; queued, and said when the frequency is
  quiet (never over ATC or the pilot), a few seconds apart.
- **chatter** (``CHATTER``): the status updates and the small talk; dropped when there's anything else to say, and below
  10,000 feet (the sterile cockpit).

``[crew] verbosity`` picks how much is said: "quiet" (safety only), "standard" (callouts, relays and reminders) or
"chatty" (everything). The speeds come from the aircraft: SimBrief's V-speeds when the plan has them, else the sim's
design speeds; the flap and gear limits from the aircraft's profile.

The copilot works its own side of the cockpit (``[crew] hands = "pm"``): the radios in standby on a handoff, the
transponder code, the altimeter at the transition, the exterior lights, the gear on positive rate and the flaps on
schedule after takeoff, the selected altitude and heading as cleared, the after-landing flow. The captain's side
(parking brake, engines, thrust, the autopilot, the flaps for takeoff and landing) it never touches: it notices when
something there is missed and says so. With ``hands = "calls"`` it touches nothing at all and only says.

Pure, like the rest of the copilot: ``observe`` every event, ``due(t)`` for what to say now.
"""

import random
import re
from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core import region as regions
from localtc.atc_core.airport.approaches import MINIMA
from localtc.atc_core.facilities import channel_khz
from localtc.atc_core.phraseology import speech
from localtc.config import PlanPerf
from localtc.crew import checklists
from localtc.crew.actions import Cockpit
from localtc.crew.answers import LB_PER_KG, Picture, _hm
from localtc.crew.commands import Command
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AtcAlert,
    AtcTransmission,
    FlightArrived,
    IntercomPressed,
    IntercomReleased,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    ReadbackEvaluated,
    TrafficSnapshot,
    Transcript,
)
from localtc.sim_api.geo import bearing_deg, haversine_nm

SAFETY, ROUTINE, CHATTER = 3, 2, 1
VERBOSITY = {"quiet": SAFETY, "standard": ROUTINE, "chatty": CHATTER}
GAP_S = 3.0  # between two of the copilot's own calls
EXPIRES_S = {SAFETY: 8.0, ROUTINE: 25.0, CHATTER: 15.0}  # not said by then: too late to be worth it
SPEECH_S_PER_CHAR = 0.065  # how long ATC's words take; the frequency is busy meanwhile
AFTER_ATC_S = 1.5  # and a moment after, for the readback to start
READBACK_GRACE_S = 7.0  # an instruction that wants a readback: the pilot's turn first
STERILE_FT = 10000.0
STATUS_EVERY_S = 1800.0
GEAR_UP_WAIT_S = 3.0  # positive rate: the pilot's "gear up" first, then the copilot's hand
FLAP_STEP_WAIT_S = 6.0
RETRACT_MARGIN_KT = 15.0  # a notch up this far below the current detent's placard speed
RETRACT_MIN_AGL = 1000.0
HANDOFF_LATE_S = 60.0
READBACK_LATE_S = 12.0
TAXI_RUNWAY_M = 60.0  # this close to a runway's edge, closing on it: hold short
TRAFFIC_NM, TRAFFIC_FT = 2.0, 1000.0
TOD_WARN_MIN = 10.0
LOW_FUEL_MIN = 45.0


@dataclass
class Call:
    key: str
    text: str
    priority: int
    t: float
    spoken: str = ""
    commands: tuple[Command, ...] = ()  # the copilot's own hands: done with the words
    urgent: bool = False  # said even over the frequency (the end of the flight, before it stops)

    @property
    def expires(self) -> float:
        return self.t + EXPIRES_S[self.priority]


@dataclass
class Offer:
    """A question the copilot asked ("Before takeoff checklist?"): a "yes" within ``until`` does it."""

    kind: str  # checklist, brief, squawk
    value: str
    until: float


@dataclass
class _Run:
    name: str
    index: int
    since: float


@dataclass
class _Flight:
    """What the copilot remembers of this flight."""

    said: dict[str, float] = field(default_factory=dict)
    briefed: set[str] = field(default_factory=set)
    checklists: set[str] = field(default_factory=set)
    takeoff_t: float | None = None
    takeoff_fuel_lb: float | None = None
    landing_t: float | None = None
    landing_fpm: float | None = None
    touchdown_t: float | None = None
    positive_rate_t: float | None = None
    readbacks: int = 0
    readbacks_right: int = 0
    approaches: int = 0
    max_engines: int = 0


class Monitor:
    def __init__(self, picture: Picture, *, verbosity: str = "standard", hands: str = "pm",
                 perf: PlanPerf | None = None, plan_source: str = "", radio_mode=lambda: "off", seed: int = 0) -> None:
        self.p = picture
        self.verbosity = verbosity if verbosity in VERBOSITY else "standard"
        self.hands = hands == "pm"
        self.perf = perf or PlanPerf()
        self.plan_source = plan_source
        self.radio_mode = radio_mode  # the radio copilot's mode: "off", "assist" or "full"
        self.rng = random.Random(seed)
        self.f = _Flight()
        self.queue: list[Call] = []
        self.offer: Offer | None = None
        self.run: _Run | None = None
        self.phase: str | None = None
        self.prev: OwnshipState | None = None
        self.prev_sys: AircraftSystems | None = None
        self.title = ""
        self.busy_until = 0.0
        self.ptt_down = False
        self.last_said = -1e9
        self.first_t: float | None = None
        self.last_atc_t = -1e9
        self.pilot_since_atc = True
        self.handoff: tuple[float, Any] | None = None  # (when, the facility ATC sent the flight to)
        self.cleared_alt: int | None = None
        self.cleared_heading: tuple[int, float] | None = None
        self.captured: int | None = None  # the cleared altitude once the aircraft got there
        self.off_since: float | None = None
        self.heading_off_since: float | None = None
        self.speed_high_since: float | None = None
        self.taxi_runway_d: dict[str, float] = {}
        self.commanded: dict[str, float] = {}  # what the pilot asked for, and when
        self.flap_step_t = -1e9
        self.atis_seen: dict[str, str] = {}
        self.traffic_nm: dict[int, float] = {}

    # --- in ----------------------------------------------------------------------------------------------------

    @property
    def c(self) -> Cockpit:
        return self.p.cockpit

    @property
    def engine(self) -> Any:
        return self.p.engine

    def pilot_said(self, cmd: Command, t: float) -> None:
        """A command the pilot gave on the intercom: what the copilot shouldn't then do or call unasked."""
        self.commanded[f"{cmd.action}:{cmd.value}"] = t
        self.commanded[cmd.action] = t

    def observe(self, ev: Any) -> None:
        t = ev.t
        if self.first_t is None:
            self.first_t = t
        if isinstance(ev, AtcTransmission):
            self.busy_until = max(self.busy_until, t + len(ev.spoken or ev.text) * SPEECH_S_PER_CHAR + AFTER_ATC_S)
            self.last_atc_t = t
            self.pilot_since_atc = False
            self._on_atc(ev)
        elif isinstance(ev, PttPressed | IntercomPressed):
            self.ptt_down = True
        elif isinstance(ev, PttReleased | IntercomReleased):
            self.ptt_down = False
            self.busy_until = max(self.busy_until, t + 2.0)
        elif isinstance(ev, Transcript) and ev.radio != 0 and ev.text:
            self.pilot_since_atc = True
            self.busy_until = max(self.busy_until, t + 3.0)  # ATC answers next
        elif isinstance(ev, ReadbackEvaluated):
            self.f.readbacks += 1
            self.f.readbacks_right += ev.status == "correct"
        elif isinstance(ev, PhaseChanged):
            self._on_phase(ev.previous, ev.phase, t)
        elif isinstance(ev, AircraftIdentity):
            self.title = ev.title or ev.atc_model
        elif isinstance(ev, AtcAlert) and ev.kind == "emergency":
            self._call("emergency", ROUTINE, t, "Copy the emergency. Want me to squawk 7700?")
            self.offer = Offer("squawk", "7700", t + 20.0)
        elif isinstance(ev, TrafficSnapshot):
            self._traffic(ev, t)
        elif isinstance(ev, AircraftSystems):
            self._systems(ev, t)
            self.prev_sys = ev
        elif isinstance(ev, OwnshipState):
            self._own(ev, t)
            self.prev = ev
        elif isinstance(ev, FlightArrived):
            self._parked(t, arrived=True)

    # --- out ---------------------------------------------------------------------------------------------------

    def due(self, t: float) -> list[Call]:
        """What to say now: a safety call at once, else the next routine one when the frequency is quiet."""
        level = VERBOSITY[self.verbosity]
        self.queue = [q for q in self.queue if q.expires >= t and q.priority >= level]
        if self.sterile:
            self.queue = [q for q in self.queue if q.priority > CHATTER]
        if self.offer is not None and t > self.offer.until:
            self.offer = None
        if not self.queue:
            return []
        self.queue.sort(key=lambda q: (-q.priority, q.t))
        top = self.queue[0]
        busy = self.ptt_down or t < self.busy_until or self._readback_due(t)
        if top.priority < SAFETY and not top.urgent and (busy or t - self.last_said < GAP_S):
            return []
        if top.priority == CHATTER and len(self.queue) > 1:
            self.queue.pop(0)  # something more useful is waiting
            return []
        self.queue.pop(0)
        self.last_said = t
        return [top]

    @property
    def sterile(self) -> bool:
        """Below 10,000 feet with the engines running: only what the flight needs."""
        own = self.c.own
        if own is None:
            return False
        running = own.engine_running or (self.c.systems is not None and self.c.systems.engines_running > 0)
        return own.alt_indicated_ft < STERILE_FT and (running or not own.on_ground)

    def take_offer(self, t: float) -> Offer | None:
        offer, self.offer = self.offer, None
        return offer if offer is not None and t <= offer.until else None

    # --- helpers -----------------------------------------------------------------------------------------------

    def _said(self, key: str, again_s: float | None = None, t: float = 0.0) -> bool:
        when = self.f.said.get(key)
        return when is not None and (again_s is None or t - when < again_s)

    def _call(self, key: str, priority: int, t: float, text: str | list[str], *, commands: tuple[Command, ...] = (),
              again_s: float | None = None, urgent: bool = False) -> bool:
        """Queue ``text`` (one of the wordings, picked) under ``key``, once (or again after ``again_s``)."""
        if self._said(key, again_s, t) or any(q.key == key for q in self.queue):
            return False
        if priority < VERBOSITY[self.verbosity] and not commands:
            self.f.said[key] = t  # not said at this verbosity, and not later either
            return False
        words = self.rng.choice(text) if isinstance(text, list) else text
        self.f.said[key] = t
        st = self.engine.state if self.engine is not None else None
        airports = (st.flight.origin or "", st.flight.destination or "") if st is not None else ()
        self.queue.append(Call(key, words, max(priority, VERBOSITY[self.verbosity]) if commands else priority, t,
                               spoken=spoken(words, airports), commands=commands if self.hands else (), urgent=urgent))
        return True

    def _readback_due(self, t: float) -> bool:
        """ATC just asked for a readback: the pilot's (or the radio copilot's) turn, not the intercom's."""
        st = self.engine.state if self.engine is not None else None
        return st is not None and st.pending is not None and not self.pilot_since_atc and t - self.last_atc_t < READBACK_GRACE_S

    def _region(self) -> regions.Region:
        st = self.engine.state if self.engine is not None else None
        if st is None:
            return regions.FAA
        departing = self.phase in (None, "PARKED", "PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF", "DEPARTURE")
        return regions.region_for((st.flight.origin if departing else st.flight.destination) or st.flight.origin)

    def _altitude(self, own: OwnshipState) -> float:
        """The altitude ATC reads: up in the flight levels, the pressure altitude, whatever the altimeter says."""
        fa = getattr(self.engine, "_flight_altitude", None)
        return fa(own).alt_indicated_ft if fa is not None else own.alt_indicated_ft

    def _agl(self, own: OwnshipState) -> float:
        s = self.c.systems
        if s is not None and 0 < s.radio_height_ft < 2500:
            return s.radio_height_ft
        return own.alt_agl_ft

    def _fuel(self, lb: float) -> str:
        if self.p.metric:
            kg = lb / LB_PER_KG
            return f"{kg / 1000:.1f} tonnes" if kg >= 10000 else f"{round(kg / 100) * 100:,.0f} kilos"
        return f"{round(lb / 100) * 100:,.0f} pounds"

    def _flaps(self, index: int) -> str:
        own = self.c.own
        name = self.c.profile.detent_name(index, self.c.flap_positions, on_ground=own is not None and own.on_ground)
        return name.replace("+F", " plus F")

    def _vref(self) -> float:
        if self.perf.vref:
            return float(self.perf.vref)
        s = self.c.systems
        return s.vs0_kt * 1.3 if s is not None and s.vs0_kt > 30 else 0.0

    def _vr(self) -> float:
        if self.perf.vr:
            return float(self.perf.vr)
        s = self.c.systems
        return s.takeoff_kt if s is not None and s.takeoff_kt > 30 else 0.0

    def _cleared(self, kind: str) -> bool:
        st = self.engine.state if self.engine is not None else None
        return st is not None and kind in st.clearances

    def _atis(self, icao: str | None):
        return self.engine.current_atis(icao) if self.engine is not None and icao else None

    def _name(self, icao: str | None) -> str:
        """An airport as a pilot says it: "San Diego", not "KSAN"."""
        named = getattr(self.engine, "_airport_name", None)
        return (named(icao) if named is not None and icao else icao) or ""

    def _on_approach(self) -> bool:
        """Cleared for the approach (or going around): the cleared altitude and heading no longer hold."""
        return self._cleared("approach") or self.phase in ("APPROACH", "LANDING")

    def _nag(self, key: str, priority: int, t: float, text: str, *, every_s: float, most: int = 2) -> None:
        """A reminder that may come again after ``every_s``, but only ``most`` times for the same thing."""
        count = int(self.f.said.get(f"{key}#", 0))
        if count >= most or self._said(key, every_s, t) or any(q.key == key for q in self.queue):
            return
        self.f.said.pop(key, None)
        if self._call(key, priority, t, text):
            self.f.said[f"{key}#"] = count + 1

    # --- ATC ---------------------------------------------------------------------------------------------------

    def _on_atc(self, ev: AtcTransmission) -> None:
        t, iid = ev.t, ev.instruction_id or ""
        st = self.engine.state if self.engine is not None else None
        if st is None:
            return
        with regions.speaking(self._region()):
            if ".handoff_" in iid or iid in ("common.contact", "tower.exit_contact_ground", "clearance.contact_ground"):
                expected = st.comms.expected
                if expected is not None and expected.mhz:
                    self.handoff = (t, expected)
                    freq = speech.frequency_display(expected.mhz)
                    key = f"handoff:{expected.station}:{channel_khz(expected.mhz)}"  # a repeated handoff: once
                    if self.radio_mode() == "off":
                        self._call(key, ROUTINE, t, [f"{expected.station}, {freq}, in standby.", f"{freq} for {expected.station} in standby."],
                                   commands=(Command("com_standby", f"{expected.mhz:.3f}"),), again_s=300)
                    else:
                        self._call(key, CHATTER, t, f"Over to {expected.station}.", again_s=300)
            if iid.startswith("clearance.ifr"):
                a = st.assignments
                parts, cmds = [], []
                if a.squawk and (self.c.own is None or self.c.own.squawk != a.squawk):
                    parts.append(f"squawk {a.squawk}")
                    cmds.append(Command("squawk", a.squawk))
                if a.altitude_ft and self.c.systems is not None and abs(self.c.systems.ap_altitude_sel - a.altitude_ft) > 50:
                    parts.append(f"initial {speech.altitude_display(a.altitude_ft)}")
                    cmds.append(Command("altitude", str(a.altitude_ft)))
                if parts:
                    self._call("clearance_set", ROUTINE, t, f"{', '.join(parts).capitalize()} set." if self.hands
                               else f"Don't forget {' and '.join(parts)}.", commands=tuple(cmds))
                self._prompt_checklist("before_start", t + 8.0)
            elif iid.startswith("ground.pushback"):
                self._lights(t, "push", beacon=True, said="Beacon on.")
            elif iid.startswith("ground.taxi_out") or iid == "ground.taxi_out_hold_short":
                self._lights(t, "taxi", taxi=True, said="Taxi light on.")
                own = self.c.own
                if own is not None and own.flaps_index == 0 and self.c.flap_positions:
                    plan = f", plan says {self.perf.takeoff_flaps.replace('+F', ' plus F')}" if self.perf.takeoff_flaps else ""
                    self._call("flaps_for_takeoff", ROUTINE, t + 4, f"Flaps for takeoff when you're ready{plan}.")
                if "departure" not in self.f.briefed:
                    self._call("brief_reminder", ROUTINE, t + 6, ["We haven't briefed the departure yet. Want it now?",
                                                                   "Departure briefing before we go?"])
                    self.offer = Offer("brief", "departure", t + 40.0)
            elif iid in ("tower.luaw", "tower.takeoff", "tower.takeoff_rnav", "tower.takeoff_sid"):
                self._lights(t, "lineup", strobe=True, landing=True, said="Strobes and landing lights on.")
                own = self.c.own
                if own is not None and own.xpdr_mode not in ("alt",):
                    self._call("tara", ROUTINE, t + 2, "Transponder to TA/RA.")
            elif iid.startswith("approach.cleared") or iid == "approach.intercept_cleared":
                self.f.said.setdefault("approach_cleared_t", t)

    def _lights(self, t: float, key: str, *, said: str, **want: bool) -> None:
        s = self.c.systems
        if s is None:
            return
        cmds = tuple(Command("light", "on" if on else "off", name) for name, on in want.items()
                     if getattr(s, f"light_{name}") != on)
        if cmds and self.hands:
            self._call(f"lights:{key}", ROUTINE, t, said, commands=cmds)
        elif cmds:
            names = " and ".join("strobes" if c.target == "strobe" else f"{c.target} light" for c in cmds)
            self._call(f"lights:{key}", ROUTINE, t, f"{names.capitalize()} on?")

    # --- phases ------------------------------------------------------------------------------------------------

    def _on_phase(self, previous: str | None, phase: str, t: float) -> None:
        self.phase = phase
        st = self.engine.state if self.engine is not None else None
        with regions.speaking(self._region()):
            if phase == "RUNWAY_HOLD":
                self._prompt_checklist("before_takeoff", t)
            elif phase == "CRUISE":
                level = (st.flight.cruise_ft if st is not None else None) or (round(self._altitude(self.c.own), -2) if self.c.own else None)
                if level:
                    self._call("toc", ROUTINE, t, [f"Level at {speech.altitude_display(int(level))}.",
                                                   f"Top of climb, level {speech.altitude_display(int(level))}."])
            elif phase == "APPROACH":
                self.f.approaches += 1
            elif phase == "TAXI_IN" and previous == "LANDING":
                self._after_landing(t)

    # --- the aircraft ------------------------------------------------------------------------------------------

    def _systems(self, s: AircraftSystems, t: float) -> None:
        was = self.prev_sys
        own = self.c.own
        if was is None or own is None:
            return
        self.f.max_engines = max(self.f.max_engines, s.engines_running)
        if s.engines_running < was.engines_running and not own.on_ground and "engine" not in self.commanded:
            self._call("engine_failure", SAFETY, t, "Engine failure!", again_s=60)
        if s.engines_running < was.engines_running and own.on_ground and own.gs_kt > 40:
            v1 = self.perf.v1
            below = v1 and own.ias_kt < v1
            self._call("engine_failure_roll", SAFETY, t, "Engine failure! Below V1." if below else "Engine failure!")
        if s.engines_running > was.engines_running and own.on_ground and self.phase in (None, "PARKED", "PUSHBACK"):
            self._prompt_checklist("after_start", t + 20.0)
        if was.ap_master and not s.ap_master and not own.on_ground and t - self.commanded.get("autopilot", -1e9) > 10:
            self._call("ap_off", ROUTINE, t, ["Autopilot's off.", "Autopilot disconnected."], again_s=30)
        if s.engines_running == 0 and was.engines_running > 0 and own.on_ground and self.f.landing_t is not None:
            self._parked(t)

    def _own(self, own: OwnshipState, t: float) -> None:  # noqa: C901 - one rule per block
        prev = self.prev
        self._preflight(own, t)
        if prev is None:
            return
        with regions.speaking(self._region()):
            self._always(own, prev, t)
            if own.on_ground:
                self._ground(own, prev, t)
            else:
                self._air(own, prev, t)
            self._radio(own, t)
            self._checklist_tick(t)
            self._status(own, t)

    def _preflight(self, own: OwnshipState, t: float) -> None:
        st = self.engine.state if self.engine is not None else None
        if not own.on_ground or self.f.takeoff_t is not None or st is None or self.first_t is None:
            return
        f = st.flight
        with regions.speaking(self._region()):
            if f.origin and t - self.first_t >= 2.0 and not self._said("greeting"):
                callsign = speech.callsign_display(f.callsign) if f.callsign else ""
                aircraft = self.c.profile.name if self.c.profile.name != "stock" else speech.aircraft_type(f.aircraft_type)[0] if f.aircraft_type else ""
                route = f"{self._name(f.origin)} to {self._name(f.destination)}" if f.destination else f"out of {self._name(f.origin)}"
                lines = [self.rng.choice(["Hi, I'm with you.", "Hey. All set on my side.", "Morning. I'm with you."]),
                         ", ".join(x for x in (callsign, route, f"in the {aircraft}" if aircraft else "") if x) + "."]
                if own.fuel_lb:
                    fuel = f"{self._fuel(own.fuel_lb)} on board"
                    if self.perf.block_fuel_lb:
                        diff = own.fuel_lb - self.perf.block_fuel_lb
                        if abs(diff) > max(0.02 * self.perf.block_fuel_lb, 500):
                            fuel += f", {self._fuel(abs(diff))} {'more' if diff > 0 else 'less'} than the plan's block"
                        else:
                            fuel += ", matches the plan"
                    lines.append(fuel.capitalize() + ".")
                if not self.plan_source:
                    lines.append("No SimBrief plan loaded, so I'm going by the sim.")
                elif self.perf.zfw_lb and own.gross_weight_lb and own.fuel_lb:
                    zfw = own.gross_weight_lb - own.fuel_lb
                    diff = zfw - self.perf.zfw_lb
                    if abs(diff) > 0.03 * self.perf.zfw_lb:
                        lines.append(f"Zero fuel weight's {self._fuel(abs(diff))} {'over' if diff > 0 else 'under'} the plan; check the payload.")
                self._call("greeting", ROUTINE, t, " ".join(lines))
            info = self._atis(f.origin)
            if info is not None and self.atis_seen.get(f.origin or "") != info.letter and self._said("greeting"):
                self.atis_seen[f.origin or ""] = info.letter
                w = info.weather
                words = [f"{self._name(f.origin)} has information {info.letter}: runway {info.runway}, wind {speech.wind_display(w.wind)}"]
                if w.altimeter_inhg:
                    words.append(f"{'QNH' if self._region().icao else 'altimeter'} {speech.altimeter_display(w.altimeter_inhg)}")
                text = ", ".join(words) + "."
                if self.engine.cfg.sid:
                    text += f" We're planned on the {self.engine.cfg.sid}."
                cmds: tuple[Command, ...] = ()
                if w.altimeter_inhg and abs(own.altimeter_inhg - w.altimeter_inhg) > 0.015:
                    cmds = (Command("altimeter", f"{w.altimeter_inhg:.2f}"),)
                    if self.hands:
                        text += " Altimeter set."
                self._call(f"atis:{f.origin}:{info.letter}", ROUTINE, t, text, commands=cmds)
            if self._said("greeting") and t - self.f.said["greeting"] > 25 and "departure" not in self.f.briefed \
                    and self._call("brief_prompt", ROUTINE, t, ["Departure briefing whenever you're ready; just say brief.",
                                                                "Ready to brief the departure when you are."]):
                self.offer = Offer("brief", "departure", t + 40.0)
            if f.rules == "IFR" and self._said("greeting") and t - self.f.said["greeting"] > 45 and not self._cleared("ifr") \
                    and self.radio_mode() != "full" and self.phase in (None, "PARKED"):
                who = next((x for x in getattr(self.engine, "facilities", ()) if x.controller == "clearance"
                            and getattr(x, "airport", None) == f.origin), None)
                where = f": {who.station} on {speech.frequency_display(who.mhz)}" if who is not None else ""
                self._call("clearance_prompt", ROUTINE, t, f"Call for the clearance when you're ready{where}.")
            if self._cleared("pushback") and own.parking_brake and own.gs_kt < 0.5 and not self._said("push_brake"):
                when = self.f.said.get("pushback_seen") or self.f.said.setdefault("pushback_seen", t)
                if t - when > 25:
                    self._call("push_brake", ROUTINE, t, "We're cleared to push; parking brake's still set.")

    def _always(self, own: OwnshipState, prev: OwnshipState, t: float) -> None:
        s, p = self.c.systems, self.c.profile
        airborne = not own.on_ground
        if airborne and self._agl(own) > 50:
            stall = s is not None and s.stall_warning
            if s is not None and not stall:
                ref = s.vs0_kt if own.gear_down and own.flaps_index >= p.landing_index(self.c.flap_positions) else s.vs1_kt
                stall = ref > 30 and own.ias_kt < ref * 1.05
            if stall:
                self._call("stall", SAFETY, t, "Speed, speed!", again_s=10)
        if (s is not None and s.overspeed_warning) or (p.vmo_kt and own.ias_kt > p.vmo_kt + 3):
            self._call("overspeed", SAFETY, t, "Overspeed!", again_s=15)
        if airborne and own.flaps_index > 0 and (vfe := p.vfe_for(own.flaps_index)) and own.ias_kt > vfe + 5:
            self._call("flap_speed", SAFETY, t, f"Flap speed! {own.ias_kt:.0f}, limit {vfe:.0f}.", again_s=15)
        if airborne and own.gear_down and p.gear_extended_kt and own.ias_kt > p.gear_extended_kt + 5:
            self._call("gear_speed", SAFETY, t, f"Gear speed! {own.ias_kt:.0f}, limit {p.gear_extended_kt:.0f}.", again_s=15)

    def _ground(self, own: OwnshipState, prev: OwnshipState, t: float) -> None:  # noqa: C901
        p = self.c.profile
        st = self.engine.state if self.engine is not None else None
        # Touchdown.
        if not prev.on_ground and own.gs_kt > 40:
            self.f.touchdown_t, self.f.landing_t, self.f.landing_fpm = t, t, prev.vs_fpm
        if self.f.touchdown_t is not None and t - self.f.touchdown_t >= 2.0 and self.c.systems is not None \
                and (p.spoilers or (self.prev_sys is not None and self.prev_sys.spoilers_armed)):
            out = self.c.systems.spoilers_pct > 40
            self._call(f"spoilers:{self.f.approaches}", ROUTINE if out else SAFETY, t, "Spoilers." if out else "No spoilers!")
        if self.f.touchdown_t is not None and prev.ias_kt > p.rollout_call_kt >= own.ias_kt and own.gs_kt > 20:
            self._call(f"rollout:{self.f.approaches}", ROUTINE, t, f"{speech.miles(p.rollout_call_kt).capitalize()} knots.")
        # Taxiing.
        if self.phase in ("TAXI_OUT", "TAXI_IN") and own.gs_kt > p.taxi_max_kt and not own.on_runway:
            self._call("taxi_speed", ROUTINE, t, [f"Watch the speed, {own.gs_kt:.0f} knots.", f"Bit quick, {own.gs_kt:.0f} knots."], again_s=60)
        if self.phase in ("TAXI_OUT", "TAXI_IN", "PUSHBACK") and own.gs_kt > 3:
            self._runway_ahead(own, t)
        # Lined up.
        if st is not None and own.on_runway and own.gs_kt < 40 and self.phase in ("RUNWAY_HOLD", "TAKEOFF", "TAXI_OUT"):
            geo = self.engine.geometry(st.flight.origin)
            rwy = geo.runway_at(own.lat, own.lon) if geo is not None else None
            end = rwy.end_for_heading(own.hdg_true, 15.0) if rwy is not None else None
            cleared = st.assignments.departure_runway
            if end is not None and cleared and end.ident != cleared and (self._cleared("takeoff") or self._cleared("line_up")):
                self._call(f"wrong_runway:{end.ident}", SAFETY, t, f"Wrong runway! We're on {end.ident}, cleared for {cleared}.")
            elif end is not None and end.ident == cleared and own.gs_kt < 15:
                self._call(f"lined_up:{end.ident}", ROUTINE, t, f"Runway {end.ident}, checked.")
        # The takeoff roll.
        if own.on_runway and prev.gs_kt < 30 <= own.gs_kt and self.f.touchdown_t is None and self.phase != "TAXI_IN":
            problems = []
            if own.flaps_index == 0 and self.c.flap_positions:
                problems.append("flaps")
            if own.parking_brake:
                problems.append("parking brake")
            if self.c.systems is not None and self.c.systems.spoilers_pct > 40:
                problems.append("spoilers")
            if problems:
                self._call("config", SAFETY, t, f"Config! {', '.join(problems).capitalize()}!")
        if own.on_runway and self.f.touchdown_t is None and self.phase in ("TAKEOFF", "RUNWAY_HOLD", "TAXI_OUT"):
            check = p.speed_check_kt
            if prev.ias_kt < check <= own.ias_kt:
                self._call("speed_check", ROUTINE, t, f"{speech.miles(check).capitalize()} knots.")
            if self.perf.v1 and prev.ias_kt < self.perf.v1 <= own.ias_kt:
                self._call("v1", SAFETY, t, "V1.")
            if (vr := self._vr()) and prev.ias_kt < vr <= own.ias_kt:
                self._call("rotate", SAFETY, t, "Rotate.")
        # Parked at the destination.
        if self.f.landing_t is not None and own.gs_kt < 0.5 and own.parking_brake and self.phase == "TAXI_IN":
            stopped = self.f.said.setdefault("stopped_t", t)
            if t - stopped > 8:
                self._parked(t)
        else:
            self.f.said.pop("stopped_t", None)

    def _runway_ahead(self, own: OwnshipState, t: float) -> None:
        """Closing on a runway without a clearance onto or across it: hold short."""
        st = self.engine.state
        icao = st.flight.destination if self.phase == "TAXI_IN" else st.flight.origin
        geo = self.engine.geometry(icao)
        if geo is None:
            return
        xy = geo.xy(own.lat, own.lon)
        for rwy in geo.runways:
            d = rwy.distance_to(xy)
            was = self.taxi_runway_d.get(rwy.name)
            self.taxi_runway_d[rwy.name] = d
            if was is None or not (0 < d < TAXI_RUNWAY_M) or d >= was - 0.5:
                continue
            across = abs(((own.hdg_true - rwy.ends[0].heading_true) + 90) % 180 - 90)
            if across < 35 or own.gs_kt < 8:
                continue  # along it (a parallel taxiway), or slowing to stop: not about to enter it
            hold = geo.nearest_hold_short(own.lat, own.lon)
            if hold is not None and (hold[0].runway is not rwy or hold[1] > 40):
                continue  # the airport's hold lines: none of this runway's just ahead
            if hold is None and d > 35:
                continue
            idents = set(rwy.runway.idents)
            ok = False
            if self.phase != "TAXI_IN" and st.assignments.departure_runway in idents and (self._cleared("takeoff") or self._cleared("line_up")):
                ok = True
            crossing = st.issued.get("ground.cross_runway")
            if crossing is not None and str(crossing.slots.get("runway", "")) in idents and t - crossing.t < 600:
                ok = True
            if not ok:
                self._call(f"hold_short:{rwy.name}", SAFETY, t, f"Hold short, runway {min(idents)} ahead!", again_s=90)

    def _air(self, own: OwnshipState, prev: OwnshipState, t: float) -> None:  # noqa: C901
        p, s = self.c.profile, self.c.systems
        st = self.engine.state if self.engine is not None else None
        agl = self._agl(own)
        alt = self._altitude(own)
        prev_alt = self._altitude(prev)
        if prev.on_ground:  # liftoff
            self.f.takeoff_t = self.f.takeoff_t or t
            self.f.takeoff_fuel_lb = self.f.takeoff_fuel_lb or own.fuel_lb
            self.f.touchdown_t = None
        departing = self.phase in ("TAKEOFF", "DEPARTURE") or (self.f.takeoff_t is not None and t - self.f.takeoff_t < 300)
        # Positive rate, gear, flaps.
        if departing and own.vs_fpm > 300 and agl > 30 and own.gear_down and self.f.positive_rate_t is None:
            self.f.positive_rate_t = t
            self._call("positive_rate", ROUTINE, t, "Positive rate.")
        if self.f.positive_rate_t is not None and own.gear_down and t - self.f.positive_rate_t >= GEAR_UP_WAIT_S \
                and t - self.commanded.get("gear", -1e9) > 10 and departing:
            if self.hands:
                self._call("gear_up", ROUTINE, t, "Gear up.", commands=(Command("gear", "up"),))
            elif agl > 400:
                self._call("gear_up", ROUTINE, t, "Gear's still down.")
        if departing and not own.gear_down and own.flaps_index > 0 and agl > RETRACT_MIN_AGL and own.vs_fpm > 0 \
                and t - self.flap_step_t > FLAP_STEP_WAIT_S and t - self.commanded.get("flaps", -1e9) > 15:
            vfe = p.vfe_for(own.flaps_index)
            if vfe and own.ias_kt >= vfe - RETRACT_MARGIN_KT:
                up = own.flaps_index - 1
                name = "up" if up == 0 else self._flaps(up)
                if self.hands:
                    self.flap_step_t = t
                    self._call(f"flaps_retract:{up}", ROUTINE, t, f"Speed checked, flaps {name}.",
                               commands=(Command("flaps", "up" if up == 0 else p.detent_name(up, self.c.flap_positions)),))
                else:
                    self._call(f"flaps_retract:{up}", ROUTINE, t, f"Speed checked, flaps {name}?")
            elif not vfe and agl > 3000 and own.ias_kt > 210:
                self._call("flaps_still_out", ROUTINE, t, "Flaps are still out.")
        if departing and not own.gear_down and own.flaps_index == 0 and agl > 1500 and "after_takeoff" not in self.f.checklists:
            self._run("after_takeoff", t)
        if departing and agl > 2000 and s is not None and not s.ap_master and self.f.takeoff_t is not None and t - self.f.takeoff_t > 60:
            self._call("ap_available", ROUTINE, t, "Autopilot's available whenever you want it.")
        # Climbing and descending: the cleared altitude.
        target = st.assignments.altitude_ft if st is not None else None
        if target != self.cleared_alt:
            self.cleared_alt, self.captured, self.off_since = target, None, None
            if target and self.hands and s is not None and abs(s.ap_altitude_sel - target) > 50 and self.f.takeoff_t is not None:
                self._call(f"set_alt:{target}", ROUTINE, t, f"{speech.altitude_display(target)} set.",
                           commands=(Command("altitude", str(target)),))
        if target and not self._on_approach():
            if (prev_alt < target - 1000 <= alt and own.vs_fpm > 500) or (prev_alt > target + 1000 >= alt and own.vs_fpm < -500):
                self._call(f"1000_to_go:{target}:{round(t, -2)}", ROUTINE, t, ["One thousand to go.", "A thousand to go."])
            if abs(alt - target) < 150:
                self.captured = target
            if self.captured == target and abs(alt - target) > 300:
                self.off_since = self.off_since or t
                if t - self.off_since > 5:
                    off = round(abs(alt - target), -2)
                    self._nag(f"altitude_dev:{target}", SAFETY, t, f"Altitude! {off:,.0f} {'high' if alt > target else 'low'}.",
                              every_s=45)
            else:
                self.off_since = None
        heading = st.assignments.heading if st is not None else None
        if heading is not None and (self.cleared_heading is None or self.cleared_heading[0] != heading):
            self.cleared_heading = (heading, t)
            self.heading_off_since = None
            if self.hands and s is not None and abs(((s.ap_heading_sel - heading) + 180) % 360 - 180) > 2:
                self._call(f"set_hdg:{heading}:{t:.0f}", ROUTINE, t, f"Heading {heading:03d} set.",
                           commands=(Command("heading", str(heading)),))
        elif heading is None:
            self.cleared_heading = None
        if self.cleared_heading is not None and t - self.cleared_heading[1] > 45 and not self._on_approach():
            off = abs(((own.hdg_mag - heading) + 180) % 360 - 180)
            if off > 20:
                self.heading_off_since = self.heading_off_since or t
                if t - self.heading_off_since > 15:
                    self._nag(f"heading_dev:{heading}:{self.cleared_heading[1]:.0f}", ROUTINE, t,
                              f"Heading, we're assigned {heading:03d}.", every_s=60)
            else:
                self.heading_off_since = None
        # Ten thousand, the transition, the speed limit.
        region = self._region()
        if prev_alt < STERILE_FT <= alt and own.vs_fpm > 0:
            self._call("10k_climb", ROUTINE, t, "Ten thousand." + (" Landing lights off." if self.hands else " Landing lights off?"),
                       commands=(Command("light", "off", "landing"),) if s is not None and s.light_landing else ())
        if prev_alt > STERILE_FT >= alt and own.vs_fpm < 0:
            on = s is not None and not s.light_landing
            self._call("10k_descent", ROUTINE, t, "Ten thousand." + (" Landing lights on." if self.hands and on else "")
                       + " Seatbelt sign on for the cabin?", commands=(Command("light", "on", "landing"),) if on else ())
        ta = region.transition_ft
        if prev_alt < ta <= alt and own.vs_fpm > 0:
            std = abs(own.altimeter_inhg - 29.92) < 0.015
            self._call("transition_climb", ROUTINE, t, "Transition, standard." if std or not self.hands else "Transition, standard set.",
                       commands=() if std else (Command("altimeter", "29.92"),))
        tl = ta + (1000 if region.icao else 0)
        if prev_alt > tl >= alt and own.vs_fpm < 0 and st is not None:
            info = self._atis(st.flight.destination)
            if info is not None and info.weather.altimeter_inhg:
                q = info.weather.altimeter_inhg
                word = "QNH" if region.icao else "altimeter"
                self._call("transition_descent", ROUTINE, t, f"Transition level, {word} {speech.altimeter_display(q)}"
                           + (" set." if self.hands else "."), commands=(Command("altimeter", f"{q:.2f}"),))
            else:
                self._call("transition_descent", ROUTINE, t, "Transition level, local altimeter.")
            self._run("descent", t + 3)
        limit = 250 if st is None or not st.assignments.speed_kt or st.assignments.speed_kt <= 250 else None
        if limit and alt < STERILE_FT - 300 and agl > 1500 and own.ias_kt > limit + 10:
            self.speed_high_since = self.speed_high_since or t
            if t - self.speed_high_since > 10:
                self._nag("speed_250", ROUTINE, t, "Speed, two fifty below ten thousand.", every_s=120)
        else:
            self.speed_high_since = None
        if st is not None and st.assignments.speed_kt and abs(own.ias_kt - st.assignments.speed_kt) > 15 and agl > 1500 \
                and self.phase != "LANDING":
            since = self.f.said.setdefault("speed_off_t", t)
            if t - since > 20:
                self._nag(f"speed_dev:{st.assignments.speed_kt}", ROUTINE, t, f"Speed, we're assigned {st.assignments.speed_kt}.",
                          every_s=90)
        else:
            self.f.said.pop("speed_off_t", None)
        if own.in_cloud and own.temperature_c is not None and -40 <= own.temperature_c <= 5:
            self._call("icing", ROUTINE, t, f"In cloud at {own.temperature_c:.0f} degrees; engine anti-ice?", again_s=1200)
        self._cruise(own, t)
        self._approach(own, prev, t, agl)

    def _cruise(self, own: OwnshipState, t: float) -> None:
        st = self.engine.state if self.engine is not None else None
        if st is None or self.phase not in ("CRUISE", "DEPARTURE", "ARRIVAL"):
            return
        geo = self.engine.geometry(st.flight.destination)
        if geo is None or own.gs_kt < 100:
            return
        to_go = haversine_nm(own.lat, own.lon, geo.airport.lat, geo.airport.lon)
        level = st.flight.cruise_ft or int(own.alt_indicated_ft)
        tod_nm = self.engine.route.descent_distance_nm(geo.airport.lat, geo.airport.lon, level, geo.airport.elev_ft)
        minutes = (to_go - tod_nm) / own.gs_kt * 60
        descending = st.assignments.altitude_ft is not None and st.assignments.altitude_ft < level - 500
        if self.phase == "CRUISE" and 0 < minutes <= TOD_WARN_MIN:
            if self._call("tod_soon", ROUTINE, t, "Top of descent in about ten minutes. Arrival briefing when you're ready."):
                self.offer = Offer("brief", "approach", t + 40.0)
        if self.phase == "CRUISE" and minutes <= 0 and to_go > 20:
            self._call("tod", ROUTINE, t, "That's top of descent." + ("" if descending else " No descent clearance yet."))
        # Fuel against the plan and the reserve.
        if own.fuel_lb and own.fuel_flow_pph and own.fuel_flow_pph > 50:
            engines = max(self.c.systems.engines_running if self.c.systems is not None else 1, 1)
            burn = own.fuel_flow_pph * engines
            at_landing = own.fuel_lb - burn * to_go / own.gs_kt
            if self.perf.reserve_fuel_lb and at_landing < self.perf.reserve_fuel_lb:
                self._call("fuel_reserve", SAFETY, t, f"Fuel: at this burn we'd land below final reserve, about {self._fuel(max(at_landing, 0))}.", again_s=1800)
            elif self.perf.landing_fuel_lb and at_landing < self.perf.landing_fuel_lb * 0.9:
                short = self.perf.landing_fuel_lb - at_landing
                self._call("fuel_plan", ROUTINE, t, f"We're tracking about {self._fuel(short)} under the planned landing fuel.", again_s=2700)
            if own.fuel_lb / burn * 60 < LOW_FUEL_MIN:
                self._call("fuel_low", SAFETY, t, f"Fuel's low: about {own.fuel_lb / burn * 60:.0f} minutes left.", again_s=900)
        info = self._atis(st.flight.destination)
        if info is not None and self.atis_seen.get(st.flight.destination or "") != info.letter and to_go < 250:
            self.atis_seen[st.flight.destination or ""] = info.letter
            w = info.weather
            with regions.speaking(regions.region_for(st.flight.destination)):
                q = f", {'QNH' if regions.CURRENT.get().icao else 'altimeter'} {speech.altimeter_display(w.altimeter_inhg)}" if w.altimeter_inhg else ""
                text = (f"{self._name(st.flight.destination)} has information {info.letter}: runway {info.runway}, "
                        f"wind {speech.wind_display(w.wind)}{q}, expect {info.approach or 'the'} approach.")
            self._call(f"atis:{st.flight.destination}:{info.letter}", ROUTINE, t, text.replace("expect the approach", "expect vectors"))

    def _approach(self, own: OwnshipState, prev: OwnshipState, t: float, agl: float) -> None:  # noqa: C901
        p, s = self.c.profile, self.c.systems
        st = self.engine.state if self.engine is not None else None
        if self.phase not in ("ARRIVAL", "APPROACH", "LANDING") or own.vs_fpm > 1500:
            return
        n = self.f.approaches
        prev_agl = self._agl(prev)
        approach = st.assignments.approach if st is not None else None
        ils = bool(approach and approach.upper().startswith("ILS"))
        if s is not None:
            if s.loc_received and abs(s.loc_deviation) < 120 and (self.prev_sys is None or abs(self.prev_sys.loc_deviation) >= 120 or not self.prev_sys.loc_received):
                self._call(f"loc_alive:{n}", ROUTINE, t, "Localizer alive.")
            if s.gs_received and abs(s.gs_deviation) < 110 and self._said(f"loc_alive:{n}"):
                self._call(f"gs_alive:{n}", ROUTINE, t, "Glideslope alive.")
            cleared_t = self.f.said.get("approach_cleared_t")
            if ils and cleared_t is not None and t - cleared_t > 30 and s.ap_master and not s.ap_approach:
                self._call(f"app_mode:{n}", ROUTINE, t, "We're cleared the ILS; approach mode?")
        if self.phase in ("APPROACH", "LANDING") and own.vs_fpm < 0:
            positions = self.c.flap_positions
            nxt = own.flaps_index + 1
            vfe = p.vfe_for(nxt) if nxt <= positions else 0
            if vfe and own.ias_kt < vfe - 5 and agl < 6000 and t - self.commanded.get("flaps", -1e9) > 20:
                self._call(f"flaps_ext:{n}:{nxt}", ROUTINE, t, f"Speed checked for flaps {self._flaps(nxt)}.")
            if own.gear_down is False and prev_agl > 2000 >= agl:
                self._call(f"gear_prompt:{n}", ROUTINE, t, "Two thousand. Gear down?")
            if own.gear_down and own.flaps_index >= p.landing_index(positions) and agl < 3000:
                self._run("landing", t, key=f"landing:{n}")
            if prev_agl > 1000 >= agl:
                problems = self._unstable(own)
                if not own.gear_down:
                    self._call(f"gear_1000:{n}", SAFETY, t, "Gear! We're not down!")
                if problems:
                    self._call(f"1000:{n}", SAFETY, t, f"One thousand, not stable: {', '.join(problems)}.")
                else:
                    self._call(f"1000:{n}", ROUTINE, t, ["One thousand, stable.", "Thousand, stable."])
            going_around = self.p.last_atc is not None and "go_around" in (self.p.last_atc.instruction_id or "") \
                and t - self.p.last_atc.t < 90
            if prev_agl > 500 >= agl and not going_around:
                if not self._cleared("landing"):
                    self._call(f"no_landing_clearance:{n}", SAFETY, t, "We're not cleared to land!")
                if self._unstable(own):
                    self._call(f"500:{n}", SAFETY, t, "Not stable. Go around.")
            dh = _minimums(approach)
            if dh and prev_agl > dh + 100 >= agl:
                self._call(f"100_above:{n}", ROUTINE, t, "Hundred above.")
            if dh and prev_agl > dh >= agl:
                self._call(f"minimums:{n}", ROUTINE, t, "Minimums.")

    def _unstable(self, own: OwnshipState) -> list[str]:
        p = self.c.profile
        out = []
        if not own.gear_down:
            out.append("gear")
        if self.c.flap_positions and own.flaps_index < p.landing_index(self.c.flap_positions):
            out.append("flaps")
        if own.vs_fpm < -1100:
            out.append(f"sinking {abs(round(own.vs_fpm, -2)):,.0f}")
        if (vref := self._vref()) and not (vref - 5 <= own.ias_kt <= vref + 25):
            out.append(f"speed {own.ias_kt:.0f}")
        return out

    def _after_landing(self, t: float) -> None:
        own, s = self.c.own, self.c.systems
        cmds = []
        if own is not None and own.flaps_index > 0:
            cmds.append(Command("flaps", "up"))
        if s is not None and s.spoilers_armed:
            cmds.append(Command("spoilers", "disarm"))
        if s is not None and s.light_strobe:
            cmds.append(Command("light", "off", "strobe"))
        if s is not None and s.light_landing:
            cmds.append(Command("light", "off", "landing"))
        done = [w for c, w in ((Command("flaps", "up"), "flaps up"), (Command("light", "off", "strobe"), "strobes off"),
                               (Command("light", "off", "landing"), "landing lights off")) if c in cmds]
        if self.hands and cmds:
            text = "Clear of the runway. " + (", ".join(done).capitalize() + "." if done else "After landing flow done.")
        else:
            text = "Clear of the runway." + (" After landing flow when you're ready." if cmds else "")
        self._call("after_landing", ROUTINE, t, text, commands=tuple(cmds))
        self.f.checklists.add("after_landing")

    def _parked(self, t: float, *, arrived: bool = False) -> None:
        own, s = self.c.own, self.c.systems
        if own is None or self.f.landing_t is None:
            return
        if not self._said("summary"):
            self._call("summary", ROUTINE, t, self._summary(own), urgent=arrived)
        if s is not None and s.engines_running == 0 and s.light_beacon:
            self._call("beacon_off", ROUTINE, t, "Beacon off.", commands=(Command("light", "off", "beacon"),))
        if s is not None and s.engines_running == 0 and not own.parking_brake:
            self._call("park_brake", ROUTINE, t, "Parking brake?")
        if s is not None and s.engines_running == 0:
            self._prompt_checklist("shutdown", t + 2)

    def _summary(self, own: OwnshipState) -> str:
        parts = []
        if self.f.takeoff_t is not None and self.f.landing_t is not None:
            parts.append(f"{_hm((self.f.landing_t - self.f.takeoff_t) / 60)} in the air")
        if self.f.takeoff_fuel_lb and own.fuel_lb:
            parts.append(f"{self._fuel(max(self.f.takeoff_fuel_lb - own.fuel_lb, 0))} burned")
        if self.f.landing_fpm is not None:
            parts.append(f"touchdown at {abs(round(self.f.landing_fpm)):.0f} feet a minute")
        if self.f.readbacks:
            parts.append(f"{round(100 * self.f.readbacks_right / self.f.readbacks)} percent of the readbacks right")
        soft = self.f.landing_fpm is not None and self.f.landing_fpm > -150
        opener = self.rng.choice(["Nice one." if soft else "We're in.", "That's the flight." if not soft else "Lovely landing."])
        return f"{opener} {', '.join(parts).capitalize()}." if parts else opener

    # --- the radio, from the right seat --------------------------------------------------------------------------

    def _radio(self, own: OwnshipState, t: float) -> None:
        st = self.engine.state if self.engine is not None else None
        if st is None:
            return
        if self.handoff is not None:
            when, facility = self.handoff
            tuned = st.comms.tuned
            if tuned is not None and channel_khz(tuned.mhz) == channel_khz(facility.mhz):
                self.handoff = None
            elif t - when > HANDOFF_LATE_S and self.radio_mode() == "off":
                self._call(f"late_handoff:{facility.station}:{when:.0f}", ROUTINE, t,
                           f"We should be with {facility.station} on {speech.frequency_display(facility.mhz)} by now.")
                self.handoff = None
        pending = st.pending
        if pending is not None and not self.pilot_since_atc and t - self.last_atc_t > READBACK_LATE_S \
                and self.radio_mode() == "off" and self.p.last_atc is not None and not pending.attempts \
                and not pending.nudged and pending.instruction_id == self.p.last_atc.instruction_id:
            # Once for an instruction (ATC saying it again is ATC's own reminder), only when it's ATC's latest words
            # with nothing said since, and not once ATC has asked "how do you read".
            self._call(f"readback:{pending.instruction_id}", ROUTINE, t,
                       f"{self.p.last_atc.station} is waiting for a readback.", again_s=300)

    def _traffic(self, ev: TrafficSnapshot, t: float) -> None:
        own = self.c.own
        if own is None or own.on_ground or self._agl(own) < 500:
            return
        seen = {}
        for x in ev.targets:
            if x.on_ground:
                continue
            nm = haversine_nm(own.lat, own.lon, x.lat, x.lon)
            seen[x.object_id] = nm
            dalt = x.alt_ft - own.alt_msl_ft
            was = self.traffic_nm.get(x.object_id)
            if nm > TRAFFIC_NM or abs(dalt) > TRAFFIC_FT or was is None or nm >= was:
                continue  # far, well above or below, or not closing
            clock = round(((bearing_deg(own.lat, own.lon, x.lat, x.lon) - own.hdg_true) % 360) / 30) % 12 or 12
            if clock in (5, 6, 7) and nm > 1.0:
                continue  # behind: it's theirs to see us
            level = "same level" if abs(dalt) < 300 else f"{abs(round(dalt, -2)):,.0f} {'above' if dalt > 0 else 'below'}"
            self._call(f"traffic:{x.object_id}", ROUTINE, t, f"Traffic, {clock} o'clock, {max(nm, 0.5):.1f} miles, {level}.", again_s=180)
            break
        self.traffic_nm = seen

    # --- checklists, briefings, status ---------------------------------------------------------------------------

    def _prompt_checklist(self, name: str, t: float) -> None:
        if name in self.f.checklists:
            return
        if self._call(f"prompt:{name}", ROUTINE, t, [f"{checklists.NAMES[name]} checklist?",
                                                      f"{checklists.NAMES[name]} checklist when you're ready."]):
            self.offer = Offer("checklist", name, t + 45.0)

    def context(self) -> checklists.Context:
        st = self.engine.state if self.engine is not None else None
        a = st.assignments if st is not None else None
        icao = (st.flight.origin if self.f.takeoff_t is None else st.flight.destination) if st is not None else None
        info = self._atis(icao)
        above = self.c.own is not None and self._altitude(self.c.own) >= self._region().transition_ft
        return checklists.Context(squawk=a.squawk if a else None,
                                  altimeter_inhg=None if above or info is None else info.weather.altimeter_inhg,
                                  takeoff_flaps=self.perf.takeoff_flaps, landing_flaps=self.perf.landing_flaps,
                                  runway=a.departure_runway if a else None)

    def checklist(self, name: str, t: float) -> None:
        """Read ``name`` now (asked for by the pilot)."""
        self.f.checklists.discard(name)
        self.f.said.pop(f"run:{name}", None)
        self._run(name, t, asked=True)

    def _run(self, name: str, t: float, *, key: str | None = None, asked: bool = False) -> None:
        if name in self.f.checklists and not asked:
            return
        key = key or f"run:{name}"
        if self._said(key):
            return
        self.f.checklists.add(name)
        with regions.speaking(self._region()):
            reading, index = checklists.read(name, self.c, self.context(), hands=self.hands)
        if reading.items == 0:
            if asked:
                self._call(key, ROUTINE, t, f"Nothing I can check for the {checklists.NAMES[name].lower()} checklist here.")
            return
        self.run = _Run(name, index, t) if reading.held is not None else None
        self._call(key, ROUTINE, t, reading.text, commands=tuple(reading.fixes))

    def _checklist_tick(self, t: float) -> None:
        """A held checklist goes on once the item's right (looked at every few seconds, for two minutes)."""
        if self.run is None or t - self.run.since < 4:
            return
        if t - self.run.since > 120:
            self.run = None
            return
        item = checklists.ITEMS[self.run.name][self.run.index]
        seen = item.state(self.c, self.context())
        if seen is None or not seen[1]:
            return
        with regions.speaking(self._region()):
            reading, index = checklists.read(self.run.name, self.c, self.context(), hands=self.hands, start=self.run.index)
        name, self.run = self.run.name, (_Run(self.run.name, index, t) if reading.held is not None else None)
        self._call(f"run:{name}:{index}:{t:.0f}", ROUTINE, t, reading.text, commands=tuple(reading.fixes))

    def briefing(self, which: str, t: float) -> None:
        """The departure or arrival briefing, from the clearance, the plan and the ATIS."""
        st = self.engine.state if self.engine is not None else None
        if st is None:
            self._call(f"brief:{t:.0f}", ROUTINE, t, "I've nothing to brief yet.")
            return
        if not which:
            which = "departure" if self.f.takeoff_t is None else "approach"
        self.f.briefed.add(which)
        a, f, perf = st.assignments, st.flight, self.perf
        with regions.speaking(self._region()):
            if which == "departure":
                info = self._atis(f.origin)
                runway = a.departure_runway or (info.runway if info is not None else None)
                parts = [f"Departure briefing. {f.origin} runway {runway}" if runway else f"Departure briefing. Out of {f.origin}"]
                if self.engine.cfg.sid:
                    parts.append(f"the {self.engine.cfg.sid} departure")
                if a.altitude_ft:
                    parts.append(f"initial {speech.altitude_display(a.altitude_ft)}")
                if a.squawk:
                    parts.append(f"squawk {a.squawk}")
                text = ", ".join(parts) + "."
                if perf.v1 and perf.vr and perf.v2:
                    text += f" V1 {perf.v1}, rotate {perf.vr}, V2 {perf.v2}."
                if perf.takeoff_flaps:
                    text += f" Flaps {perf.takeoff_flaps.replace('+F', ' plus F')}."
                if info is not None:
                    text += f" Wind {speech.wind_display(info.weather.wind)}."
                text += (" Anything before V1, we stop. After V1 we continue, fly the departure, and talk about "
                         f"coming back to {f.origin}." if perf.v1 else
                         f" A problem on the roll, we stop; after rotation we fly the departure and come back to {f.origin}.")
            else:
                info = self._atis(f.destination)
                text = f"Arrival briefing. {f.destination}"
                if info is not None:
                    w = info.weather
                    q = f", {'QNH' if self._region().icao else 'altimeter'} {speech.altimeter_display(w.altimeter_inhg)}" if w.altimeter_inhg else ""
                    text += f" information {info.letter}, wind {speech.wind_display(w.wind)}{q}"
                text += "."
                approach = a.approach or (f"{info.approach} runway {info.runway}" if info is not None and info.approach else None)
                if approach:
                    text += f" Expecting the {approach.replace('RWY', 'runway')}."
                if (dh := _minimums(approach)):
                    text += f" Minimums {dh} feet."
                if perf.vref:
                    text += f" Vref {perf.vref}" + (f", flaps {perf.landing_flaps}." if perf.landing_flaps else ".")
                text += " Missed approach as published."
                if a.gate:
                    text += f" Then {a.gate}."
        self._call(f"brief:{which}:{t:.0f}", ROUTINE, t, text)

    def status(self, t: float) -> None:
        own = self.c.own
        parts = []
        if own is not None and own.fuel_lb:
            parts.append(f"fuel {self._fuel(own.fuel_lb)}")
        if (to_go := self.p.to_go()) is not None:
            nm, minutes = to_go
            parts.append(f"{nm:,.0f} miles to go" + (f", about {_hm(minutes)}" if minutes else ""))
            if minutes and own is not None and own.zulu_s is not None:
                eta = (own.zulu_s + minutes * 60) % 86400
                parts.append(f"landing around {int(eta // 3600):02d}{int(eta % 3600 // 60):02d} Zulu")
        self._call(f"status:{t:.0f}", ROUTINE, t, (", ".join(parts).capitalize() + ".") if parts else "All quiet, nothing to report.")

    def _status(self, own: OwnshipState, t: float) -> None:
        if self.phase == "CRUISE" and not self._said("status_auto", STATUS_EVERY_S, t):
            if "status_auto" in self.f.said:
                self.f.said["status_auto"] = t
                before = len(self.queue)
                self.status(t)
                for q in self.queue[before:]:
                    q.priority = CHATTER
            else:
                self.f.said["status_auto"] = t  # the first one half an hour into the cruise


def _minimums(approach: str | None) -> int | None:
    """The decision height for an approach as ATC named it ("ILS runway 34", "RNAV (GPS) 16"), or None (visual)."""
    if not approach:
        return None
    upper = approach.upper()
    if "VISUAL" in upper:
        return None
    kind = next((k for k in sorted(MINIMA, key=len, reverse=True) if upper.startswith(k)), None)
    if kind is None and upper.startswith("RNAV"):
        kind = "LNAV/VNAV" if "LNAV" in upper else "LPV"
    return MINIMA[kind].height_ft if kind else None


# --- the words, as said ------------------------------------------------------------------------------------------

_SPOKEN = (
    (re.compile(r"\bFL(\d{3})\b"), lambda m: "flight level " + speech.digits(m.group(1))),
    (re.compile(r"\b(squawk|code) (\d{4})\b", re.I), lambda m: f"{m.group(1)} {speech.digits(m.group(2))}"),
    (re.compile(r"\b(1[1-3]\d\.\d{1,3})\b"), lambda m: speech.frequency(float(m.group(1)))),
    (re.compile(r"\b((?:2[89]|3[01])\.\d\d)\b"), lambda m: speech.digits(m.group(1).replace(".", ""))),
    (re.compile(r"\b(QNH) (\d{3,4})\b"), lambda m: "Q N H " + speech.digits(m.group(2))),
    (re.compile(r"\brunway (\d{1,2}[LRC]?)\b"), lambda m: "runway " + speech.runway(m.group(1))),
    (re.compile(r"\b(\d{1,2}[LRC]), checked"), lambda m: speech.runway(m.group(1)) + ", checked"),
    (re.compile(r"\binformation ([A-Z])\b"), lambda m: "information " + speech.letter(m.group(1))),
    (re.compile(r"\bTA/RA\b"), lambda m: "T A R A"),
    (re.compile(r"\bV([12])\b"), lambda m: f"vee {'one' if m.group(1) == '1' else 'two'}"),
    (re.compile(r"\bVref\b"), lambda m: "vee ref"),
    (re.compile(r"\bheading (\d{3})\b", re.I), lambda m: "heading " + speech.digits(m.group(1))),
    (re.compile(r"\b(\d{4}) Zulu\b"), lambda m: speech.digits(m.group(1)) + " zulu"),
)


def spoken(text: str, airports: tuple[str, ...] = ()) -> str:
    """``text`` as the copilot says it: codes, frequencies, settings and runways digit by digit, the flight's airports
    without a name spelled ("kilo papa hotel x-ray"), the rest as written."""
    out = text
    for pattern, repl in _SPOKEN:
        out = pattern.sub(repl, out)
    for icao in airports:
        if icao:
            out = re.sub(rf"\b{re.escape(icao)}\b", speech.digits(icao), out)
    return "" if out == text else out
