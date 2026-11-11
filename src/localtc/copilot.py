"""The copilot: works the radio for the pilot so a flight can be flown without typing.

- ``assist``: reads back every instruction and tunes COM1 whenever ATC hands you off.
  You still make your own requests and check-ins.
- ``full``: also makes every call itself: IFR clearance request, the pushback and taxi when the
  crew is ready (the captain's "request pushback" / "request taxi" on the intercom, or the beacon,
  then the taxi light, coming on), ready for departure, check-ins after each handoff, final, clear
  of the runway, going around, and any call the captain asks for.

Everything it means to say or tune is an item on the shared flight deck (``localtc.flightdeck``),
checked again the moment before it goes: the pilot's own call, the pilot tuning the radio, a new
instruction, a go-around, a phase change or a new flight drop what it no longer should say. A
check-in nobody answers is tried once more, then the captain is told.

It is pure and event-driven like the engine: feed it every input event and every engine
output with ``observe``, then ask ``due(t)`` for what to do. The driver turns a ``Say``
into a ``Transcript`` and a ``Tune`` into a sim command (live) or a changed COM1 in the
next own-ship state (offline scenarios), so it behaves the same against a recording.
It reads the engine's state but never changes it.
"""

import random
from dataclasses import dataclass, field
from typing import Literal

from localtc.atc_core.airport import gates as stands
from localtc.atc_core.airport.classes import bearing_words
from localtc.atc_core.engine import AtcEngine
from localtc.atc_core.facilities import Facility, channel_khz
from localtc.atc_core.phraseology import speech
from localtc.flightdeck import HELD, FlightDeck, Item
from localtc.sim_api import (
    AircraftSystems,
    AtcTransmission,
    BusEvent,
    OwnshipState,
    ReadbackEvaluated,
    Transcript,
)

CopilotMode = Literal["assist", "full"]
QUIET_S = 4.0  # radio silence before the copilot starts a call of its own
AFTER_PUSH_S = 45.0  # pushed back and stopped: this long (engines going) before "ready to taxi"
MAX_REPEATS = 2  # the same words twice in a row; a third time won't go better
REPEAT_WINDOW_S = 120.0  # the same words again after this long are a new call, not a repeat
CORRECTING = ("common.negative", "common.read_back", "common.confirm")  # ATC named what was wrong
# A handoff at a human pace: read back once ATC has finished, change frequency a few seconds after the readback,
# then listen on the new one before checking in. All three in six seconds sounded like a machine.
TUNE_AFTER_S = (3.0, 6.0)  # from the end of the readback to the new frequency in the radio
CHECKIN_AFTER_S = (5.0, 10.0)  # listening on the new frequency before the check-in
SPEECH_S_PER_CHAR = 0.065  # how long the copilot's words take to say
STALE_S = 90.0  # anything of the copilot's not said this long after it was meant to: dropped, not said late
LEVEL_STALE_S = 300.0  # cleared to another level this long ago and still level: checked in as level, not "climbing"
TREND_S = 30.0  # climbing or descending: the altitude over this long, not the vertical speed of the moment
UNANSWERED_S = 25.0  # a check-in with no word back from ATC this long: tried once more, then the captain is told
VFR_AWAY_NM = 8.0  # a VFR flight this far from its airport has been away: coming back in, it calls tower inbound
VFR_INBOUND_NM = 12.0  # ... from this far out
DOWNWIND_ACROSS_NM = (0.3, 2.5)  # abeam the runway, this far to the side and flying the other way: on the downwind
DOWNWIND_HEADING_DEG = 35.0
VFR_SETTLE_S = 30.0  # a VFR flight with no light switches to go by: ready to taxi this long after the start
ESTABLISHED_M = 600.0  # "contact tower when established": this close to the final's centreline is established on it
ESTABLISHED_WAIT_S = 600.0  # never established by this long after (vectored off, a visual): over to tower anyway


@dataclass(frozen=True)
class Say:
    t: float
    text: str
    kind = "say"


@dataclass(frozen=True)
class Tune:
    t: float
    mhz: float
    kind = "tune"

    @property
    def hz(self) -> int:
        return channel_khz(self.mhz) * 1000  # "120.42" is the 120.425 channel


@dataclass(frozen=True)
class Note:
    """Something the driver should log: the copilot gave up on a step."""

    t: float
    text: str
    kind = "note"


Action = Say | Tune | Note


@dataclass(order=True)
class _Queued:
    due: float
    seq: int
    kind: str = field(compare=False)  # "say", "tune", "checkin"
    text: str = field(default="", compare=False)
    facility: Facility | None = field(default=None, compare=False)
    tries: int = field(default=0, compare=False)
    # A readback: the instruction it answers, and the controller who gave it. Said only while that controller is
    # still waiting for it (a readback held up behind the pilot's own calls went out half an hour late at LAX, on
    # another frequency).
    answers: str | None = field(default=None, compare=False)
    heard_on: Facility | None = field(default=None, compare=False)
    created: float = field(default=0.0, compare=False)
    item: Item | None = field(default=None, compare=False)  # its entry on the flight deck
    once: str = field(default="", compare=False)  # the one-shot call it makes ("taxi"), made again if it's dropped


class CallWords:
    """The words of the calls a crew makes on the ground (the pushback, the taxi, the clearance, ready for departure),
    and who they go to: the radio copilot's, and the intercom copilot's when the captain asks it for one."""

    CONTROLLERS = {"pushback": "ground", "taxi": "ground", "clearance": "clearance", "departure": "tower"}

    def __init__(self, engine: AtcEngine) -> None:
        self.engine = engine

    def asked(self, call: str) -> tuple[Facility, str] | None:
        """``call``'s station and words; None where there's nobody to make it to."""
        engine = self.engine
        f = engine.facility(self.CONTROLLERS.get(call, "")) or (engine.facility("ground") if call == "clearance" else None)
        if f is None:
            return None
        if call == "pushback":
            return f, self._pushback_request(f)
        if call == "taxi":
            return f, self._taxi_request(f)
        if call == "clearance":
            return f, self._ifr_request(f)
        if call == "departure":
            return f, self._ready_for_departure(f)
        return None

    def _pushback_request(self, f: Facility) -> str:
        st = self.engine.state
        gate = st.assignments.departure_gate
        own = st.aircraft
        geo = self.engine.geometry(st.flight.origin) if st.flight.origin else None
        if gate is None and own is not None and geo is not None:
            parked = stands.parked_at(geo, own.lat, own.lon, self.engine._real_gates(geo.icao))
            gate = parked.display if parked is not None else None
        at = f", {gate}," if gate else ","
        return f"{f.station}, {self._callsign()}{at} request push and start" + self._with_atis(st.flight.origin)

    def _with_atis(self, icao: str | None) -> str:
        """", with information Charlie": the copilot listens to the ATIS first."""
        info = self.engine.current_atis(icao)
        return f", with information {speech.letter(info.letter).capitalize()}" if info is not None else ""

    def _ifr_request(self, f: Facility) -> str:
        if self._vfr():
            # A VFR flight out of Class B or a control zone asks for its VFR clearance; never "IFR to ..., ready to
            # copy" (the copilot asked St-Hubert Ground for an IFR clearance on a local VFR flight).
            return f"{f.station}, {self._callsign()}{self._vfr_way()}, request VFR departure clearance"
        return f"{f.station}, {self._callsign()}, IFR to {self._destination()}, ready to copy"

    def _taxi_request(self, f: Facility) -> str:
        """ "Ready to taxi, with information Charlie"; VFR, the way the flight is going first ("VFR to Lachute,
        westbound", "VFR local", "closed traffic"), as ground needs it for the runway."""
        way = self._vfr_way() if self._vfr() else ""
        return f"{f.station}, {self._callsign()}{way}, ready to taxi" + self._with_atis(self.engine.state.flight.origin)

    def _ready_for_departure(self, f: Facility) -> str:
        runway = self.engine.state.assignments.departure_runway
        where = f"holding short runway {runway}" if runway else "holding short"
        said = f"{f.station}, {self._callsign()}, {where}, ready for departure"
        if self._vfr():
            direction = self._vfr_direction()
            if direction == "closed-traffic":
                said += ", closed traffic"
            elif direction and direction != "straight-out":
                said += f", VFR {direction}"
        return said

    # --- VFR ----------------------------------------------------------------------------------------------

    def _vfr(self) -> bool:
        return bool(getattr(self.engine, "_vfr", False))

    def _vfr_local(self) -> bool:
        st = self.engine.state
        return not st.flight.destination or st.flight.destination == st.flight.origin

    def _vfr_direction(self) -> str | None:
        """Which way the VFR flight leaves: what the pilot said, else towards the destination ("westbound"); None for a
        local flight nobody has said anything about."""
        engine, st = self.engine, self.engine.state
        said = getattr(getattr(engine, "vfr", None), "direction", None)
        if said:
            return said
        if self._vfr_local() or not st.flight.origin:
            return None
        origin, dest = engine.geometry(st.flight.origin), engine.geometry(st.flight.destination)
        if origin is None or dest is None:
            return None
        return bearing_words(origin.airport.lat, origin.airport.lon, dest.airport.lat, dest.airport.lon) + "bound"

    def _vfr_way(self) -> str:
        """ ", VFR to Lachute, westbound", ", VFR local", ", closed traffic" """
        direction = self._vfr_direction()
        if direction == "closed-traffic":
            return ", closed traffic"
        where = "VFR local" if self._vfr_local() else f"VFR to {self._destination()}"
        return f", {where}" + (f", {direction}" if direction and direction != "straight-out" else "")

    def _vfr_needs_clearance(self) -> bool:
        """Out of Class B or a control zone, a VFR flight is cleared out (ATC's ``_vfr_departure_clearance``)."""
        engine, st = self.engine, self.engine.state
        zone = getattr(engine, "_zone", None)
        return bool(st.flight.origin) and zone is not None and zone(st.flight.origin).kind in ("B", "CTR") \
            and engine.facility("clearance") is not None

    def _departure_cleared(self) -> bool:
        """Cleared to go: IFR, its clearance read back; VFR, its VFR clearance where it needs one, else nothing to wait for."""
        if not self._vfr():
            return self._cleared("ifr")
        return self._cleared("vfr_departure") if self._vfr_needs_clearance() else True

    def _callsign(self) -> str:
        callsign = self.engine.state.flight.callsign
        return speech.callsign_display(callsign) if callsign else "unknown"

    def _destination(self) -> str:
        dest = self.engine.state.flight.destination
        geometry = self.engine.geometry(dest)
        return speech.airport_name(geometry.airport.name, dest) if geometry and dest else (dest or "destination")


class Copilot(CallWords):
    def __init__(self, engine: AtcEngine, *, mode: CopilotMode = "full", delay_s: tuple[float, float] = (2.0, 4.0),
                 seed: int = 1, deck: FlightDeck | None = None) -> None:
        self.engine = engine
        self.deck = deck or FlightDeck()  # shared with the intercom copilot (crew.pm)
        self._mode: CopilotMode = mode
        self.deck.set_radio_mode(mode, 0.0)
        self.delay_s = delay_s
        self._rng = random.Random(seed)
        self._t = 0.0
        self._start()

    def _start(self) -> None:
        """Everything that belongs to one flight, new (a new flight loaded, or the sim back after a disconnect)."""
        self._generation = self.deck.generation
        self._queue: list[_Queued] = []
        self._seq = 0
        self._answered: set[tuple[str, float]] = set()  # (instruction id, ATC transmission time) read back
        self._evaluated: dict[str, ReadbackEvaluated] = {}  # how ATC judged the last readback of each instruction
        self._done: set[str] = set()  # one-shot calls already made
        self._last_said = ""
        self._last_said_t = -1e9
        self._repeats = 0
        self._last_radio_t = -1e9
        self._pilot_spoke_last = False  # the last call on the radio was the pilot's own, not the copilot's
        self._pilot_said_t = -1e9
        self._beacon = self._taxi_light = False  # the crew's own signs they're ready: push and start, then taxi
        self._lights_known = False  # no light switches from the sim (an older recording): ready when cleared
        self._engines: int | None = None  # engines running (None: not reported)
        self._pushed_t: float | None = None  # when the pushback ended (stopped after it)
        self._checked_in: tuple[float, Facility, int] | None = None  # a check-in waiting for ATC's word, and the tries
        self._last_tuned: Facility | None = None  # the frequency before the last handoff (to go back to)
        self._assigned: tuple[int | None, float] = (None, 0.0)  # the cleared altitude, and since when
        self._joined_called: Facility | None = None  # LocalTC started in the air: the controller already called
        self._alts: list[tuple[float, float]] = []  # the last TREND_S of altitude: climbing or descending, overall
        self._when_established: tuple[Facility, float] | None = None  # "contact tower when established": who, since when
        self._vfr_away = False  # a VFR flight has been out of its airport's zone (coming back in is the inbound call)
        self._started_t = self._t  # this flight's start, as the copilot saw it

    @property
    def mode(self) -> CopilotMode:
        return self._mode

    @mode.setter
    def mode(self, value: CopilotMode) -> None:
        self._mode = value
        self.deck.set_radio_mode(value, self._t)  # what was queued under the old mode goes

    # --- inputs -------------------------------------------------------------------------------------

    def observe(self, event: BusEvent) -> None:
        self._t = max(self._t, event.t)
        self.deck.observe(event)
        if self.deck.generation != self._generation:
            self._start()
        cleared = self.engine.state.assignments.altitude_ft
        if cleared != self._assigned[0]:
            self._assigned = (cleared, event.t)
        if isinstance(event, OwnshipState):
            self._alts.append((event.t, event.alt_indicated_ft))
            while self._alts and event.t - self._alts[0][0] > TREND_S:
                self._alts.pop(0)
        if isinstance(event, AircraftSystems):
            self._beacon, self._taxi_light, self._lights_known = event.light_beacon, event.light_taxi, True
            self._engines = event.engines_running
        elif isinstance(event, ReadbackEvaluated):
            self._evaluated[event.instruction_id] = event
        elif isinstance(event, Transcript):
            self._pilot_spoke_last = event.source != "copilot"
            if event.source != "copilot" and event.radio != 0 and event.text and event.text != self._last_said:
                self._pilot_said_t = event.t  # the pilot's own call, not the copilot's
        elif isinstance(event, AtcTransmission):
            self._last_radio_t = event.t
            self._checked_in = None  # ATC spoke: the check-in was heard
            self._on_atc(event)

    def due(self, t: float) -> list[Action]:
        """Actions whose time has come; call after every event."""
        self._t = max(self._t, t)
        out: list[Action] = []
        if getattr(self.engine, "online_muted", False):
            self._queue.clear()  # COM1 on an online network's controller: the pilot talks to them, not the copilot
            return out
        self._drop_cancelled()
        if self.engine.state.pending is None and self.engine.idle:
            self._established_handoff(t)
        if not self._queue:
            self._catch_up(t)
        if not self._queue:
            self._asked(t)  # what the captain asked for on the intercom
        if not self._queue and self.mode == "full":
            self._initiate(t)
        out += self._unanswered(t)
        while self._queue and self._queue[0].due <= t:
            if not self.engine.idle or self.deck.pilot_keyed:
                break  # ATC is about to talk, or the pilot is: wait
            item = self._queue.pop(0)
            out += self._fire(item, t)
        return out

    def _drop_cancelled(self) -> None:
        """What the flight deck dropped (the pilot called, tuned, ATC spoke first ...) goes from the queue; a one-shot
        call dropped because ATC spoke first is made again when it's quiet."""
        kept = []
        for q in self._queue:
            if q.item is not None and q.item.cancelled:
                if q.once and q.item.cancelled.startswith(("ATC spoke first", "the phase")):
                    self._done.discard(q.once)
                continue
            kept.append(q)
        self._queue = kept

    def _catch_up(self, t: float) -> None:
        """Switched on with an instruction already waiting, answer it.

        The copilot normally replies to a transmission as it happens, so one flipped on halfway through
        a flight would sit silent while ATC waited for a readback it never heard.
        """
        st = self.engine.state
        pending = st.pending
        if pending is None or not self.engine.idle:
            return
        issued = st.issued.get(pending.instruction_id)
        if issued is None or not self.engine.library.get(pending.instruction_id).pilot_readback:
            return
        if any(key[0] == pending.instruction_id for key in self._answered):
            return  # already answered this one; ATC is waiting on a correction, not on silence
        self._answered.add((pending.instruction_id, pending.issued_t))
        self._push("say", t, text=self.engine.library.pilot_readback(pending.instruction_id, issued.slots),
                   answers=pending.instruction_id, heard_on=issued.facility)

    # --- reacting to ATC --------------------------------------------------------------------------------

    def _on_atc(self, tx: AtcTransmission) -> None:
        st = self.engine.state
        if tx.instruction_id == "common.say_again" and self._last_said:
            if not self._pilot_spoke_last:  # "say again" to the pilot's own call is theirs to answer
                self._push("say", tx.t, text=self._last_said, again=True)
            return
        if tx.instruction_id == "common.traffic":
            self._push("say", tx.t, text=f"Looking, {self._callsign()}")
            return
        pending = st.pending
        if tx.instruction_id == "common.how_read":
            # Asked after a handoff nobody read back: the readback is the answer (it went out along with "loud and
            # clear", both at once, over each other).
            owed = pending is not None and pending.instruction_id in st.issued                 and self.engine.library.get(pending.instruction_id).pilot_readback
            if not owed:
                self._push("say", tx.t, text=f"Loud and clear, {self._callsign()}")
        if pending is None or (pending.instruction_id, tx.t) in self._answered:
            return
        issued = st.issued.get(pending.instruction_id)
        if issued is None or not self.engine.library.get(pending.instruction_id).pilot_readback:
            return
        self._answered.add((pending.instruction_id, tx.t))
        words = self._correction(pending.instruction_id, issued.slots) if tx.instruction_id in CORRECTING else None
        start = max(tx.t, self.engine.radio_busy_until)  # once ATC has finished saying it
        readback = self._push("say", start, text=words or self.engine.library.pilot_readback(pending.instruction_id, issued.slots),
                              answers=pending.instruction_id, heard_on=issued.facility)
        handoff = st.comms.expected
        if handoff is not None and "frequency" in issued.slots and handoff.matches(float(issued.slots["frequency"])):
            if self._on_establishing(pending.instruction_id):
                # "... contact Zurich Tower when established": tower is called on the localiser, not straight after the
                # readback at 11,000 feet, 30 miles out.
                self._when_established = (handoff, tx.t)
                return
            said = readback.due + len(readback.text) * SPEECH_S_PER_CHAR
            tune = self._push("tune", tx.t, facility=handoff, after=said, pause=self._rng.uniform(*TUNE_AFTER_S))
            if self.mode == "full":
                self._push("checkin", tx.t, facility=handoff, after=tune.due, pause=self._rng.uniform(*CHECKIN_AFTER_S))

    def _on_establishing(self, instruction_id: str) -> bool:
        """The instruction's handoff is for later: "contact tower when established"."""
        try:
            words = " ".join(self.engine.library.get(instruction_id).text).lower()
        except Exception:
            return False
        return "when established" in words

    def _established_handoff(self, t: float) -> None:
        """The handoff waiting for the localiser: made once on it (or after a long while, or once landing)."""
        if self._when_established is None:
            return
        facility, since = self._when_established
        st = self.engine.state
        tuned = st.comms.tuned
        if tuned is not None and tuned.matches(facility.mhz):
            self._when_established = None  # the pilot changed over themselves: theirs to call
            return
        final = self.engine.tracker.context.final
        established = final is not None and abs(final.lateral_m) <= ESTABLISHED_M
        if not (established or t - since >= ESTABLISHED_WAIT_S or st.phase == "LANDING"):
            return
        self._when_established = None
        tune = self._push("tune", t, facility=facility, pause=self._rng.uniform(*TUNE_AFTER_S))
        if self.mode == "full":
            self._push("checkin", t, facility=facility, after=tune.due, pause=self._rng.uniform(*CHECKIN_AFTER_S))

    def _correction(self, instruction_id: str, slots: dict) -> str | None:
        """Told "negative" or "read back ...", answer with the part ATC picked out rather than saying the
        whole thing over again. Repeating words that were just rejected only gets them rejected again."""
        evaluated = self._evaluated.get(instruction_id)
        if evaluated is None:
            return None
        elements = [e for e in (*evaluated.mismatched, *evaluated.missing) if e in self.engine.library.fragments]
        if not elements:
            return None
        parts = []
        for element in dict.fromkeys(elements):
            if element in slots:
                parts.append(self.engine.library.fragment(element, slots).display.capitalize())
        return ", ".join(parts) + f", {self._callsign()}" if parts else None

    # --- calls the copilot starts (full mode) ------------------------------------------------------------

    def _initiate(self, t: float) -> None:
        engine, st = self.engine, self.engine.state
        if st.pending is not None or not engine.idle or t - self._last_radio_t < QUIET_S or st.phase is None:
            return
        tuned = st.comms.tuned
        phase = st.phase
        joined = getattr(engine, "joined_to", None)
        if joined is not None and joined is not self._joined_called and self.mode == "full":
            # LocalTC started in the air: over to the controller the flight is with now, and the check-in there.
            self._joined_called = joined
            here = tuned is not None and tuned.station == joined.station
            after = None if here else self._push("tune", t, facility=joined).due
            self._push("checkin", t, facility=joined, after=after, pause=self._rng.uniform(*CHECKIN_AFTER_S))
            return
        if phase == "PARKED" and getattr(engine, "_landed", False):
            return  # in at the gate at the end of the flight: no clearance or taxi to ask for (it called "ready to taxi")
        if phase == "PARKED" and self._vfr() and self._vfr_needs_clearance() and "vfr_departure" not in st.clearances:
            if st.flight.origin:
                self._call_once("ifr", engine.facility("clearance"), t, self._ifr_request)
        elif phase == "PARKED" and not self._vfr() and "ifr" not in st.clearances and st.flight.destination:
            if st.flight.origin:  # (not knowing where it is yet, it called the destination's Clearance for its clearance)
                self._call_once("ifr", engine.facility("clearance"), t, self._ifr_request)
        elif phase == "PARKED" and self._departure_cleared() and "taxi" not in st.clearances:
            # At a gate: push and start first, and only once the crew is ready (the beacon on, or asked for on the
            # intercom). It called "ready to taxi" straight after the clearance, at the gate, not pushed back.
            # Nothing to tell readiness by (no light switches from the sim), or pushed back already: ready.
            pushed = any(p == "PUSHBACK" for _, p in getattr(st, "phase_history", ()))
            if pushed and self._pushed_t is None:
                self._pushed_t = t
            # Pushed back: taxi once the engines are going and the crew has had a minute after the tug let go (the
            # after-start flow), not while still being pushed.
            started = self._engines is None or self._engines > 0
            ready = not self._lights_known or (pushed and started and t - (self._pushed_t or t) >= AFTER_PUSH_S)
            if self._vfr() and not (self._needs_pushback() and "pushback" not in st.clearances and not pushed):
                # No clearance to wait for first: the taxi call once the engine's going (the beacon on with it), or asked
                # for, not the moment LocalTC connects to a light aircraft with its lights left on. (Nothing to tell by:
                # after a moment to settle in.)
                if (self._lights_known and self._beacon and self._engines is not None and self._engines > 0) \
                        or (not self._lights_known and t - self._started_t >= VFR_SETTLE_S) or self.deck.take_want("taxi", t):
                    self._call_once("taxi", engine.facility("ground"), t, self._taxi_request)
            elif self._needs_pushback() and "pushback" not in st.clearances and not pushed:
                if ready or self._beacon or self.deck.take_want("pushback", t):
                    self._call_once("pushback", engine.facility("ground"), t, self._pushback_request)
            elif ready or self._taxi_light or self.deck.take_want("taxi", t) or (
                    self._beacon and ("pushback" in st.clearances or not self._needs_pushback())):
                self._call_once("taxi", engine.facility("ground"), t, self._taxi_request)
        elif (phase == "TAXI_OUT" or (phase == "PUSHBACK" and "pushback" not in st.clearances)) and self._departure_cleared() \
                and "taxi" not in st.clearances:
            # Moving already without a taxi clearance (pushed on the captain's own word): the crew is ready. Being
            # pushed with the push approved is no time to ask for taxi: after it.
            self._call_once("taxi", engine.facility("ground"), t, self._taxi_request)
        elif phase == "RUNWAY_HOLD" and not ({"takeoff", "line_up"} & st.clearances.keys()):
            if tuned is not None and tuned.controller == "ground" and engine.facility("tower") is not None:
                return  # ground hands off to tower on its own at the hold short line
            self._call_once("departure", engine.facility("tower"), t, self._ready_for_departure)
        elif self._vfr() and st.aircraft is not None and not st.aircraft.on_ground:
            self._vfr_calls(t, st.aircraft)

    def _vfr_calls(self, t: float, own: OwnshipState) -> None:
        """A VFR flight's own calls in the air, in full mode: tower inbound when it comes back to its airport (nobody hands
        a local flight back to tower), and the downwind report in the pattern."""
        engine, st = self.engine, self.engine.state
        dest = st.flight.destination or st.flight.origin
        geo = engine.geometry(dest) if dest else None
        tower = next((f for f in engine.facilities if f.airport == dest and f.controller == "tower"), None) \
            if dest else None
        if geo is None or tower is None:
            return
        miles = geo.distance_nm(own.lat, own.lon)
        if miles >= VFR_AWAY_NM:
            self._vfr_away = True
        vfr = getattr(engine, "vfr", None)
        tuned = st.comms.tuned
        on_tower = tuned is not None and tuned.matches(tower.mhz)
        runway = st.assignments.arrival_runway
        if on_tower and runway and "landing" not in st.clearances:
            # In the pattern ("enter left downwind, report midfield", or closed traffic): the downwind report, once a
            # circuit, which is what the landing clearance waits for.
            if (side := self._on_downwind(geo, own)) is not None:
                option = {"touch_and_go": ", touch and go", "option": ", for the option", "full_stop": ", full stop"}.get(
                    getattr(vfr, "option", None) or "", "")
                self._call_once(f"downwind_{getattr(vfr, 'circuits', 0)}", tower, t,
                                lambda f: f"{f.station}, {self._callsign()}, {side} downwind runway {runway}{option}")
            return
        if getattr(engine, "_vfr_in_pattern", False):
            return
        following = vfr is not None and vfr.following and vfr.identified
        if self._vfr_away and miles <= VFR_INBOUND_NM and "landing" not in st.clearances and not following \
                and not (on_tower and st.assignments.arrival_runway):  # (tower has it already: "enter left downwind")
            # Back from the local flight, or arriving without flight following: tower, from ten miles out.
            self._call_once("vfr_inbound", tower, t, lambda f: self._vfr_inbound_words(f, own))

    def _vfr_inbound_words(self, f: Facility, own: OwnshipState) -> str:
        """ "St-Hubert Tower, CPWL, 10 miles west of St-Hubert, 2,500, inbound for landing, with information Golf" """
        where = self.engine._vfr_position(own).display if hasattr(self.engine, "_vfr_position") else ""
        alt = int(round(own.alt_indicated_ft / 100.0) * 100)
        dest = self.engine.state.flight.destination or self.engine.state.flight.origin
        return f"{f.station}, {self._callsign()}, " + (f"{where}, " if where else "") + f"{alt:,}, inbound for landing" \
            + self._with_atis(dest)

    def _on_downwind(self, geo, own: OwnshipState) -> str | None:
        """ "left" or "right": abeam the landing runway on its downwind, flying the other way; None elsewhere."""
        from localtc.sim_api.geo import unit

        runway = self.engine.state.assignments.arrival_runway
        end = geo.end(runway) if runway else None
        if end is None:
            return None
        x, y = geo.xy(own.lat, own.lon)
        ux, uy = unit(end.heading_true)
        dx, dy = x - end.threshold[0], y - end.threshold[1]
        along, across = (dx * ux + dy * uy) / 1852.0, (dx * uy - dy * ux) / 1852.0
        length_nm = end.runway.runway.length_m / 1852.0
        opposite = abs((own.hdg_true - end.heading_true) % 360 - 180) <= DOWNWIND_HEADING_DEG
        if opposite and DOWNWIND_ACROSS_NM[0] <= abs(across) <= DOWNWIND_ACROSS_NM[1] and 0.0 <= along <= length_nm:
            return "right" if across > 0 else "left"
        return None

    def _asked(self, t: float) -> None:
        """Calls the captain asked for on the intercom ("request pushback", "get us taxi", "going around"): made now,
        whatever the mode (in assist too: the captain asked), each once."""
        if self.mode == "off":
            return
        engine, st = self.engine, self.engine.state
        if self.deck.take_want("clearance", t) and "ifr" not in st.clearances:
            self._done.discard("ifr")
            self._call_once("ifr", engine.facility("clearance"), t, self._ifr_request)
        elif self.deck.take_want("pushback", t) and "pushback" not in st.clearances:
            self._done.discard("pushback")
            self._call_once("pushback", engine.facility("ground"), t, self._pushback_request)
        elif self.deck.take_want("taxi", t) and "taxi" not in st.clearances:
            self._done.discard("taxi")
            self._call_once("taxi", engine.facility("ground"), t, self._taxi_request)
        elif self.deck.take_want("departure", t) and not ({"takeoff", "line_up"} & st.clearances.keys()):
            self._done.discard("departure")
            self._call_once("departure", engine.facility("tower"), t, self._ready_for_departure)
        elif self.deck.take_want("go_around", t) and st.comms.tuned is not None:
            self._push("say", t, text=f"{st.comms.tuned.station}, {self._callsign()}, going around", pause=0.5)
        else:
            for word in ("mayday", "pan"):
                if self.deck.take_want(word, t) and st.comms.tuned is not None:
                    said = "Mayday, mayday, mayday" if word == "mayday" else "Pan-pan, pan-pan, pan-pan"
                    self._push("say", t, text=f"{said}, {st.comms.tuned.station}, {self._callsign()}, "
                                              "declaring an emergency", pause=0.5)

    def _needs_pushback(self) -> bool:
        """Parked at a gate (nose in): the way out is a pushback, not a taxi."""
        st, engine = self.engine.state, self.engine
        own = st.aircraft
        geo = engine.geometry(st.flight.origin) if st.flight.origin else None
        if own is None or geo is None:
            return False
        gate = stands.parked_at(geo, own.lat, own.lon, engine._real_gates(geo.icao))
        return gate is not None and gate.word == "gate"

    def _unanswered(self, t: float) -> list[Action]:
        """A check-in nobody answered: once more after ``UNANSWERED_S``, then the captain is told (and in full mode
        the copilot goes back to the frequency it came from)."""
        if self._checked_in is None or self.mode != "full":
            return []
        when, facility, tries = self._checked_in
        tuned = self.engine.state.comms.tuned
        last_atc = self.engine.state.comms.last_atc_t
        if last_atc is not None and last_atc > when and tuned is not None and tuned.matches(facility.mhz):
            self._checked_in = None  # the controller spoke since: heard
            return []
        if t - when < UNANSWERED_S or not self.engine.idle:
            return []
        if tuned is None or not tuned.matches(facility.mhz):
            self._checked_in = None
            return []
        if tries == 1:
            self._checked_in = (t, facility, 2)
            text = self._checkin_text(facility, self.engine.state.aircraft)
            if text:
                self._push("say", t, text=text, facility=facility, pause=0.5)
            return []
        self._checked_in = None
        back = self._last_tuned
        going_back = back is not None and not back.matches(facility.mhz)
        said = (f"No answer from {facility.station} on {speech.frequency_display(facility.mhz)}"
                + (f"; I'm back on {back.station} {speech.frequency_display(back.mhz)}" if going_back else ""))
        self.deck.note("unanswered", t, said, action=f"checkin:{facility.station}")
        out: list[Action] = [Note(t, said)]
        if going_back:
            out.append(Tune(t, back.mhz))
            self.deck.expect_tune(back.mhz, t)
        return out

    def _call_once(self, key: str, facility: Facility | None, t: float, text) -> None:
        if key in self._done or facility is None:
            return
        self._done.add(key)
        tuned = self.engine.state.comms.tuned
        after = t
        if tuned is None or tuned.controller != facility.controller or not tuned.matches(facility.mhz):
            after = self._push("tune", t, facility=facility).due + 1.0
        self._push("say", t, text=text(facility), facility=facility, after=after, once=key)

    def _checkin_text(self, f: Facility, own: OwnshipState | None) -> str | None:
        st = self.engine.state
        cs = self._callsign()
        if f.controller == "clearance":
            return self._ifr_request(f)
        if f.controller == "ground" and st.phase in ("PARKED", "TAXI_OUT"):
            if st.phase == "PARKED" and ("taxi" not in st.clearances) and self._lights_known and (
                    (self._needs_pushback() and "pushback" not in st.clearances) or not (self._taxi_light or self._beacon)):
                return None  # "contact ground when ready": the crew says when (the push, then the taxi)
            return self._taxi_request(f)
        if f.controller == "tower" and st.phase in ("RUNWAY_HOLD", "TAXI_OUT"):
            return self._ready_for_departure(f)
        if f.controller == "tower" and self._vfr() and own is not None and not own.on_ground \
                and not getattr(self.engine, "_vfr_in_pattern", False) and "landing" not in st.clearances:
            return self._vfr_inbound_words(f, st.aircraft or own)
        if f.controller == "tower":
            final = self.engine.tracker.context.final
            # Report the runway being flown to, not the one expected earlier. Being on a runway's final at
            # all means lined up with it, and calling the other one leaves tower clearing a runway the
            # aircraft is pointing away from.
            # (A parallel 800 ft over, still well out, is the one cleared for: the engine sorts that out.)
            landing = self.engine.landing_runway(self.engine.state.aircraft) if self.engine.state.aircraft else None
            runway = landing or (final.end.ident if final is not None else "") or st.assignments.arrival_runway
            miles = f"{max(1, round(final.distance_nm))} mile final" if final else "inbound"
            return f"{f.station}, {cs}, {miles} runway {runway}".rstrip()
        if f.controller == "ground":
            if "taxi_in" in st.clearances or st.assignments.gate:
                return None  # already given the way in
            runway = st.assignments.arrival_runway
            return f"{f.station}, {cs}, clear of runway {runway}, taxi to parking" if runway else \
                f"{f.station}, {cs}, clear of the runway, taxi to parking"
        if own is None:
            return f"{f.station}, {cs}, with you"
        own = st.aircraft or own  # the engine's reading: the flight level up high, whatever the altimeter says
        alt = int(round(own.alt_indicated_ft / 100.0) * 100)
        assigned = st.assignments.altitude_ft
        atis = self._with_atis(st.flight.destination) if f.controller == "approach" else ""
        if getattr(self.engine, "_going_around", False):
            # Sent back from tower: going around, and nothing about the ATIS (heard it once already).
            return f"{f.station}, {cs}, going around, {alt:,}" + (f" climbing {assigned:,}" if assigned and assigned > alt + 300 else "")
        star = self.engine.cfg.star
        vs = self._trend_fpm(own.vs_fpm)
        if getattr(self.engine, "_via_floor", None) is not None and star and vs < -300:
            return f"{f.station}, {cs}, {alt:,} descending via the {star}" + atis
        if assigned and abs(assigned - alt) > 300:
            if assigned > alt and vs < -300:  # going down below an old clearance: say what it's doing
                return f"{f.station}, {cs}, {alt:,} descending" + atis
            stale = self._t - self._assigned[1] > LEVEL_STALE_S
            if stale and ((assigned > alt and vs < 300) or (assigned < alt and vs > -300)):
                # Cleared higher and still level (five hours of "35,000 climbing 37,000" at 35,000): as it is.
                return f"{f.station}, {cs}, level {alt:,}" + atis
            verb = "climbing" if assigned > alt else "descending"
            return f"{f.station}, {cs}, {alt:,} {verb} {assigned:,}" + atis
        return f"{f.station}, {cs}, level {alt:,}" + atis

    def _trend_fpm(self, now: float) -> float:
        """Feet a minute over the last TREND_S, not this instant's: "33,500 descending" was said to Milano climbing to
        FL370, the vertical speed at -1,000 for the few seconds of a dip. ``now`` without the history."""
        if len(self._alts) < 2 or self._alts[-1][0] - self._alts[0][0] < TREND_S / 2:
            return now
        (t0, a0), (t1, a1) = self._alts[0], self._alts[-1]
        return (a1 - a0) / (t1 - t0) * 60.0

    # --- queue ------------------------------------------------------------------------------------------

    def _delay(self) -> float:
        return self._rng.uniform(*self.delay_s)

    def _push(self, kind: str, t: float, *, text: str = "", facility: Facility | None = None,
              after: float | None = None, pause: float | None = None, answers: str | None = None,
              heard_on: Facility | None = None, once: str = "", again: bool = False) -> _Queued:
        """Queue an action ``pause`` seconds (by default a reaction time, or a second to turn a knob) after ``after``
        (by default ``t``, or the last thing queued). It's on the flight deck too, which may drop it before it's due."""
        self._seq += 1
        base = max(t, self._queue[-1].due if self._queue else t) if after is None else after
        if pause is None:
            pause = self._delay() if kind != "tune" else 1.0
        item = _Queued(base + pause, self._seq, kind, text, facility, answers=answers, heard_on=heard_on, created=t,
                       once=once)
        deck_kind = "tune" if kind == "tune" else "checkin" if kind == "checkin" else \
            "readback" if answers is not None or not once else "call"
        where = facility.station if facility is not None else ""
        item.item = self.deck.submit("radio", deck_kind, f"{once or answers or kind}:{where}".rstrip(":"), t, text=text,
                                     ttl=max(STALE_S, item.due - t + STALE_S), again=again)
        self._queue.append(item)
        self._queue.sort()
        return item

    def _fire(self, item: _Queued, t: float) -> list[Action]:
        st = self.engine.state
        if item.item is not None and item.kind != "say" and (why := self.deck.check(item.item, t)) is not None:
            if why in HELD:
                item.due = t + 1.0
                self._queue.append(item)
                self._queue.sort()
            return []
        if item.kind == "tune":
            assert item.facility is not None
            if st.comms.tuned is not None and not st.comms.tuned.matches(item.facility.mhz):
                self._last_tuned = st.comms.tuned
            self.deck.expect_tune(item.facility.mhz, t)
            if item.item is not None:
                self.deck.done(item.item, t)
            late = t - item.due  # tuned later than planned (the frequency busy): the check-in listens as long after it
            if late > 0:
                for q in self._queue:
                    if q.kind == "checkin" and q.facility is item.facility:
                        q.due += late
                self._queue.sort()
            return [Tune(t, item.facility.mhz)]
        text = item.text
        if item.answers is not None and (st.pending is None or st.pending.instruction_id != item.answers or (
                item.heard_on is not None and st.comms.tuned is not None and not st.comms.tuned.matches(item.heard_on.mhz))):
            return []  # answered already (by the pilot), replaced, or that controller is no longer the one listening
        if item.kind == "checkin":
            assert item.facility is not None
            if self._pilot_said_t > item.created:
                return self._skip(item, t, "the pilot called them already")  # a second call-up on top of theirs is noise
            if self._still_checked_in(item.facility.station):
                # Heard already (London answered, then a second check-in went out, wasn't answered, and the copilot
                # said "no answer" and went back to departure). Back to a controller left earlier (Boston, then New
                # York again) is a new check-in: it was skipped, and the flight sat unannounced for six hours.
                return self._skip(item, t, f"{item.facility.station} heard us already")
            text = self._checkin_text(item.facility, st.aircraft) or ""
            if item.item is not None:
                item.item.text = text
        if item.facility is not None and (st.comms.tuned is None or not st.comms.tuned.matches(item.facility.mhz)):
            # The radio isn't on the frequency yet (the tune hasn't shown up in the sim data): retry, then give up.
            if item.tries >= 2:
                # The frequency didn't change: the captain is told on the intercom (crew.pm), and nothing's said
                # on a frequency nobody there is listening to.
                self.deck.note("tune_failed", t, f"COM1 didn't change to {item.facility.station} "
                                                 f"{speech.frequency_display(item.facility.mhz)}", action=item.facility.station)
                return [Note(t, f"COM1 never reached {item.facility.station} {item.facility.mhz:.3f}; skipped: {text}")]
            item.tries += 1
            item.due = t + 2.0
            self._queue.append(item)
            self._queue.sort()
            return [Tune(t, item.facility.mhz)] if item.tries == 2 else []
        if not text:
            return []
        if item.item is not None and item.kind == "say" and (why := self.deck.check(item.item, t)) is not None:
            if why in HELD:
                item.due = t + 1.0
                self._queue.append(item)
                self._queue.sort()
            return []
        # Only words repeated straight away are a copilot stuck in a loop. The same short answer hours
        # later ("Looking, DAL42" to a second traffic call) is a new call and must not be held against it.
        again = text == self._last_said and t - self._last_said_t <= REPEAT_WINDOW_S
        self._repeats = self._repeats + 1 if again else 1
        if self._repeats > MAX_REPEATS:
            return [Note(t, f"ATC keeps rejecting this; not repeating it again: {text}")]
        self._last_said, self._last_said_t = text, t
        self._last_radio_t = t
        if item.item is not None:
            self.deck.done(item.item, t)
        if item.kind == "checkin" and item.facility is not None and self.mode == "full":
            self._checked_in = (t, item.facility, 1)
        return [Say(t, text)]

    def _skip(self, item: _Queued, t: float, why: str) -> list[Action]:
        """Not said after all: off the flight deck too (left there, it was "cancelled" hours later by a tune)."""
        if item.item is not None and not item.item.done and not item.item.cancelled:
            self.deck._drop(item.item, t, why)
        return []

    def _still_checked_in(self, station: str) -> bool:
        """Checked in with ``station`` and not left it since."""
        engine = self.engine
        if station not in getattr(engine, "_checked_in", ()):
            return False
        left = getattr(engine, "_left_at", {}).get(station, float("-inf"))
        return left <= getattr(engine, "_checkin_t", {}).get(station, float("-inf"))

    def _cleared(self, kind: str) -> bool:
        clearance = self.engine.state.clearances.get(kind)
        return clearance is not None and clearance.readback in ("correct", "none")
