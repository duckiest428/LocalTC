"""The flight deck: where the radio copilot (``localtc.copilot``) and the Pilot Monitoring (``localtc.crew``) agree on
what they say on the radio and do in the cockpit.

Each used to keep its own queue and its own idea of the flight: a check-in queued before the pilot took back a request
still went out, the copilot read back a go-around the captain had just said they weren't flying, and a step climb was
asked for while the pilot was on the radio. Now every radio call and cockpit action either of them means to make is an
``Item`` here, and the moment before it's made it's asked whether it's still right.

- **The flight** is a generation number. A new flight loaded or a reconnect starts the next one: anything queued under
  the last is dropped, never said late into the new one.
- **Who has the radios**: the radio copilot's mode. "full": the copilot; "assist": the pilot, with the copilot reading
  back; "off": the pilot alone.
- **What cancels a queued item**: the pilot's own call on the radio, the pilot tuning COM1 themselves, a new instruction
  from ATC, a go-around, a phase change, a new flight or a reconnect, the copilot's mode changing. Each one with its
  reason, in a ``CopilotEvent``.
- **Twice the same**: the same words on the radio within ``DUPLICATE_S`` aren't said again.
- **What the copilot knows** (``Memory``): the flight's state it works from, for its words and its model: the
  clearance, the frequency, the phase, what's waiting, what it did lately, the pilot's last correction and preferences.

Pure, like the rest: ``observe`` each event (each once, whichever of the two passes it on first), ``check`` an item
before acting on it, ``drain`` the events to publish.
"""

import re
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from localtc.sim_api import (
    AtcTransmission,
    ConnectionStatus,
    CopilotEvent,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    RadioTuned,
    SimLifecycle,
    Transcript,
)

KINDS = ("readback", "call", "checkin", "request", "tune", "cockpit")
RADIO_KINDS = ("readback", "call", "checkin", "request")
# Who goes first on the frequency: what ATC is waiting for, then a check-in, then a call the copilot starts, then a
# request (a step climb, a diversion). A lower one of the other copilot's waits while a higher one is queued.
HELD = ("the pilot is on the radio", "a more urgent call first")  # waiting, not dropped
PRIORITY = {"readback": 3, "checkin": 2, "call": 1, "request": 0}
DUPLICATE_S = 20.0  # the same words on the radio again within this: not said
TUNE_EXPECTED_S = 15.0  # a frequency the copilot (or the pilot through it) dialled shows up within this
STALE_S = {"readback": 60.0, "call": 120.0, "checkin": 90.0, "request": 180.0, "tune": 60.0, "cockpit": 30.0}
MEMORY_DONE = 8
TELL_CREW = ("tune_failed", "unanswered", "manual_tune")  # the radio copilot's news for the captain on the intercom  # what the copilot did lately, remembered for its words


@dataclass
class Item:
    """Something the copilot means to say on the radio or do in the cockpit."""

    id: int
    owner: str  # "radio" (localtc.copilot) or "crew" (localtc.crew)
    kind: str  # one of KINDS
    key: str  # what it is, for the record and for spotting the same thing twice ("checkin:Seattle Center")
    created: float
    generation: int
    text: str = ""
    expires: float = 0.0
    valid: Callable[[], str | None] | None = None  # None: still right; otherwise why not
    cancelled: str = ""  # why it was dropped ("" while it stands)
    done: bool = False
    again: bool = False  # the same words again on purpose (answering "say again"): not a duplicate


@dataclass(frozen=True)
class Utterance:
    """One thing said, kept as it was heard: who said it to whom, how sure speech-to-text was, the audio, when, and
    what the flight looked like at that moment."""

    id: str
    t: float
    source: str  # pilot, copilot, atc, system
    recipient: str  # intercom, com1, com2, unknown
    text: str
    confidence: float | None = None
    audio_ref: str | None = None
    snapshot: tuple[tuple[str, Any], ...] = ()  # phase, station and frequency, clearance, altitude, heading, speed ...
    act: str = ""  # what it was read as (crew.speech_acts), for the pilot's words on the intercom


@dataclass
class Memory:
    """The flight as the copilot knows it, kept up to date from the events (and by the copilot itself)."""

    phase: str | None = None
    station: str = ""  # who COM1 is tuned to
    mhz: float = 0.0
    last_atc: str = ""  # the last thing ATC said to this flight
    clearance: str = ""  # the last instruction ATC wanted read back
    correction: str = ""  # the pilot's last correction on the intercom
    preferences: dict[str, str] = field(default_factory=dict)  # "fuel calls": "only in the last hour"
    done: deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=MEMORY_DONE))
    paused_at: float | None = None
    paused_atc: list[str] = field(default_factory=list)  # what ATC said while the sim was paused
    utterances: deque[Utterance] = field(default_factory=lambda: deque(maxlen=200))  # everything said, lately
    aircraft: tuple[tuple[str, Any], ...] = ()  # the last own-ship state, for the snapshot

    def did(self, t: float, what: str) -> None:
        self.done.append((t, what))

    def lines(self, t: float) -> dict[str, str]:
        """For the copilot's model: what it's working from, as labelled lines."""
        out: dict[str, str] = {}
        if self.clearance:
            out["the last clearance"] = self.clearance
        if self.done:
            out["what I did lately"] = "; ".join(f"{what} ({_ago(t - when)})" for when, what in self.done)
        if self.correction:
            out["the captain's last correction"] = self.correction
        for k, v in self.preferences.items():
            out[f"the captain wants: {k}"] = v
        return out


def _ago(seconds: float) -> str:
    minutes = round(seconds / 60)
    return "just now" if minutes < 1 else f"{minutes} min ago"


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


class FlightDeck:
    def __init__(self) -> None:
        self.generation = 1
        self.radio_mode = "off"
        self.items: list[Item] = []
        self.memory = Memory()
        self.events: list[CopilotEvent] = []
        self.pilot_keyed = False  # the pilot's push-to-talk is down: nothing of the copilot's goes out
        self.pilot_radio_t = -1e9  # the pilot's last own call on the radio
        self._sent: deque[tuple[float, str]] = deque(maxlen=12)  # (t, words) said on the radio lately
        self._tunes: deque[tuple[float, int]] = deque(maxlen=6)  # (t, kHz) dialled by the copilot
        self._com1_khz: int | None = None
        self._connected: bool | None = None
        self._seen: deque[Any] = deque(maxlen=64)  # the last events observed (each is handled once)
        self._next = 0
        self.t = 0.0
        # Calls the captain asked the copilot to make ("pushback", "taxi", "clearance", "departure", "go_around",
        # "mayday", "pan"): the radio copilot makes them at the next free moment, if it has the radios.
        self.wants: dict[str, float] = {}
        self.for_crew: list[CopilotEvent] = []  # news the intercom copilot tells the captain (and takes)
        self._lock = threading.RLock()  # the radio copilot and the intercom copilot may run on different threads

    # --- items -------------------------------------------------------------------------------------------------

    def submit(self, owner: str, kind: str, key: str, t: float, *, text: str = "", ttl: float | None = None,
               valid: Callable[[], str | None] | None = None, again: bool = False) -> Item:
        """Queue something to say or do; it's ``check``-ed again before it's done."""
        with self._lock:
            self._next += 1
            item = Item(self._next, owner, kind, key, t, self.generation, text,
                        t + (ttl if ttl is not None else STALE_S.get(kind, 60.0)), valid, again=again)
            self.items = [i for i in self.items if not i.done and not i.cancelled] + [item]
            return item

    def queued(self, owner: str | None = None, kinds: tuple[str, ...] | None = None) -> list[Item]:
        return [i for i in self.items if not i.done and not i.cancelled and (owner is None or i.owner == owner)
                and (kinds is None or i.kind in kinds)]

    def check(self, item: Item, t: float) -> str | None:
        """Still right to do now? None if so; otherwise why not (and it's dropped, with a ``CopilotEvent``)."""
        with self._lock:
            return self._check(item, t)

    def _check(self, item: Item, t: float) -> str | None:
        why = item.cancelled
        if not why and item.generation != self.generation:
            why = "a new flight"
        if not why and t > item.expires:
            why = "too late now"
        if not why and item.kind in RADIO_KINDS and self.pilot_keyed:
            return "the pilot is on the radio"  # held, not dropped: it goes when they've finished
        if not why and item.kind in RADIO_KINDS and any(
                other.owner != item.owner and other.kind in RADIO_KINDS and PRIORITY[other.kind] > PRIORITY[item.kind]
                and other.generation == self.generation for other in self.queued()):
            return "a more urgent call first"  # held too
        if not why and item.valid is not None:
            why = item.valid() or ""
        if not why and item.kind in RADIO_KINDS and item.text and not item.again and self.duplicate(item.text, t):
            why = "just said that"
        if why:
            self._drop(item, t, why)
            return why
        return None

    def done(self, item: Item, t: float) -> None:
        item.done = True
        if item.kind in RADIO_KINDS and item.text:
            self._sent.append((t, _words(item.text)))

    def cancel(self, t: float, reason: str, *, owner: str | None = None, kinds: tuple[str, ...] | None = None,
               before: float | None = None, keep: Callable[[Item], bool] | None = None) -> list[Item]:
        with self._lock:
            return self._cancel(t, reason, owner, kinds, before, keep)

    def _cancel(self, t: float, reason: str, owner: str | None, kinds: tuple[str, ...] | None, before: float | None,
                keep: Callable[[Item], bool] | None) -> list[Item]:
        dropped = []
        for item in self.queued(owner, kinds):
            if (before is not None and item.created >= before) or (keep is not None and keep(item)):
                continue
            self._drop(item, t, reason)
            dropped.append(item)
        return dropped

    def duplicate(self, text: str, t: float) -> bool:
        words = _words(text)
        return any(w == words and t - when <= DUPLICATE_S for when, w in self._sent)

    def said_on_radio(self, text: str, t: float) -> None:
        """Words that went out on the radio by another way (the copilot's reply to "send it?")."""
        self._sent.append((t, _words(text)))

    def _drop(self, item: Item, t: float, why: str) -> None:
        item.cancelled = why
        self.note("cancelled", t, f"{item.kind} {item.key}: {why}", action=item.key)

    # --- the flight and the radios ------------------------------------------------------------------------------

    def new_flight(self, t: float, why: str) -> None:
        """A new flight (or the sim back after a disconnect): everything queued goes, the picture starts again."""
        with self._lock:
            self.cancel(t, why)
            self.generation += 1
            self.memory = Memory(preferences=self.memory.preferences)  # what the pilot likes outlives the flight
            self._sent.clear()
            self.wants.clear()
            self.note("new_flight", t, why)

    def set_radio_mode(self, mode: str, t: float) -> None:
        if mode != self.radio_mode:
            self.cancel(t, f"the copilot's radio mode is now {mode}", owner="radio")
            self.note("mode", t, f"radios: {mode}")
        self.radio_mode = mode

    def want(self, call: str, t: float) -> None:
        with self._lock:
            self.wants[call] = t
            self.note("wanted", t, call, action=call)

    def take_want(self, call: str, t: float, within_s: float = 120.0) -> bool:
        """The call was asked for lately (and is now taken: made once)."""
        with self._lock:
            when = self.wants.pop(call, None)
            return when is not None and t - when <= within_s

    def expect_tune(self, mhz: float, t: float) -> None:
        """The copilot is dialling ``mhz``: when COM1 shows it, it's not the pilot tuning."""
        self._tunes.append((t, round(mhz * 1000)))

    def note(self, kind: str, t: float, detail: str = "", *, utterance: str = "", act: str = "", action: str = "") -> None:
        event = CopilotEvent(t=t, kind=kind, detail=detail, utterance=utterance, act=act, action=action,
                             generation=self.generation)
        self.events.append(event)
        if kind in TELL_CREW:
            self.for_crew.append(event)

    def snapshot(self) -> tuple[tuple[str, Any], ...]:
        m = self.memory
        return (("flight", self.generation), ("phase", m.phase), ("station", m.station), ("mhz", m.mhz),
                ("clearance", m.clearance), ("radios", self.radio_mode), *m.aircraft)

    def said(self, t: float, source: str, recipient: str, text: str, *, confidence: float | None = None,
             audio_ref: str | None = None, act: str = "", extra: tuple[tuple[str, Any], ...] = ()) -> Utterance:
        """Keep an utterance, with the moment's snapshot; its id goes in the record of what's done about it."""
        with self._lock:
            self._next += 1
            u = Utterance(f"{source[0]}{self.generation}.{self._next}", t, source, recipient, text, confidence, audio_ref,
                          self.snapshot() + extra, act)
            self.memory.utterances.append(u)
            return u

    def drain(self) -> list[CopilotEvent]:
        with self._lock:
            out, self.events = self.events, []
            return out

    # --- what happens ---------------------------------------------------------------------------------------------

    def observe(self, ev: Any) -> None:
        """An event, from whichever of the two sees it first (the second time it's ignored)."""
        with self._lock:
            self._observe(ev)

    def _observe(self, ev: Any) -> None:
        if any(seen is ev for seen in self._seen):
            return
        self._seen.append(ev)
        t = ev.t
        self.t = max(self.t, t)
        if isinstance(ev, ConnectionStatus):
            if ev.connected and self._connected is False:
                self.new_flight(t, "the sim reconnected")
            self._connected = ev.connected
        elif isinstance(ev, SimLifecycle):
            if ev.kind == "flight_loaded":
                self.new_flight(t, "a new flight loaded")
            elif ev.kind == "paused":
                self.memory.paused_at = t if self.memory.paused_at is None else self.memory.paused_at
                self.memory.paused_atc = []
            elif ev.kind == "unpaused":
                pass  # the copilot's summary reads paused_at, then clears it
        elif isinstance(ev, PttPressed) and ev.radio != 0:
            self.pilot_keyed = True
        elif isinstance(ev, PttReleased) and ev.radio != 0:
            self.pilot_keyed = False
        if isinstance(ev, Transcript) and ev.radio != 0 and ev.text:
            self.said(t, "copilot" if ev.source == "copilot" else "pilot", f"com{ev.radio}", ev.text,
                      confidence=ev.confidence, audio_ref=ev.audio_ref)
        elif isinstance(ev, AtcTransmission):
            self.said(t, "atc", "com1", ev.text, audio_ref=ev.audio_ref)
        if isinstance(ev, Transcript) and ev.radio != 0 and ev.text and ev.source != "copilot" \
                and not any(w == _words(ev.text) and t - when <= 5.0 for when, w in self._sent):  # the copilot's own
            self.pilot_radio_t = t
            # The pilot made a call: theirs replaces whatever the copilot was about to start (a readback stands
            # while ATC still wants it: its own check sees to that).
            self.cancel(t, "the pilot called", kinds=("call", "checkin", "request"), before=t)
        elif isinstance(ev, AtcTransmission):
            self.memory.last_atc = f"{ev.station}: {ev.text}"
            if ev.instruction_id and not ev.instruction_id.startswith("common."):
                self.memory.clearance = f"{ev.station}: {ev.text}"
            if self.memory.paused_at is not None:
                self.memory.paused_atc.append(f"{ev.station}: {ev.text}")
            iid = ev.instruction_id or ""
            if "go_around" in iid:
                self.cancel(t, "a go-around", kinds=("call", "checkin", "request", "tune"), before=t)
            elif not iid.startswith(("common.roger", "common.readback_correct", "common.traffic", "common.say_again")):
                # A new instruction: anything the copilot was about to ask or start waits for the next quiet moment.
                self.cancel(t, "ATC spoke first", kinds=("call", "request"), before=t)
        elif isinstance(ev, PhaseChanged):
            self.memory.phase = ev.phase
            self.cancel(t, f"the phase is now {ev.phase.lower().replace('_', ' ')}", kinds=("call", "request"), before=t)
        elif isinstance(ev, RadioTuned):
            self.memory.station, self.memory.mhz = ev.station or "", ev.frequency_mhz
        elif isinstance(ev, OwnshipState):
            self.memory.aircraft = (("altitude", round(ev.alt_indicated_ft)), ("heading", round(ev.hdg_mag)),
                                    ("ias", round(ev.ias_kt)), ("vs", round(ev.vs_fpm)), ("on_ground", ev.on_ground),
                                    ("com1", round(ev.com1_mhz, 3)))
            khz = round(ev.com1_mhz * 1000)
            if self._com1_khz is not None and khz != self._com1_khz:
                ours = any(abs(k - khz) <= 5 and t - when <= TUNE_EXPECTED_S for when, k in self._tunes)
                if not ours:
                    self.cancel(t, f"the pilot tuned {ev.com1_mhz:.3f}", kinds=("call", "checkin", "request", "tune"),
                                before=t)
                    self.note("manual_tune", t, f"COM1 {ev.com1_mhz:.3f}")
            self._com1_khz = khz
