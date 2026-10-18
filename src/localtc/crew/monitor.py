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
transponder code, the altimeter at the transition, the gear on positive rate and the flaps on schedule after
takeoff, the selected altitude and heading as cleared, the flaps up after landing. The captain's side (parking brake,
engines, thrust, the speedbrake lever, the autopilot, the exterior lights, the flaps for takeoff and landing) it never
touches: it notices when something there is missed and says so in a word ("Spoilers, your side."); asked to, it says
"Your side." (crew.actions PILOT_SIDE). With ``hands = "calls"`` it touches nothing at all and only says.

Pure, like the rest of the copilot: ``observe`` every event, ``due(t)`` for what to say now.
"""

import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core import region as regions
from localtc.atc_core.airport.approaches import MINIMA
from localtc.atc_core.facilities import channel_khz
from localtc.atc_core.phraseology import speech
from localtc.config import PlanPerf
from localtc.crew import checklist_run as run
from localtc.crew.checklist_db import Checklist, ChecklistSet
from localtc.crew.checklist_run import Progress
from localtc.crew.actions import Cockpit
from localtc.crew.answers import LB_PER_KG, Picture, _hm
from localtc.crew.commands import Command
from localtc.crew.watches import WatchMixin
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    ArrivalData,
    AtcAlert,
    AtcTransmission,
    ChecklistState,
    FlightArrived,
    IntercomPressed,
    IntercomReleased,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    ReadbackEvaluated,
    Transcript,
)
from localtc.sim_api.geo import haversine_nm

SAFETY, ROUTINE, CHATTER = 3, 2, 1
VERBOSITY = {"quiet": SAFETY, "standard": ROUTINE, "chatty": CHATTER}
GAP_S = 8.0  # between two of the copilot's own routine calls: a person, not a feed
QUIET_AFTER_RADIO_S = 6.0  # routine calls wait this long after anything said on the radio
GREETING_AFTER_S = 20.0  # the copilot settles in before it says hello
ATIS_RUNWAY_ONLY = True  # a new ATIS letter is news only when the runway, approach or setting changed
EXPIRES_S = {SAFETY: 8.0, ROUTINE: 25.0, CHATTER: 15.0}  # not said by then: too late to be worth it
SPEECH_S_PER_CHAR = 0.065  # how long ATC's words take; the frequency is busy meanwhile
AFTER_ATC_S = 1.5  # and a moment after, for the readback to start
READBACK_GRACE_S = 7.0  # an instruction that wants a readback: the pilot's turn first
STERILE_FT = 10000.0
STATUS_EVERY_S = 1800.0
GEAR_UP_WAIT_S = 1.0  # positive rate, then "gear up" (and the gear up) straight after
FLAP_STEP_WAIT_S = 6.0
RETRACT_MARGIN_KT = 15.0  # a notch up this far below the current detent's placard speed
RETRACT_MIN_AGL = 1000.0
HANDOFF_LATE_S = 60.0
READBACK_LATE_S = 18.0
TAXI_SPEED_MARGIN_KT = 5.0  # a knot or two over the taxi limit isn't worth a word
TAXI_RUNWAY_M = 60.0  # this close to a runway's edge, closing on it: hold short
TOD_WARN_MIN = 10.0
FUEL_WINDOW_S = 900.0  # the cruise burn: fuel used over this long
FUEL_MIN_SPAN_S = 300.0  # ... at least this much of it
FINAL_RESERVE_H = 0.5  # 30 minutes, where the plan gives none
APPROACH_ALLOWANCE_H = 0.15  # the approach and a little more beyond the straight line, at half the cruise burn
DESCENT_BURN = 0.4  # the descent's burn, of the cruise one
FUEL_PLAN_MARGIN_LB = 1100.0  # a projection this far (or 15 percent) under the plan's landing fuel is worth a word
FUEL_MATCH_LB = 1100.0  # on board against the plan's block: this close (or 3 percent) matches
FUEL_STEADY_S = 20.0  # the fuel unchanged this long after engine start: loaded, and checked against the block
FUEL_LAST_H = 1.0  # the projections to landing only this close to the destination: a long way out they're guesses
LEVEL_BELOW_S = 120.0  # cleared higher (or lower) and still level this long: said
PUSH_BRAKE_S = 150.0  # cleared to push and the parking brake still set this long: a word
# The transition: seen this far below it (or above the level), then this far past it, climbing (or descending).
TRANSITION_BAND_FT, TRANSITION_PAST_FT = 300.0, 100.0
SPEED_TREND_S = 10.0  # the speed's trend is taken over this long ...
SPEED_GRACE_S = 60.0  # ... and an assigned speed not yet reached is reminded only after this long off it
DECLINE_QUIET_S = 900.0  # "later", "not now": the copilot's own suggestions wait this long
# The copilot's suggestions (not callouts, not warnings): what "later" quiets.
PROMPTS = ("clearance_prompt", "brief", "prompt:", "flaps_for_takeoff", "tod_soon", "step:", "divert:", "sight",
           "app_mode", "lights:", "push_brake", "seatbelt", "turb:belts")


@dataclass
class Call:
    key: str
    text: str
    priority: int
    t: float
    spoken: str = ""
    commands: tuple[Command, ...] = ()  # the copilot's own hands: done with the words
    urgent: bool = False  # said even over the frequency (the end of the flight, before it stops)
    radio: str = ""  # then said on the radio by the copilot (asking ATC for a step climb, a diversion)
    before_readback: bool = False  # ATC's instruction repeated: in the moment before the readback, or not at all
    valid: Callable[[], bool] | None = None  # still worth saying when its turn comes (None: always)

    @property
    def expires(self) -> float:
        return self.t + EXPIRES_S[self.priority] + (30.0 if self.priority == ROUTINE else 0.0)


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


CHALLENGE_REPEAT_S = 20.0  # no answer: the item asked again
CHALLENGE_DROP_S = 90.0  # no answer still: the checklist waits until it's asked for again


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


class Monitor(WatchMixin):
    def __init__(self, picture: Picture, *, verbosity: str = "standard", hands: str = "pm",
                 perf: PlanPerf | None = None, plan_source: str = "", radio_mode=lambda: "off", seed: int = 0,
                 alternate: str = "", repeat_atc: bool = False) -> None:
        self.p = picture
        self.verbosity = verbosity if verbosity in VERBOSITY else "standard"
        self.hands_setting = hands == "pm"
        self.hands_dead = False  # the aircraft ignored the copilot's switches: calls only, for the rest of the flight
        self.declined_t = -1e9  # the pilot said "later" to a suggestion
        self.hushed_t = -1e9  # ... or "shut up", "chill out": nothing but safety calls for a while
        self.ias_history: list[tuple[float, float]] = []  # (t, IAS) over the last SPEED_TREND_S
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
        self.last_pilot_radio_t = -1e9
        self._fuel_samples: list[tuple[float, float]] = []
        self.fuel_check_from: float | None = None  # the engines started: the fuel against the block, once it's steady
        self._fuel_recent: list[tuple[float, float]] = []
        self.cleared_alt_t = 0.0  # when the cleared altitude was last given
        self.atis_said: dict[str, tuple] = {}  # airport: (runway, approach, setting) last told
        self.book: ChecklistSet | None = None  # the aircraft's checklists (None: it has none)
        self.progress: Progress | None = None  # the checklist being read
        self.paused_progress: Progress | None = None  # one put on hold, to resume
        self.checklist_mode = "challenge"  # challenge, read, auto ([crew] checklist_mode)
        self.checklist_setting = "offer"  # offer, run, off ([crew] checklists)
        self.checklist_events: list = []  # ChecklistState, for the app and the replay
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
        self._init_watches(alternate, repeat_atc)

    # --- in ----------------------------------------------------------------------------------------------------

    @property
    def hands(self) -> bool:
        """The copilot works its own side: as set, in an aircraft whose switches it can reach."""
        return self.hands_setting and self.c.profile.hands and not self.hands_dead

    def hushed(self, t: float) -> None:
        """The pilot told the copilot to be quiet ("shut up", "chill out, man"): its reminders and callouts wait, and
        only safety calls are said, for ``DECLINE_QUIET_S``. "Speed, we're assigned 180" came three times, "missed the
        280 knots at ARYEL" right after "shut up"."""
        self.declined(t)
        self.hushed_t = t
        self.queue = [q for q in self.queue if q.priority >= SAFETY]

    def declined(self, t: float) -> None:
        """The pilot said "later" / "not now" / "quiet": the copilot's suggestions wait (callouts and warnings don't)."""
        self.declined_t = t
        self.queue = [q for q in self.queue if not q.key.startswith(PROMPTS)]
        self.offer = None

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
            self._watch_atc(ev)
        elif isinstance(ev, PttPressed | IntercomPressed):
            self.ptt_down = True
        elif isinstance(ev, PttReleased | IntercomReleased):
            self.ptt_down = False
            self.busy_until = max(self.busy_until, t + 2.0)
        elif isinstance(ev, Transcript) and ev.radio != 0 and ev.text:
            self.pilot_since_atc = True
            self.last_pilot_radio_t = t
            self.queue = [q for q in self.queue if not q.before_readback]  # read back already: nothing to repeat
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
            self._watch_emergency(t, "emergency")
        elif isinstance(ev, ArrivalData):
            self._watch_arrival(ev)
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
        if self.sterile:  # ATC's instruction repeated is the flight's business, not chat
            self.queue = [q for q in self.queue if q.priority > CHATTER or q.before_readback]
        if self.offer is not None and t > self.offer.until:
            self.offer = None
        self.queue = [q for q in self.queue if q.valid is None or q.valid()]  # no longer true: not said
        if not self.queue:
            return []
        ready = [q for q in self.queue if q.t <= t]  # a call for later (a prompt after the readback) waits its turn
        if not ready:
            return []
        ready.sort(key=lambda q: (-q.priority, q.t))
        top = ready[0]
        busy = self.ptt_down or t < self.busy_until or self._readback_due(t) \
            or (top.priority < SAFETY and t - max(self.last_atc_t, self.last_pilot_radio_t) < QUIET_AFTER_RADIO_S)
        if top.before_readback:  # the gap after ATC, before the readback: the only moment it's any use
            busy = self.ptt_down or t < self.busy_until - AFTER_ATC_S + 0.3
        if top.priority < SAFETY and not top.urgent and (busy or (t - self.last_said < GAP_S and not top.before_readback)):
            return []
        if top.priority == CHATTER and len(ready) > 1 and not top.before_readback:
            self.queue.remove(top)  # something more useful is waiting
            return []
        self.queue.remove(top)
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
              again_s: float | None = None, urgent: bool = False, radio: str = "", before_readback: bool = False,
              valid: Callable[[], bool] | None = None) -> bool:
        """Queue ``text`` (one of the wordings, picked) under ``key``, once (or again after ``again_s``). ``valid``:
        checked again when its turn comes (a heading reminder after ATC gave a new one isn't said)."""
        if self._said(key, again_s, t) or any(q.key == key for q in self.queue):
            return False
        if key.startswith(PROMPTS) and t - self.declined_t < DECLINE_QUIET_S and priority < SAFETY:
            return False  # "later": not now
        if t - self.hushed_t < DECLINE_QUIET_S and priority < SAFETY and not urgent:
            return False  # "shut up", "chill out": only what matters for a while
        if priority < VERBOSITY[self.verbosity] and not commands:
            self.f.said[key] = t  # not said at this verbosity, and not later either
            return False
        words = self.rng.choice(text) if isinstance(text, list) else text
        self.f.said[key] = t
        st = self.engine.state if self.engine is not None else None
        airports = (st.flight.origin or "", st.flight.destination or "") if st is not None else ()
        self.queue.append(Call(key, words, max(priority, VERBOSITY[self.verbosity]) if commands else priority, t,
                               spoken=spoken(words, airports), commands=commands if self.hands else (), urgent=urgent,
                               radio=radio, before_readback=before_readback, valid=valid))
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

    def _via(self) -> bool:
        """Cleared to climb or descend via a procedure: its own altitudes, not one cleared altitude, hold."""
        st = self.engine.state if self.engine is not None else None
        if st is None:
            return False
        return getattr(self.engine, "_via_floor", None) is not None

    def _on_approach(self) -> bool:
        """Cleared for the approach (or going around): the cleared altitude and heading no longer hold."""
        return self._cleared("approach") or self.phase in ("APPROACH", "LANDING")

    def _nag(self, key: str, priority: int, t: float, text: str, *, every_s: float, most: int = 2,
             valid: Callable[[], bool] | None = None) -> None:
        """A reminder that may come again after ``every_s``, but only ``most`` times for the same thing."""
        count = int(self.f.said.get(f"{key}#", 0))
        if count >= most or self._said(key, every_s, t) or any(q.key == key for q in self.queue):
            return
        self.f.said.pop(key, None)
        if self._call(key, priority, t, text, valid=valid):
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
                    # The radios are the pilot's (or the radio copilot's): no setting someone else's standby. The late
                    # reminder (``_radio``) is what's useful from the right seat.
                    if self.radio_mode() != "off":
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
                    self._call("clearance_set", ROUTINE, t, f"{_cap(', '.join(parts))} set." if self.hands
                               else f"Don't forget {' and '.join(parts)}.", commands=tuple(cmds))
            elif iid in ("tower.luaw", "tower.takeoff", "tower.takeoff_rnav", "tower.takeoff_sid"):
                own = self.c.own
                if own is not None and own.xpdr_mode not in ("alt",):
                    # Set, not only said ("it didn't do any of what it said"): the transponder's state, checked.
                    self._call("tara", ROUTINE, t + 2, "Transponder TA/RA." if self.hands else "Transponder TA/RA, your side.",
                               commands=(Command("transponder", "alt"),) if self.hands else ())
            elif iid.startswith("approach.cleared") or iid == "approach.intercept_cleared":
                self.f.said.setdefault("approach_cleared_t", t)

    # --- phases ------------------------------------------------------------------------------------------------

    def _on_phase(self, previous: str | None, phase: str, t: float) -> None:
        self.phase = phase
        st = self.engine.state if self.engine is not None else None
        with regions.speaking(self._region()):
            if phase == "RUNWAY_HOLD":
                self._prompt_checklist("line_up", t=t)
            elif phase == "PUSHBACK":
                self._prompt_checklist("before_start", t=t)
            elif phase == "TAXI_OUT":
                self._prompt_checklist("after_start", "taxi", t=t + 5)
            elif phase == "ARRIVAL" and previous == "CRUISE":
                self._prompt_checklist("descent", t=t + 5)
            elif phase == "CRUISE":
                level = (st.flight.cruise_ft if st is not None else None) or (round(self._altitude(self.c.own), -2) if self.c.own else None)
                if level:
                    self._call("toc", ROUTINE, t, [f"Level at {speech.altitude_display(int(level))}.",
                                                   f"Top of climb, level {speech.altitude_display(int(level))}."])
            elif phase == "APPROACH":
                self.f.approaches += 1
            elif phase == "TAXI_IN" and previous == "LANDING":
                self.f.said["rolled_out"] = t  # the after-landing calls once off the runway (``_ground``)

    # --- the aircraft ------------------------------------------------------------------------------------------

    def _systems(self, s: AircraftSystems, t: float) -> None:
        was = self.prev_sys
        own = self.c.own
        if was is None or own is None:
            return
        self.f.max_engines = max(self.f.max_engines, s.engines_running)
        if s.engines_running < was.engines_running and not own.on_ground and "engine" not in self.commanded:
            self._call("engine_failure", SAFETY, t, "Engine failure!", again_s=60)
            self._watch_emergency(t, "engine")
        if s.engines_running < was.engines_running and own.on_ground and own.gs_kt > 40:
            v1 = self.perf.v1
            below = v1 and own.ias_kt < v1
            self._call("engine_failure_roll", SAFETY, t, "Engine failure! Below V1." if below else "Engine failure!")
        if s.light_beacon and not was.light_beacon and own.on_ground and s.engines_running == 0                 and self.phase in (None, "PARKED") and self.f.landing_t is None:
            self._prompt_checklist("before_start", t=t + 2)  # the beacon on: ready to push and start
        if s.engines_running > was.engines_running and own.on_ground and self.phase in (None, "PARKED", "PUSHBACK"):
            self._prompt_checklist("after_start", t=t + 20.0)
            self.fuel_check_from = t  # checked once the fuel stops going up (a refuel still running isn't "under")
        if was.ap_master and not s.ap_master and not own.on_ground and t - self.commanded.get("autopilot", -1e9) > 10 \
                and self.c.autopilot_known:
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
            self._watch(own, prev, t)

    def _preflight(self, own: OwnshipState, t: float) -> None:
        st = self.engine.state if self.engine is not None else None
        if not own.on_ground or self.f.takeoff_t is not None or st is None or self.first_t is None:
            return
        f = st.flight
        with regions.speaking(self._region()):
            if f.origin and t - self.first_t >= GREETING_AFTER_S and not self._said("greeting") and not self.ptt_down:
                # Hello, who and where; nothing else. The fuel is checked once the engines start (an add-on's tanks
                # are often filled after the flight loads), the briefings and the ATIS when asked.
                callsign = speech.callsign_display(f.callsign) if f.callsign else ""
                aircraft = _aircraft_name(self.title) or (speech.aircraft_type(f.aircraft_type)[0] if f.aircraft_type else "")
                route = f"{self._name(f.origin)} to {self._name(f.destination)}" if f.destination else f"out of {self._name(f.origin)}"
                lines = [self.rng.choice(["Hi, I'm with you.", "Hey. All set on my side."]),
                         ", ".join(x for x in (callsign, route, f"in the {aircraft}" if aircraft else "") if x) + "."]
                if not self.plan_source:
                    lines.append("No SimBrief plan loaded, so I'm going by the sim.")
                if not self.c.profile.hands and self.hands_setting:
                    lines.append("I can't reach this aircraft's switches from here, so they're yours; I'll call.")
                self._call("greeting", ROUTINE, t, " ".join(lines))
            # The ATIS: the copilot's side of the altimeter set to it, quietly. Read out only when asked ("weather").
            info = self._atis(f.origin)
            if info is not None and self.atis_seen.get(f.origin or "") != info.letter:
                self.atis_seen[f.origin or ""] = info.letter
                w = info.weather
                if self.hands and w.altimeter_inhg and abs(own.altimeter_inhg - w.altimeter_inhg) > 0.015:
                    self._call(f"atis:{f.origin}:{info.letter}", ROUTINE, t, "",
                               commands=(Command("altimeter", f"{w.altimeter_inhg:.2f}"),))
            # (No "call for the clearance when you're ready": the captain calls when the captain's ready.)
            if self._cleared("pushback") and own.parking_brake and own.gs_kt < 0.5 and not self._said("push_brake") \
                    and self.phase in (None, "PARKED"):
                when = self.f.said.get("pushback_seen") or self.f.said.setdefault("pushback_seen", t)
                if t - when > PUSH_BRAKE_S:  # a tug takes its time (GSX: minutes): not a nag 10 s after the approval
                    self._call("push_brake", ROUTINE, t, "We're cleared to push; parking brake's still set.")

    def _always(self, own: OwnshipState, prev: OwnshipState, t: float) -> None:
        self._fuel_steady(own, t)
        self._always_rest(own, prev, t)

    def _always_rest(self, own: OwnshipState, prev: OwnshipState, t: float) -> None:
        s, p = self.c.systems, self.c.profile
        airborne = not own.on_ground
        if airborne and self._agl(own) > 50:
            stall = s is not None and s.stall_warning
            if s is not None and not stall:
                stall = self._below_stall(own, s)
            if stall:
                self._call("stall", SAFETY, t, "Speed, speed!", again_s=10)
        if (s is not None and s.overspeed_warning) or (p.vmo_kt and own.ias_kt > p.vmo_kt + 3):
            self._call("overspeed", SAFETY, t, "Overspeed!", again_s=15)
        flaps = self.c.flaps_index
        if airborne and flaps and (vfe := p.vfe_for(flaps)) and own.ias_kt > vfe + 5:
            self._call("flap_speed", SAFETY, t, f"Flap speed! {own.ias_kt:.0f}, limit {vfe:.0f}.", again_s=15)
        if airborne and own.gear_down and p.gear_extended_kt and own.ias_kt > p.gear_extended_kt + 5:
            self._call("gear_speed", SAFETY, t, f"Gear speed! {own.ias_kt:.0f}, limit {p.gear_extended_kt:.0f}.", again_s=15)

    def _fuel_steady(self, own: OwnshipState, t: float) -> None:
        """The block check, once the fuel has stopped changing for ``FUEL_STEADY_S`` (a refuel in progress at
        engine start had "120,800 pounds under the plan's block"); not after takeoff."""
        if self.fuel_check_from is None or not own.fuel_lb:
            return
        if not own.on_ground:
            self.fuel_check_from = None
            return
        self._fuel_recent = [x for x in self._fuel_recent if t - x[0] <= FUEL_STEADY_S] + [(t, own.fuel_lb)]
        if t - self.fuel_check_from < FUEL_STEADY_S or self._fuel_recent[0][0] > t - FUEL_STEADY_S + 1:
            return
        amounts = [f for _, f in self._fuel_recent]
        if max(amounts) - min(amounts) <= max(0.005 * own.fuel_lb, 50.0):
            self.fuel_check_from = None
            self._fuel_check(own, t)

    def _fuel_check(self, own: OwnshipState, t: float) -> None:
        """The fuel against the plan's block, once, when the engines start (by then it's loaded): only when it's off."""
        block = self.perf.block_fuel_lb
        if not block or not own.fuel_lb or self._said("fuel_check"):
            return
        diff = own.fuel_lb - block
        if abs(diff) <= max(0.03 * block, FUEL_MATCH_LB):
            self.f.said["fuel_check"] = t
            return
        self._call("fuel_check", ROUTINE, t, f"Fuel's {self._fuel(own.fuel_lb)}, {self._fuel(abs(diff))} "
                   f"{'over' if diff > 0 else 'under'} the plan's block.")

    def _below_stall(self, own: OwnshipState, s: AircraftSystems) -> bool:
        """The sim's stall warning is the call; this only backs it up where the aircraft has none. The design stall
        speeds are rough (the stock A320 says 179 knots clean, its green dot), so only well below them, slowing, and
        never with the flaps out above the landing stall speed plus a margin."""
        if (self.c.flaps_index or 0) > 0 or own.gear_down:
            ref = s.vs0_kt
            margin = 1.0
        else:
            ref = s.vs1_kt
            margin = 0.85  # VS1 is often the clean manoeuvring speed, well above the real stall
        if not 30 < ref < 220:
            return False
        decelerating = self.prev is not None and own.ias_kt < self.prev.ias_kt
        return own.ias_kt < ref * margin and decelerating

    def _atis_news(self, icao: str, info) -> bool:
        """A new ATIS letter is worth a word only the first time, or when the runway, the approach or the altimeter
        changed: the wind going from 7 to 8 knots isn't news."""
        w = info.weather
        now = (info.runway, info.approach, round(w.altimeter_inhg or 0, 2))
        was = self.atis_said.get(icao)
        if was is not None and was == now:
            self.atis_seen[icao] = info.letter  # seen, and nothing to say about it
            return False
        self.atis_said[icao] = now
        return True

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
        if self.phase == "TAXI_IN" and "rolled_out" in self.f.said and not own.on_runway and not self._said("after_landing"):
            self._after_landing(t)
        # Taxiing.
        if self.phase in ("TAXI_OUT", "TAXI_IN") and own.gs_kt > p.taxi_max_kt + TAXI_SPEED_MARGIN_KT and not own.on_runway:
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
            if self.c.flaps_index == 0 and self.c.flap_positions:
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
            # No "V1" or "rotate": the pilot flying calls the speeds the aircraft's own voice doesn't (the
            # copilot calling them sounded as if it were flying).
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
            still_down = lambda: self.c.own is not None and self.c.own.gear_down and t - self.commanded.get("gear", -1e9) > 10  # noqa: E731
            if self.hands:
                self._call("gear_up", ROUTINE, t, "Gear up.", commands=(Command("gear", "up"),), valid=still_down)
            elif agl > 400:
                self._call("gear_up", ROUTINE, t, "Gear's still down.", valid=still_down)
        flaps = self.c.flaps_index
        if departing and not own.gear_down and flaps and agl > RETRACT_MIN_AGL and own.vs_fpm > 0 \
                and t - self.flap_step_t > FLAP_STEP_WAIT_S and t - self.commanded.get("flaps", -1e9) > 15:
            vfe = p.vfe_for(flaps)
            if vfe and own.ias_kt >= vfe - RETRACT_MARGIN_KT:
                up = flaps - 1
                name = "up" if up == 0 else self._flaps(up)
                if self.hands:
                    self.flap_step_t = t
                    self._call(f"flaps_retract:{up}", ROUTINE, t, f"Speed checked, flaps {name}.",
                               commands=(Command("flaps", "up" if up == 0 else p.detent_name(up, self.c.flap_positions)),))
                else:
                    self._call(f"flaps_retract:{up}", ROUTINE, t, f"Speed checked, flaps {name}?")
            elif not vfe and agl > 3000 and own.ias_kt > 210:
                self._call("flaps_still_out", ROUTINE, t, "Flaps are still out.")
        if departing and not own.gear_down and flaps == 0 and agl > 1500:
            self._prompt_checklist("after_takeoff", t=t)
        # Climbing and descending: the cleared altitude.
        target = st.assignments.altitude_ft if st is not None else None
        if target != self.cleared_alt:
            self.cleared_alt, self.captured, self.off_since = target, None, None
            self.cleared_alt_t = t
            if target and self.hands and s is not None and abs(s.ap_altitude_sel - target) > 50 and self.f.takeoff_t is not None:
                self._call(f"set_alt:{target}", ROUTINE, t, f"{speech.altitude_display(target)} set.",
                           commands=(Command("altitude", str(target)),))
        if target and not self._on_approach():
            if (prev_alt < target - 1000 <= alt and own.vs_fpm > 500) or (prev_alt > target + 1000 >= alt and own.vs_fpm < -500):
                self._call(f"1000_to_go:{target}:{round(t, -2)}", ROUTINE, t, "One thousand to go.")
            if abs(alt - target) < 150:
                self.captured = target
            via = self._via()
            # Not on the way up out of the departure, low down: a SID's level-offs ("6,000 at DET") are flown there.
            sid_climb = self.phase == "DEPARTURE" and alt < 10000 and alt < target
            if self.captured != target and abs(alt - target) >= 1000 and abs(own.vs_fpm) < 300 and not via and not sid_climb \
                    and t - self.cleared_alt_t > LEVEL_BELOW_S and self.phase in ("DEPARTURE", "CRUISE", "ARRIVAL"):
                # Cleared to FL370 and still level at 350 an hour later (the selected altitude never reached the
                # aircraft): the pilot monitoring says so.
                where = "below" if alt < target else "above"
                self._nag(f"level_{where}:{target}", ROUTINE, t, f"We're cleared to {speech.altitude_display(target)}, "
                          f"still level at {speech.altitude_display(int(round(alt, -2)))}.", every_s=600,
                          valid=lambda: st.assignments.altitude_ft == target)
            if self.captured == target and abs(alt - target) > 300 and not via:
                self.off_since = self.off_since or t
                if t - self.off_since > 5:
                    off = round(abs(alt - target), -2)
                    self._nag(f"altitude_dev:{target}", SAFETY, t, f"Altitude! {off:,.0f} {'high' if alt > target else 'low'}.",
                              every_s=45, valid=lambda: st.assignments.altitude_ft == target and not self._on_approach())
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
                              f"Heading, we're assigned {heading:03d}.", every_s=60,
                              valid=lambda: st.assignments.heading == heading and not self._on_approach())
            else:
                self.heading_off_since = None
        # Ten thousand, the transition, the speed limit.
        region = self._region()
        if prev_alt < STERILE_FT <= alt and own.vs_fpm > 0:
            self._call("10k_climb", ROUTINE, t, "Ten thousand.")
        if prev_alt > STERILE_FT >= alt and own.vs_fpm < 0:
            belts = s is None or not s.seatbelt or "seatbelt" in p.unread
            if self.hands and belts and "seatbelt" not in self.c.dead:
                self._call("10k_descent", ROUTINE, t, "Ten thousand. Seatbelt sign on.", commands=(Command("seatbelt", "on"),))
            else:
                self._call("10k_descent", ROUTINE, t, "Ten thousand.")
        ta = region.transition_ft
        # Climbing out through it, not a bump at a level-off on the way down (Paris, 4,900 ft on the approach: standard
        # set again over the QNH just set).
        going_around = self.p.last_atc is not None and "go_around" in (self.p.last_atc.instruction_id or "")             and t - self.p.last_atc.t < 300
        climbing_out = self.phase in (None, "TAKEOFF", "DEPARTURE", "CRUISE") or going_around
        if min(alt, prev_alt) < ta - TRANSITION_BAND_FT:
            self.f.said["below_ta"] = t
        if alt >= ta + TRANSITION_PAST_FT and own.vs_fpm > 300 and climbing_out and "below_ta" in self.f.said:
            # Set whatever the altimeter reads: a QNH of 1013 reads 29.92 and isn't standard (it was taken for set,
            # and nothing was).
            self._call("transition_climb", ROUTINE, t, "Transition, standard set." if self.hands else "Transition, standard.",
                       commands=(Command("altimeter", "29.92"),) if self.hands else ())
        tl = ta + (1000 if region.icao else 0)
        if max(alt, prev_alt) > tl + TRANSITION_BAND_FT:
            self.f.said["above_tl"] = t
        if alt <= tl - TRANSITION_PAST_FT and own.vs_fpm < -300 and st is not None and "above_tl" in self.f.said:
            info = self._atis(st.flight.destination)
            if info is not None and info.weather.altimeter_inhg:
                q = info.weather.altimeter_inhg
                word = "QNH" if region.icao else "altimeter"
                self._call("transition_descent", ROUTINE, t, f"Transition level, {word} {speech.altimeter_display(q)}"
                           + (" set." if self.hands else "."), commands=(Command("altimeter", f"{q:.2f}"),))
            else:
                self._call("transition_descent", ROUTINE, t, "Transition level, local altimeter.")
            self._prompt_checklist("approach", "descent", t=t + 3)
        limit = 250 if st is None or not st.assignments.speed_kt or st.assignments.speed_kt <= 250 else None
        if limit and alt < STERILE_FT - 300 and agl > 1500 and own.ias_kt > limit + 10:
            self.speed_high_since = self.speed_high_since or t
            if t - self.speed_high_since > 10:
                self._nag("speed_250", ROUTINE, t, "Speed, two fifty below ten thousand.", every_s=120)
        else:
            self.speed_high_since = None
        # An assigned speed holds until the approach clearance (and then it's the approach's own).
        assigned_kt = st.assignments.speed_kt if st is not None else None
        history = self.ias_history
        history.append((t, own.ias_kt))
        while history and t - history[0][0] > SPEED_TREND_S:
            history.pop(0)
        trend = own.ias_kt - history[0][1]  # over the last SPEED_TREND_S
        # Slowing down to it (or speeding up to it) already: nothing to remind. "Speed, we're assigned 180" three
        # times while decelerating from 210 ("we're decelerating, chill out, man").
        converging = (own.ias_kt - assigned_kt) * trend < 0 and abs(trend) >= 3 if assigned_kt else False
        if assigned_kt and abs(own.ias_kt - assigned_kt) > 15 and agl > 1500 and not self._on_approach() and not converging:
            since = self.f.said.setdefault("speed_off_t", t)
            if t - since > SPEED_GRACE_S:
                self._nag(f"speed_dev:{assigned_kt}", ROUTINE, t, f"Speed, we're assigned {assigned_kt}.", every_s=90,
                          valid=lambda: st.assignments.speed_kt == assigned_kt and not self._on_approach())
        else:
            self.f.said.pop("speed_off_t", None)
        # (No "in cloud at 2 degrees; engine anti-ice?" questions: ice building up is called when there is some.)
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
            self._call("tod_soon", ROUTINE, t, "Top of descent in about ten minutes.")
        if self.phase == "CRUISE" and minutes <= 0 and to_go > 20:
            self._call("tod", ROUTINE, t, "That's top of descent." + ("" if descending else " No descent clearance yet."))
        # Fuel against the plan and the reserve: only from a steady cruise burn, measured over minutes. The burn in
        # the climb is twice the cruise one, and projecting it called "below final reserve" just after takeoff.
        # The samples are of level cruise only: a climb or descent starts them again (a climb's burn leaking into the
        # window had a level-off projecting "we'd land with 0").
        if own.fuel_lb and self.phase == "CRUISE" and abs(own.vs_fpm) <= 300:
            self._fuel_samples.append((t, own.fuel_lb))
            self._fuel_samples = [x for x in self._fuel_samples if t - x[0] <= FUEL_WINDOW_S]
        else:
            self._fuel_samples = []
        burn = self._cruise_burn_pph(own, t)
        if burn:
            self.p.burn_pph = burn
        last_hour = to_go / own.gs_kt <= FUEL_LAST_H  # the pilot: "only an hour away, then it's a real concern"
        if burn and own.fuel_lb and last_hour and not self.p.fuel_doubted:
            at_landing = own.fuel_lb - landing_burn(burn, to_go, tod_nm, own.gs_kt)
            reserve = self.perf.reserve_fuel_lb or burn * FINAL_RESERVE_H
            if at_landing < reserve:
                landing, final = self._fuel(max(at_landing, 0)), self._fuel(reserve)
                where = "right at" if landing == final else "under"  # "2,500 kilos, under the final reserve of 2,500"
                self._call("fuel_reserve", SAFETY, t, f"Fuel: at this burn we'd land with about {landing}, "
                           f"{where} the final reserve of {final}.", again_s=1800)
                self._watch_emergency(t, "fuel")
            elif self.perf.landing_fuel_lb and self.perf.landing_fuel_lb - at_landing > max(
                    0.15 * self.perf.landing_fuel_lb, FUEL_PLAN_MARGIN_LB):
                short = self.perf.landing_fuel_lb - at_landing
                self._call("fuel_plan", ROUTINE, t, f"We're tracking about {self._fuel(short)} under the planned landing fuel.", again_s=2700)
        info = self._atis(st.flight.destination)
        # Not once ATC has said which approach to expect, nor after a go-around: then the ATIS's runway and approach
        # only contradict the clearance ("expect VISUAL approach" read out after being vectored for the ILS).
        if info is not None and self.atis_seen.get(st.flight.destination or "") != info.letter and to_go < 250 \
                and not st.assignments.approach and not self.f.approaches \
                and self._atis_news(st.flight.destination or "", info):
            self.atis_seen[st.flight.destination or ""] = info.letter
            w = info.weather
            with regions.speaking(regions.region_for(st.flight.destination)):
                q = f", {'QNH' if regions.CURRENT.get().icao else 'altimeter'} {speech.altimeter_display(w.altimeter_inhg)}" if w.altimeter_inhg else ""
                text = (f"{self._name(st.flight.destination)} has information {speech.letter(info.letter).capitalize()}: "
                        f"runway {info.runway}, "
                        f"wind {speech.wind_display(w.wind)}{q}, expect {info.approach or 'the'} approach.")
            self._call(f"atis:{st.flight.destination}:{info.letter}", ROUTINE, t, text.replace("expect the approach", "expect vectors"))

    def _cruise_burn_pph(self, own: OwnshipState, t: float) -> float | None:
        """The fuel burn in pounds an hour, from the fuel used over the last minutes of level cruise; None until
        there's that much of it (in the climb, the descent, or just after a level-off)."""
        if self.phase != "CRUISE" or abs(own.vs_fpm) > 300 or len(self._fuel_samples) < 2:
            return None
        (t0, f0), (t1, f1) = self._fuel_samples[0], self._fuel_samples[-1]
        if t1 - t0 < FUEL_MIN_SPAN_S or f0 <= f1:
            return None
        return (f0 - f1) / (t1 - t0) * 3600

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
            if ils and cleared_t is not None and t - cleared_t > 30 and s.ap_master and not s.ap_approach and self.c.autopilot_known:
                self._call(f"app_mode:{n}", ROUTINE, t, "We're cleared the ILS; approach mode?")
        if self.phase in ("APPROACH", "LANDING") and own.vs_fpm < 0:
            positions = self.c.flap_positions
            flaps = self.c.flaps_index
            nxt = flaps + 1 if flaps is not None else positions + 1
            vfe = p.vfe_for(nxt) if nxt <= positions else 0
            if vfe and own.ias_kt < vfe - 5 and agl < 6000 and t - self.commanded.get("flaps", -1e9) > 20:
                self._call(f"flaps_ext:{n}:{nxt}", ROUTINE, t, f"Speed checked for flaps {self._flaps(nxt)}.")
            if own.gear_down is False and prev_agl > 2000 >= agl:
                self._call(f"gear_prompt:{n}", ROUTINE, t, "Two thousand. Gear down?")
            if own.gear_down and flaps is not None and flaps >= p.landing_index(positions) and agl < 3000:
                self._prompt_checklist("landing", t=t)
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
                # The lights only when they could be there: out of cloud, the ceiling and visibility for it. Never in fog.
                self._call(f"minimums:{n}", ROUTINE, t, "Minimums. Approach lights in sight." if self._lights_plausible(dh)
                           else "Minimums.")

    def _unstable(self, own: OwnshipState) -> list[str]:
        p = self.c.profile
        out = []
        if not own.gear_down:
            out.append("gear")
        if self.c.flap_positions and self.c.flaps_index is not None and self.c.flaps_index < p.landing_index(self.c.flap_positions):
            out.append("flaps")
        if own.vs_fpm < -1100:
            out.append(f"sinking {abs(round(own.vs_fpm, -2)):,.0f}")
        if (vref := self._vref()) and not (vref - 5 <= own.ias_kt <= vref + 25):
            out.append(f"speed {own.ias_kt:.0f}")
        return out

    def _after_landing(self, t: float) -> None:
        own, s = self.c.own, self.c.systems
        cmds = []
        if own is not None and self.c.flaps_index:
            cmds.append(Command("flaps", "up"))
        if self.hands and cmds:
            text = "Clear of the runway. Flaps up."
        else:
            text = "Clear of the runway."
        if s is not None and s.spoilers_armed:  # the speedbrake lever is the pilot's side: a word, not a hand
            text += " Spoilers, your side."
        self._call("after_landing", ROUTINE, t, text, commands=tuple(cmds))
        self._prompt_checklist("after_landing", t=t + 5)

    def _parked(self, t: float, *, arrived: bool = False) -> None:
        own, s = self.c.own, self.c.systems
        if own is None or self.f.landing_t is None:
            return
        if not self._said("summary"):
            self._call("summary", ROUTINE, t, self._summary(own), urgent=arrived)
        if s is not None and s.engines_running == 0 and not own.parking_brake:
            self._call("park_brake", ROUTINE, t, "Parking brake?")
        if s is not None and s.engines_running == 0:
            self._prompt_checklist("parking", t=t + 2)

    def _summary(self, own: OwnshipState) -> str:
        """What a first officer says at the gate: a word on the landing and the flight, not a score sheet (the
        numbers are in the logbook)."""
        fpm = abs(self.f.landing_fpm) if self.f.landing_fpm is not None else None
        if fpm is None:
            landing = "We're in."
        elif fpm < 120:
            landing = self.rng.choice(["Lovely landing.", "Greaser, nice one."])
        elif fpm < 300:
            landing = self.rng.choice(["Nice landing.", "Good landing."])
        elif fpm < 500:
            landing = "Bit firm, but fine."
        else:
            landing = "Firm one. Might want a look at that in the logbook."
        tail = ""
        if self.f.takeoff_t is not None and self.f.landing_t is not None:
            tail = f" {_hm((self.f.landing_t - self.f.takeoff_t) / 60).capitalize()} in the air."
        return landing + tail

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
        # (No "they're waiting for a readback": ATC asks for it itself when it wants one, and the copilot telling the
        # captain to answer the radio isn't how a crew works.)

    # (No traffic calls: telling the crew about other aircraft is ATC's job, not the pilot monitoring's.)

    # --- checklists, briefings, status ---------------------------------------------------------------------------

    def set_checklists(self, book: "ChecklistSet | None") -> None:
        """The aircraft's checklists (``checklist_db``): its own, or its family's; None: there are none."""
        self.book = book
        self.progress = None

    def values(self) -> dict[str, str]:
        """What a checklist's {placeholders} are now: the clearance, the plan, the ATIS, the aircraft."""
        st = self.engine.state if self.engine is not None else None
        a = st.assignments if st is not None else None
        before_takeoff = self.f.takeoff_t is None
        icao = (st.flight.origin if before_takeoff else st.flight.destination) if st is not None else None
        info = self._atis(icao)
        own = self.c.own
        out: dict[str, str] = {}
        above = own is not None and self._altitude(own) >= self._region().transition_ft - 1000 and not own.on_ground
        if above:
            out["altimeter"] = "STD"
        elif info is not None and info.weather.altimeter_inhg:
            q = info.weather.altimeter_inhg
            out["altimeter"] = str(round(q * 33.8639)) if self._region().icao else f"{q:.2f}"
        if a is not None:
            if a.squawk:
                out["squawk"] = a.squawk
            if a.altitude_ft:
                out["altitude"] = speech.altitude_display(a.altitude_ft)
            runway = a.departure_runway if before_takeoff else a.arrival_runway
            if runway:
                out["runway"] = runway
        perf = self.perf
        flaps = perf.takeoff_flaps if before_takeoff else perf.landing_flaps
        if flaps:
            out["flaps"] = flaps
        if perf.v1 and perf.vr and perf.v2:
            out["speeds"] = f"V1 {perf.v1}, VR {perf.vr}, V2 {perf.v2}"
            out["v2"] = str(perf.v2)
        if perf.vref:
            out["vref"] = str(perf.vref)
        if own is not None and own.fuel_lb:
            out["fuel"] = self._fuel(own.fuel_lb).upper()
        return out

    def _prompt_checklist(self, *phases: str, t: float) -> None:
        """A checklist due now (the first of ``phases`` this aircraft has one for, not yet done): offered ("Before
        start checklist?"), or read straight away when the pilot's set it so, or nothing without one."""
        if self.book is None or self.checklist_setting == "off" or self.progress is not None:
            return
        for cl in (c for phase in phases for c in self.book.due(phase)):
            if cl.id in self.f.checklists:
                continue
            if self.checklist_setting == "run":
                self._start(cl, t)
            elif self._call(f"prompt:{cl.id}", ROUTINE, t, [f"{cl.name} checklist?", f"{cl.name} checklist when you're ready."]):
                self.offer = Offer("checklist", cl.id, t + 45.0)
            return

    def checklist(self, name: str, t: float) -> None:
        """Read ``name`` now (asked for: by its name or its phase), or the pilot's control of the one being read:
        "pause", "resume", "skip"."""
        if name in ("pause", "resume", "skip", "stop"):
            self._control_checklist(name, t)
            return
        if self.book is None:
            self._call(f"nochecklist:{t:.0f}", ROUTINE, t, "I don't have a checklist for this aircraft.")
            return
        cl = self.book.get(name) if name else self._next_due()
        if cl is None:
            self._call(f"nochecklist:{name}:{t:.0f}", ROUTINE, t, f"There's no {name.replace('_', ' ')} checklist for this aircraft."
                       if name else "Which checklist?")
            return
        self.f.checklists.discard(cl.id)
        self._start(cl, t)

    def _next_due(self) -> "Checklist | None":
        """The first checklist not read yet of the phase the flight is in or just past."""
        order = {"PARKED": ("cockpit_prep", "before_start", "parking", "securing"), "PUSHBACK": ("before_start", "after_start"),
                 "TAXI_OUT": ("after_start", "taxi", "line_up"), "RUNWAY_HOLD": ("line_up",), "TAKEOFF": ("line_up",),
                 "DEPARTURE": ("after_takeoff",), "CRUISE": ("cruise", "descent"), "ARRIVAL": ("descent", "approach"),
                 "APPROACH": ("approach", "landing"), "LANDING": ("landing",), "TAXI_IN": ("after_landing", "parking")}
        for phase in order.get(self.phase or "PARKED", ()):
            for cl in self.book.due(phase):
                if cl.id not in self.f.checklists:
                    return cl
        return None

    def _start(self, cl: "Checklist", t: float) -> None:
        self.f.checklists.add(cl.id)
        self.progress = Progress(cl, asked_t=t)
        self._checklist_event(t, "started")
        self._advance(t, opener=f"{cl.name} checklist.")

    def _advance(self, t: float, opener: str = "") -> None:
        """Read on from the line ``progress.index``: the copilot's own side answered (and set), what it sees checked;
        in challenge and response it stops at the captain's next item and waits; it holds at one that's wrong."""
        pr = self.progress
        values = self.values()
        lines = [opener] if opener else []
        cmds: list[Command] = []
        mode = self.checklist_mode
        with regions.speaking(self._region()):
            while pr.index < len(pr.checklist.lines):
                line = pr.checklist.lines[pr.index]
                if line.note:
                    lines.append(run.say(line, values).rstrip("?"))
                    pr.results[pr.index] = "read"
                    pr.index += 1
                    continue
                shown = run.seen(line, self.c, values)
                mine = run.side(line) == "copilot"
                if shown is not None and shown[1] is False and (mine or mode != "challenge"):
                    # (Challenge and response: the captain's item is asked all the same; the answer's checked.)
                    fix = run.fix_for(line, self.c, values) if (mine or mode == "auto") and self.hands else None
                    if fix is not None and mode != "read":
                        cmds.append(fix)
                        lines.append(run.done(line, values, "set"))
                        pr.results[pr.index] = "ok"
                        pr.index += 1
                        continue
                    if line.action is not None and mode == "auto" and self.hands:
                        cmds.append(Command("sim_event", str(line.action[1]), line.action[0]))
                        lines.append(run.done(line, values, "done"))
                        pr.results[pr.index] = "ok"
                        pr.index += 1
                        continue
                    if mode == "read":
                        lines.append(f"{run._speakable(line.challenge)} shows {shown[0]}.")
                        pr.results[pr.index] = "wrong"
                        pr.index += 1
                        continue
                    # The captain's, and not right: said, and the checklist holds here till it is.
                    lines.append(f"{run._speakable(line.challenge)} shows {shown[0]}. Holding the checklist till it's right.")
                    pr.held, pr.asked_t = True, t
                    self._checklist_event(t, "held")
                    break
                if mine or mode in ("read", "auto") or (shown is not None and shown[1] is True and mode != "challenge"):
                    words = run.say(line, values)
                    if shown is not None and "?" in words:
                        words = f"{run._speakable(line.challenge)}, {shown[0]}."
                    lines.append(words)
                    pr.results[pr.index] = "ok" if shown is not None and shown[1] is not False else "read"
                    pr.index += 1
                    continue
                # Challenge and response: the captain's item asked, and waited for.
                lines.append(f"{run._speakable(line.challenge)}?")
                pr.asked_t, pr.repeats = t, 0
                self._checklist_event(t, "waiting")
                break
            else:
                lines.append(f"{pr.checklist.name} checklist complete.")
                self._checklist_event(t, "completed")
                self.progress = None
        if lines:
            self._call(f"run:{pr.checklist.id}:{pr.index}:{t:.2f}", ROUTINE, t, " ".join(lines), commands=tuple(cmds))

    def respond(self, text: str, t: float) -> bool:
        """The captain's answer to the item asked; True when it was one (the next read). One the aircraft shows
        otherwise holds the checklist."""
        pr = self.progress
        if pr is None or pr.paused or pr.index >= len(pr.checklist.lines):
            return False
        if re.search(r"\b(?:skip|next item|skip (?:it|that|this))\b", text.lower()):
            self._control_checklist("skip", t)
            return True
        line = pr.checklist.lines[pr.index]
        values = self.values()
        shown = run.seen(line, self.c, values)
        if shown is not None and shown[1] is False and _says(text, shown[0]):
            # The captain's answer is what the aircraft shows ("one plus F", with the plan's 2): the captain's call.
            # Said once, and on: held, the answer went unheard and the checklist never moved again.
            pr.held = False
            pr.results[pr.index] = "ok"
            pr.index += 1
            plan = values.get(run.sense_key(line.challenge) or "", "")
            self._advance(t, opener=f"{run._speakable(line.challenge)} {shown[0]}" + (f", the plan had {plan}." if plan else "."))
            return True
        if pr.held:
            return False
        if shown is not None and shown[1] is False:
            pr.held, pr.asked_t = True, t
            self._checklist_event(t, "held")
            with regions.speaking(self._region()):
                self._call(f"run:{pr.checklist.id}:held:{t:.0f}", ROUTINE, t,
                           f"{run._speakable(line.challenge)} shows {shown[0]}. Holding the checklist till it's right.")
            return True
        pr.results[pr.index] = "ok" if shown is not None else "read"  # the captain's word for what it can't see
        pr.index += 1
        self._advance(t)
        return True

    def _control_checklist(self, what: str, t: float) -> None:
        pr = self.progress
        if pr is None:
            if what == "resume" and self.paused_progress is not None:
                self.progress, self.paused_progress = self.paused_progress, None
                self.progress.paused = False
                self._checklist_event(t, "resumed")
                self._advance(t, opener=f"{self.progress.checklist.name} checklist, continuing.")
            else:
                self._call(f"checklist:{what}:{t:.0f}", ROUTINE, t, "No checklist running.")
            return
        if what in ("pause", "stop"):
            pr.paused = True
            self.paused_progress, self.progress = pr, None
            self._checklist_event(t, "paused")
            self._call(f"checklist:pause:{t:.0f}", ROUTINE, t, f"{pr.checklist.name} checklist on hold.")
        elif what == "skip" and pr.index < len(pr.checklist.lines):
            pr.results[pr.index] = "skipped"
            pr.skipped[pr.index] = "skipped by the captain"
            pr.index += 1
            pr.held = False
            self._checklist_event(t, "skipped")
            self._advance(t, opener="Skipped.")
        elif what == "resume":
            pr.held = False  # held on an item: asked again, the captain's answer takes it on
            self._advance(t)

    def _checklist_tick(self, t: float) -> None:
        """A held item goes on by itself once the aircraft shows it right; an item asked and not answered is asked
        once more, then the checklist is put on hold (said), to resume when asked."""
        pr = self.progress
        if pr is None or pr.index >= len(pr.checklist.lines):
            return
        line = pr.checklist.lines[pr.index]
        if pr.held:
            shown = run.seen(line, self.c, self.values())
            if shown is not None and shown[1] is not False and t - pr.asked_t > 3:
                pr.held = False
                pr.results[pr.index] = "ok"
                pr.index += 1
                with regions.speaking(self._region()):
                    self._advance(t, opener=f"{run._speakable(line.challenge)}, {shown[0]}.")
            return
        if t - pr.asked_t > CHALLENGE_DROP_S:
            self._control_checklist("pause", t)
        elif t - pr.asked_t > CHALLENGE_REPEAT_S * (pr.repeats + 1) and pr.repeats < 1:
            pr.repeats += 1
            with regions.speaking(self._region()):
                self._call(f"run:{pr.checklist.id}:again:{t:.0f}", ROUTINE, t, f"{run._speakable(line.challenge)}?")

    def _checklist_event(self, t: float, status: str) -> None:
        pr = self.progress or self.paused_progress
        if pr is None:
            return
        self.checklist_events.append(ChecklistState(
            t=t, checklist=pr.checklist.id, name=pr.checklist.name, status=status, index=pr.index,
            source=self.book.source if self.book is not None else "",
            data=json.dumps(pr.to_json(self.values()))))

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
        self._call(f"status:{t:.0f}", ROUTINE, t, (_cap(", ".join(parts)) + ".") if parts else "All quiet, nothing to report.")

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


def landing_burn(burn_pph: float, to_go_nm: float, tod_nm: float, gs_kt: float) -> float:
    """The fuel burned from here to landing: the cruise at the cruise burn to the top of descent, the descent at a
    fraction of it (idle, and slower), and a little for the approach."""
    cruise_nm = max(to_go_nm - tod_nm, 0.0)
    descent_nm = min(tod_nm, to_go_nm)
    return (burn_pph * cruise_nm / gs_kt + burn_pph * DESCENT_BURN * descent_nm / (gs_kt * 0.75)
            + burn_pph * 0.5 * APPROACH_ALLOWANCE_H)


def _cap(text: str) -> str:
    """The first letter up, the rest as it is ("initial FL090", not "initial fl090")."""
    return text[:1].upper() + text[1:]


def _aircraft_name(title: str) -> str:
    """The sim's title as a pilot says it: "FSLabs A321-211 - Air Canada (C-FJNX)" -> "A321"."""
    m = re.search(r"\b(A3[1-8]\d|B7[0-8]7|7[0-8]7|[A-Z]{1,2}\d{2,3})(?:neo|NEO|-\d+)?", title or "")
    if m is None:
        return ""
    name = m.group(1)
    return name + ("neo" if re.search(r"neo", title, re.I) and name.startswith("A3") else "")


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
    (re.compile(r"\binformation ([A-Z])(?![\w-])"), lambda m: "information " + speech.letter(m.group(1))),  # not X-ray's X
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


def _says(answer: str, shown: str) -> bool:
    """The captain's answer names the setting shown ("one plus F" for 1+F, "two" for 2)."""
    words = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "full": "full", "plus": "+", "up": "0"}
    said = re.sub(r"\s+", "", " ".join(words.get(w, w) for w in re.findall(r"[a-z0-9+]+", answer.lower())))
    want = re.sub(r"\s+", "", shown.lower()).replace("up", "0")
    return bool(want) and want in said
