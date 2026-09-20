"""The copilot: works the radio for the pilot so a flight can be flown without typing.

- ``assist``: reads back every instruction and tunes COM1 whenever ATC hands you off.
  You still make your own requests and check-ins.
- ``full``: also makes every call itself: IFR clearance request, taxi, ready for
  departure, check-ins after each handoff, final, clear of the runway.

It is pure and event-driven like the engine: feed it every input event and every engine
output with ``observe``, then ask ``due(t)`` for what to do. The driver turns a ``Say``
into a ``Transcript`` and a ``Tune`` into a sim command (live) or a changed COM1 in the
next own-ship state (offline scenarios), so it behaves the same against a recording.
It reads the engine's state but never changes it.
"""

import random
from dataclasses import dataclass, field
from typing import Literal

from localtc.atc_core.engine import AtcEngine
from localtc.atc_core.facilities import Facility, channel_khz
from localtc.atc_core.phraseology import speech
from localtc.sim_api import AtcTransmission, BusEvent, OwnshipState, ReadbackEvaluated

CopilotMode = Literal["assist", "full"]
QUIET_S = 4.0  # radio silence before the copilot starts a call of its own
MAX_REPEATS = 2  # the same words twice in a row; a third time won't go better
REPEAT_WINDOW_S = 120.0  # the same words again after this long are a new call, not a repeat
CORRECTING = ("common.negative", "common.read_back", "common.confirm")  # ATC named what was wrong


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


class Copilot:
    def __init__(self, engine: AtcEngine, *, mode: CopilotMode = "full", delay_s: tuple[float, float] = (2.0, 4.0),
                 seed: int = 1) -> None:
        self.engine = engine
        self.mode = mode
        self.delay_s = delay_s
        self._rng = random.Random(seed)
        self._queue: list[_Queued] = []
        self._seq = 0
        self._answered: set[tuple[str, float]] = set()  # (instruction id, ATC transmission time) read back
        self._evaluated: dict[str, ReadbackEvaluated] = {}  # how ATC judged the last readback of each instruction
        self._done: set[str] = set()  # one-shot calls already made
        self._last_said = ""
        self._last_said_t = -1e9
        self._repeats = 0
        self._last_radio_t = -1e9
        self._t = 0.0

    # --- inputs -------------------------------------------------------------------------------------

    def observe(self, event: BusEvent) -> None:
        self._t = max(self._t, event.t)
        if isinstance(event, ReadbackEvaluated):
            self._evaluated[event.instruction_id] = event
        elif isinstance(event, AtcTransmission):
            self._last_radio_t = event.t
            self._on_atc(event)

    def due(self, t: float) -> list[Action]:
        """Actions whose time has come; call after every event."""
        self._t = max(self._t, t)
        out: list[Action] = []
        if not self._queue and self.mode == "full":
            self._initiate(t)
        while self._queue and self._queue[0].due <= t:
            if not self.engine.idle:
                break  # ATC is about to talk; wait
            item = self._queue.pop(0)
            out += self._fire(item, t)
        return out

    # --- reacting to ATC --------------------------------------------------------------------------------

    def _on_atc(self, tx: AtcTransmission) -> None:
        st = self.engine.state
        if tx.instruction_id == "common.say_again" and self._last_said:
            self._push("say", tx.t, text=self._last_said)
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
        readback = self._push("say", tx.t, text=words or self.engine.library.pilot_readback(pending.instruction_id, issued.slots))
        handoff = st.comms.expected
        if handoff is not None and "frequency" in issued.slots and handoff.matches(float(issued.slots["frequency"])):
            tune = self._push("tune", tx.t, facility=handoff, after=readback.due + 1.0)
            if self.mode == "full":
                self._push("checkin", tx.t, facility=handoff, after=tune.due + self._delay())

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
            self._call_once("taxi", engine.facility("ground"), t, lambda f: f"{f.station}, {self._callsign()}, ready to taxi"
                            + self._with_atis(st.flight.origin))
        elif phase == "RUNWAY_HOLD" and not ({"takeoff", "line_up"} & st.clearances.keys()):
            if tuned is not None and tuned.controller == "ground" and engine.facility("tower") is not None:
                return  # ground hands off to tower on its own at the hold short line
            self._call_once("departure", engine.facility("tower"), t, self._ready_for_departure)

    def _call_once(self, key: str, facility: Facility | None, t: float, text) -> None:
        if key in self._done or facility is None:
            return
        self._done.add(key)
        tuned = self.engine.state.comms.tuned
        after = t
        if tuned is None or tuned.controller != facility.controller or not tuned.matches(facility.mhz):
            after = self._push("tune", t, facility=facility).due + 1.0
        self._push("say", t, text=text(facility), facility=facility, after=after)

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
            return f"{f.station}, {cs}, ready to taxi" + self._with_atis(st.flight.origin)
        if f.controller == "tower" and st.phase in ("RUNWAY_HOLD", "TAXI_OUT"):
            return self._ready_for_departure(f)
        if f.controller == "tower":
            final = self.engine.tracker.context.final
            # Report the runway being flown to, not the one expected earlier. Being on a runway's final at
            # all means lined up with it, and calling the other one leaves tower clearing a runway the
            # aircraft is pointing away from.
            runway = (final.end.ident if final is not None else "") or st.assignments.arrival_runway
            miles = f"{max(1, round(final.distance_nm))} mile final" if final else "inbound"
            return f"{f.station}, {cs}, {miles} runway {runway}".rstrip()
        if f.controller == "ground":
            runway = st.assignments.arrival_runway
            return f"{f.station}, {cs}, clear of runway {runway}, taxi to parking" if runway else \
                f"{f.station}, {cs}, clear of the runway, taxi to parking"
        if own is None:
            return f"{f.station}, {cs}, with you"
        alt = int(round(own.alt_indicated_ft / 100.0) * 100)
        assigned = st.assignments.altitude_ft
        atis = self._with_atis(st.flight.destination) if f.controller == "approach" else ""
        if assigned and abs(assigned - alt) > 300:
            verb = "climbing" if assigned > alt else "descending"
            return f"{f.station}, {cs}, {alt:,} {verb} {assigned:,}" + atis
        return f"{f.station}, {cs}, level {alt:,}" + atis

    # --- queue ------------------------------------------------------------------------------------------

    def _delay(self) -> float:
        return self._rng.uniform(*self.delay_s)

    def _push(self, kind: str, t: float, *, text: str = "", facility: Facility | None = None,
              after: float | None = None) -> _Queued:
        self._seq += 1
        base = max(t, self._queue[-1].due if self._queue else t) if after is None else after
        item = _Queued(base + (self._delay() if kind != "tune" else 1.0), self._seq, kind, text, facility)
        self._queue.append(item)
        self._queue.sort()
        return item

    def _fire(self, item: _Queued, t: float) -> list[Action]:
        st = self.engine.state
        if item.kind == "tune":
            assert item.facility is not None
            return [Tune(t, item.facility.mhz)]
        text = item.text
        if item.kind == "checkin":
            assert item.facility is not None
            text = self._checkin_text(item.facility, st.aircraft) or ""
        if item.facility is not None and (st.comms.tuned is None or not st.comms.tuned.matches(item.facility.mhz)):
            # The radio isn't on the frequency yet (the tune hasn't shown up in the sim data): retry, then give up.
            if item.tries >= 2:
                return [Note(t, f"COM1 never reached {item.facility.station} {item.facility.mhz:.3f}; skipped: {text}")]
            item.tries += 1
            item.due = t + 2.0
            self._queue.append(item)
            self._queue.sort()
            return [Tune(t, item.facility.mhz)] if item.tries == 2 else []
        if not text:
            return []
        # Only words repeated straight away are a copilot stuck in a loop. The same short answer hours
        # later ("Looking, DAL42" to a second traffic call) is a new call and must not be held against it.
        again = text == self._last_said and t - self._last_said_t <= REPEAT_WINDOW_S
        self._repeats = self._repeats + 1 if again else 1
        if self._repeats > MAX_REPEATS:
            return [Note(t, f"ATC keeps rejecting this; not repeating it again: {text}")]
        self._last_said, self._last_said_t = text, t
        self._last_radio_t = t
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
