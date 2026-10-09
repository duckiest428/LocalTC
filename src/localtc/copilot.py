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
from localtc.atc_core.engine import AtcEngine
from localtc.atc_core.facilities import Facility, channel_khz
from localtc.atc_core.phraseology import speech
from localtc.flightdeck import FlightDeck, Item
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
UNANSWERED_S = 25.0  # a check-in with no word back from ATC this long: tried once more, then the captain is told


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


class Copilot:
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
        self._checked_in: tuple[float, Facility, int] | None = None  # a check-in waiting for ATC's word, and the tries
        self._last_tuned: Facility | None = None  # the frequency before the last handoff (to go back to)
        self._assigned: tuple[int | None, float] = (None, 0.0)  # the cleared altitude, and since when

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
        if isinstance(event, AircraftSystems):
            self._beacon, self._taxi_light, self._lights_known = event.light_beacon, event.light_taxi, True
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
        self._drop_cancelled()
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
        if tx.instruction_id == "common.how_read":
            self._push("say", tx.t, text=f"Loud and clear, {self._callsign()}")
        pending = st.pending
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
            said = readback.due + len(readback.text) * SPEECH_S_PER_CHAR
            tune = self._push("tune", tx.t, facility=handoff, after=said, pause=self._rng.uniform(*TUNE_AFTER_S))
            if self.mode == "full":
                self._push("checkin", tx.t, facility=handoff, after=tune.due, pause=self._rng.uniform(*CHECKIN_AFTER_S))

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
        if phase == "PARKED" and "ifr" not in st.clearances and st.flight.destination:
            self._call_once("ifr", engine.facility("clearance"), t, self._ifr_request)
        elif phase == "PARKED" and self._cleared("ifr") and "taxi" not in st.clearances:
            # At a gate: push and start first, and only once the crew is ready (the beacon on, or asked for on the
            # intercom). It called "ready to taxi" straight after the clearance, at the gate, not pushed back.
            # Nothing to tell readiness by (no light switches from the sim), or pushed back already: ready.
            pushed = any(p == "PUSHBACK" for _, p in getattr(st, "phase_history", ()))
            ready = not self._lights_known or pushed
            if self._needs_pushback() and "pushback" not in st.clearances and not pushed:
                if ready or self._beacon or self.deck.take_want("pushback", t):
                    self._call_once("pushback", engine.facility("ground"), t, self._pushback_request)
            elif ready or self._taxi_light or self.deck.take_want("taxi", t) or (
                    self._beacon and ("pushback" in st.clearances or not self._needs_pushback())):
                self._call_once("taxi", engine.facility("ground"), t, lambda f: f"{f.station}, {self._callsign()}, ready to taxi"
                                + self._with_atis(st.flight.origin))
        elif phase in ("PUSHBACK", "TAXI_OUT") and self._cleared("ifr") and "taxi" not in st.clearances:
            # Moving already without a taxi clearance (pushed on the captain's own word): the crew is ready.
            self._call_once("taxi", engine.facility("ground"), t, lambda f: f"{f.station}, {self._callsign()}, ready to taxi"
                            + self._with_atis(st.flight.origin))
        elif phase == "RUNWAY_HOLD" and not ({"takeoff", "line_up"} & st.clearances.keys()):
            if tuned is not None and tuned.controller == "ground" and engine.facility("tower") is not None:
                return  # ground hands off to tower on its own at the hold short line
            self._call_once("departure", engine.facility("tower"), t, self._ready_for_departure)

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
            self._call_once("taxi", engine.facility("ground"), t, lambda f: f"{f.station}, {self._callsign()}, ready to taxi"
                            + self._with_atis(st.flight.origin))
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

    def _unanswered(self, t: float) -> list[Action]:
        """A check-in nobody answered: once more after ``UNANSWERED_S``, then the captain is told (and in full mode
        the copilot goes back to the frequency it came from)."""
        if self._checked_in is None or self.mode != "full":
            return []
        when, facility, tries = self._checked_in
        tuned = self.engine.state.comms.tuned
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

    def _with_atis(self, icao: str | None) -> str:
        """", with information Charlie": the copilot listens to the ATIS first."""
        info = self.engine.current_atis(icao)
        return f", with information {speech.letter(info.letter).capitalize()}" if info is not None else ""

    def _ifr_request(self, f: Facility) -> str:
        return f"{f.station}, {self._callsign()}, IFR to {self._destination()}, ready to copy"

    def _ready_for_departure(self, f: Facility) -> str:
        runway = self.engine.state.assignments.departure_runway
        where = f"holding short runway {runway}" if runway else "holding short"
        return f"{f.station}, {self._callsign()}, {where}, ready for departure"

    def _checkin_text(self, f: Facility, own: OwnshipState | None) -> str | None:
        st = self.engine.state
        cs = self._callsign()
        if f.controller == "clearance":
            return self._ifr_request(f)
        if f.controller == "ground" and st.phase in ("PARKED", "TAXI_OUT"):
            if st.phase == "PARKED" and ("taxi" not in st.clearances) and self._lights_known and (
                    (self._needs_pushback() and "pushback" not in st.clearances) or not (self._taxi_light or self._beacon)):
                return None  # "contact ground when ready": the crew says when (the push, then the taxi)
            return f"{f.station}, {cs}, ready to taxi" + self._with_atis(st.flight.origin)
        if f.controller == "tower" and st.phase in ("RUNWAY_HOLD", "TAXI_OUT"):
            return self._ready_for_departure(f)
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
        if getattr(self.engine, "_via_floor", None) is not None and star and own.vs_fpm < -300:
            return f"{f.station}, {cs}, {alt:,} descending via the {star}" + atis
        if assigned and abs(assigned - alt) > 300:
            if assigned > alt and own.vs_fpm < -300:  # going down below an old clearance: say what it's doing
                return f"{f.station}, {cs}, {alt:,} descending" + atis
            stale = self._t - self._assigned[1] > LEVEL_STALE_S
            if stale and ((assigned > alt and own.vs_fpm < 300) or (assigned < alt and own.vs_fpm > -300)):
                # Cleared higher and still level (five hours of "35,000 climbing 37,000" at 35,000): as it is.
                return f"{f.station}, {cs}, level {alt:,}" + atis
            verb = "climbing" if assigned > alt else "descending"
            return f"{f.station}, {cs}, {alt:,} {verb} {assigned:,}" + atis
        return f"{f.station}, {cs}, level {alt:,}" + atis

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
            if why == "the pilot is on the radio":
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
            return [Tune(t, item.facility.mhz)]
        text = item.text
        if item.answers is not None and (st.pending is None or st.pending.instruction_id != item.answers or (
                item.heard_on is not None and st.comms.tuned is not None and not st.comms.tuned.matches(item.heard_on.mhz))):
            return []  # answered already (by the pilot), replaced, or that controller is no longer the one listening
        if item.kind == "checkin":
            assert item.facility is not None
            if self._pilot_said_t > item.created:
                return []  # the pilot called them already: a second call-up on top of theirs is noise
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
            if why == "the pilot is on the radio":
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

    def _cleared(self, kind: str) -> bool:
        clearance = self.engine.state.clearances.get(kind)
        return clearance is not None and clearance.readback in ("correct", "none")

    def _callsign(self) -> str:
        callsign = self.engine.state.flight.callsign
        return speech.callsign_display(callsign) if callsign else "unknown"

    def _destination(self) -> str:
        dest = self.engine.state.flight.destination
        geometry = self.engine.geometry(dest)
        return speech.airport_name(geometry.airport.name, dest) if geometry and dest else (dest or "destination")
