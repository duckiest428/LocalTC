"""The ground crew: told what the pilot says, what the aircraft is doing and what the services are doing; says what to send
and what to say. Pure, as the ATC is: ``service`` does the waiting and the sending.

- One service asked for at a time, each once GSX shows it took (asked for quickly in a row, it drops all but the last).
- The menu is read afresh for every pick (``menu``). GSX's own questions are answered when they're ours to answer: the
  operator its choice, the direction the pilot gave, a good start with the engines running. Anything else is read out, and
  the pilot says which.
- The push waits for ATC's clearance, and for the aircraft: stopped, brake set, engines off. Its words are the standard ones
  (``lines.FIXED``).
- Boarding and deboarding are the cabin crew's: the ramp passes them on (``RampLink.relay``).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from localtc.ground import grammar as g
from localtc.ground.api import GroundState, Menu, Service
from localtc.ground import words as foreign
from localtc.ground.lines import SPOKEN, Words, spoken_list
from localtc.ground.menu import Close, MenuRun, Open, Pick, Want, find, has_words, is_question, norm, real_entries, row_of
from localtc.sim_api import OwnshipState, RampSpeech
from localtc.sim_api.ramp import RAMP_MHZ, on_ramp

log = logging.getLogger(__name__)

SAFETY, ROUTINE, CHATTER = 3, 2, 1  # as the copilot's calls (crew.monitor)
VERBOSITY = {"quiet": SAFETY, "standard": ROUTINE, "chatty": CHATTER}
STERILE_FT = 10000.0
LISTENING_S = 20.0  # after the crew's spoken, an answer needs no "ramp, ..."
SEND_S = 25.0  # a service called, and this long for GSX to show it took
OURS_S = 120.0  # GSX's questions this soon after calling a service are about it
CALL_GAP_S = 4.0  # between the crew's own calls
CALL_WAIT_S = 45.0  # a call that couldn't be said this long (ATC talking, the pilot keyed): dropped
DOOR_AGAIN_S = 120.0
BRAKE_AGAIN_S = 20.0
FULL_SERVICE = ("GPU", "Cleaning", "Catering", "Refueling", "Water", "Lavatory")
RELAYED = ("Boarding", "Deboarding")
OPERATOR_CHOICE = "gsx choice"
# GSX's questions with an answer that's always right here, recorded at Zurich. The tug's only connected when the push is
# called (and ATC's cleared it); the pilots and crew are on board already, flying it.
SETTLED = (("attach pushback tug", ("no",)), ("deboard crew", ("nobody",)), ("board crew", ("nobody",)))
GOOD_START_ROW = ("good engine start", "good start", "confirm")
DIRECTIONS = {g.NOSE_LEFT: ("nose left",), g.NOSE_RIGHT: ("nose right",), g.STRAIGHT: ("straight",)}
SAID_DIRECTION = {g.NOSE_LEFT: "nose left, tail right", g.NOSE_RIGHT: "nose right, tail left", g.STRAIGHT: "straight back"}
SIDE = {g.NOSE_LEFT: "right", g.NOSE_RIGHT: "left"}  # the headset's side: the side the tail went, clear of the engines
# What GSX says that the pilot needs to know ("Fuel Truck is in position" and the bills aren't).
PASSED_ON = ("cannot", "can't", "you already", "jetway not close", "no jetway", "not enough")
FUEL_ROW = re.compile(r"(\d{1,3})\s*%")
WHY = {"never_opened": "its menu never came up.", "missing": "that isn't on its menu right now.", "disabled": "it's greyed out on its menu.",
       "no_change": "it didn't react.", "ambiguous": "its menu has more than one like that."}


@dataclass
class RampLink:
    """What the ground crew needs from the rest of LocalTC (``app`` wires it; a test gives its own)."""

    pushback_cleared: Callable[[], bool] = lambda: False  # ATC has cleared the push
    atc_working: Callable[[], bool] = lambda: False  # a controller here who'd clear it (none: no waiting)
    gate: Callable[[], str] = lambda: ""  # the gate ATC gave
    request_gate: Callable[[str], tuple[bool, str]] = lambda label: (False, "there's no ATC to ask.")
    locale: Callable[[str], str] = lambda airport: ""  # the crew's English, by the airport
    # The cabin crew's or the copilot's turn on boarding and deboarding: ("boarding_start", ...) -> what they say ([]: they'll
    # say it in their own time), or None when there's nobody and the ramp says it itself.
    relay: Callable[[str, float], list[Any] | None] = lambda what, t: None


@dataclass
class Push:
    direction: str = ""
    stage: str = "armed"  # armed, called, asked, connecting, pushing, set_brake, start, done
    t: float = 0.0
    told: set[str] = field(default_factory=set)
    brake_said_t: float = 0.0


class RampCrew:
    def __init__(self, link: RampLink | None = None, *, verbosity: str = "standard", wait_for_clearance: bool = True,
                 ramp_mhz: float = RAMP_MHZ, seed: int = 0, language: str = "") -> None:
        self.link = link or RampLink()
        self.level = VERBOSITY.get(verbosity, ROUTINE)
        self.wait_for_clearance = wait_for_clearance
        self.ramp_mhz = ramp_mhz
        self.language = language  # [crew] language: what the pilot talks in ("" English)
        self.words = Words(seed, language)
        self.seed = seed
        self.own: OwnshipState | None = None
        self.state = GroundState()
        self.keyed = False
        self.atc_until = 0.0
        self._t = 0.0
        self._spoke_t = -1e9
        self._queue: list[str] = []
        self._sending: tuple[str, float] | None = None
        self._ours: dict[str, float] = {}  # the services the pilot asked for, and when
        self._run: MenuRun | None = None
        self._run_after: tuple[str, str] | None = None  # what it was for: (kind, service)
        self._push: Push | None = None
        self._offer: tuple[str, Any] | None = None  # asked the pilot: ("push", direction), ("finish", service)
        self._asking: Menu | None = None  # a question of GSX's read out, waiting for the pilot
        self._seen_question: Menu | None = None
        self._cancelled: set[str] = set()
        self._calls: list[tuple[float, int, str, dict]] = []
        self._call_t = -1e9
        self._door_t: dict[str, float] = {}
        self._said_differs = False
        self._gate_told = False
        self._fuel_wanted = 0  # the fuel asked for, in percent, for when GSX asks
        self._finished: list[str] = []  # done this turnaround (GSX shows "completed" for only a few seconds)
        self._bridge: dict[str, bool] = {}  # the jetway and stairs: on the aircraft or not, as GSX last said (one call moves them either way)

    # ---- who's listening -------------------------------------------------------------------------------------------

    @property
    def tuned(self) -> bool:
        return self.own is not None and on_ramp(self.own.com1_mhz, self.ramp_mhz)

    @property
    def parked(self) -> bool:
        return self.own is not None and self.own.on_ground and self.own.gs_kt < 1.0

    def _speech(self, key: str, t: float, **values: str) -> RampSpeech:
        self._spoke_t = t
        return RampSpeech(t=t, text=self.words.line(key, **values), speaker="Ramp", channel="radio" if self.tuned else "interphone",
                          sex="M" if self.seed % 2 else "F", locale=self.link.locale(self.state.airport),
                          language=self.language if self.words.in_language(key) else "")

    def read(self, text: str) -> str:
        """The pilot's words as the grammar reads them."""
        return foreign.as_english(text, self.language)

    def _name(self, service: str) -> str:
        return foreign.NAMES.get(self.language, {}).get(service) or SPOKEN.get(service, service.lower())

    def _names(self, names: list[str]) -> str:
        joined = spoken_list(names)
        word = foreign.AND.get(self.language)
        return joined.replace(" and ", f" {word} ") if word else joined

    def _said_direction(self, direction: str) -> str:
        terms = foreign.TERMS.get(self.language)
        return terms[(g.NOSE_LEFT, g.NOSE_RIGHT, g.STRAIGHT).index(direction)] if terms else SAID_DIRECTION[direction]

    def _term(self, index: int, english: str) -> str:
        terms = foreign.TERMS.get(self.language)
        return terms[index] if terms else english

    def _why(self, failed: str) -> str:
        return foreign.WHY.get(self.language, {}).get(failed) or WHY.get(failed, "it didn't work.")

    def claims(self, text: str, t: float, to: str = "") -> bool:
        """Whether words on the intercom are for the ground crew: addressed to them, or the answer to what they just said.
        "Ground" alone stays ATC's: "ask ground for the push" is for the copilot."""
        if to == "cabin" or not text.strip():
            return False
        said = self.read(text)
        if g.is_addressed(said):
            return True
        return t - self._spoke_t <= LISTENING_S and (self._offer is not None or self._asking is not None) and bool(g.parse(said))

    # ---- the pilot -------------------------------------------------------------------------------------------------

    def heard(self, text: str, t: float) -> list[Any]:
        """The pilot's words (on the ramp's frequency, or claimed on the intercom)."""
        self._t = t
        asks = g.parse(self.read(text))
        if not asks:
            if (said := self._by_label(text, t)) is not None:
                return said
            return [self._speech("say_again", t)]
        out: list[Any] = []
        for ask in asks:
            out += self.do(ask, t)
        return out

    def do(self, ask: g.Ask, t: float) -> list[Any]:  # noqa: C901 - one reading per kind of call
        a = ask.action
        if a == g.YES or a == g.NO:
            return self._answer(a == g.YES, t)
        if a == g.OPTION:
            return self._option(ask.option, t)
        if a == g.BRAKES_SET or a == g.BRAKES_RELEASED or a == g.GOOD_START:
            return self._confirmation(a, t)
        if a == g.STATUS:
            return self._status(ask.services, t)
        if a == g.GATE:
            return self._gate(ask.gate, t)
        if not self.parked and a in (g.REQUEST, g.FULL, g.PUSH):
            return [self._speech("stopped_first" if a == g.PUSH else "on_stand_first", t)]
        if a == g.LEVEL:
            return self._fuel_level(ask.option, t)
        if a == g.REQUEST:
            if "Refueling" in ask.services and ask.option:
                self._fuel_wanted = ask.option
            return self._request(ask.services, t)
        if a == g.FULL:
            return self._request(FULL_SERVICE, t)
        if a == g.PUSH:
            return self._ask_push(ask.direction, t)
        if a == g.STOP_PUSH:
            return self._stop_push(t)
        if a == g.CANCEL:
            return [line for s in self._named(ask.services) for line in self._cancel(s, t)]
        if a == g.FINISH:
            return [line for s in self._named(ask.services) for line in self._finish(s, t)]
        if a == g.HURRY:
            return [line for s in self._named(ask.services) for line in self._hurry(s, t)]
        if a == g.OFF:
            return [line for s in ask.services for line in self._off(s, t)]
        return [self._speech("say_again", t)]

    def _named(self, named: tuple[str, ...]) -> tuple[str, ...]:
        """The services said, or with none said ("cancel that"), the last one asked for still running."""
        if named:
            return named
        running = [s for s in self._ours if (svc := self.state.services.get(s)) is not None and svc.active]
        return (running[-1],) if running else ()

    def _service(self, service: str) -> Service | None:
        return self.state.services.get(service)

    def _request(self, services: tuple[str, ...], t: float) -> list[Any]:
        out: list[Any] = []
        coming: list[str] = []
        for service in services:
            if service == "Departure":
                out += self._ask_push("", t)
            elif (no := self._refused(service, t)) is not None:
                out.append(no)
            elif service in RELAYED:
                out += self._relayed_request(service, t)
                coming += []
            else:
                svc = self._service(service)
                if svc is None:
                    out.append(self._speech("no_gsx" if not self.state.services else "not_here", t, name=self._name(service)))
                elif svc.active or service in self._queue or self._bridge.get(service) is True:
                    out.append(self._speech("already", t, name=self._name(service)))
                elif svc.state == "unavailable" or (not svc.can_trigger and svc.state != "completed"):
                    out.append(self._speech("not_here", t, name=self._name(service)))
                else:
                    self._queue.append(service)
                    coming.append(self._name(service))
        if coming:
            out.insert(0, self._speech("ack", t, list=self._names(coming)))
        return out

    def _refused(self, service: str, t: float) -> RampSpeech | None:
        own = self.own
        if own is None:
            return None
        if service == "Refueling" and own.engine_running:
            return self._speech("fuel_engines", t)
        if service in ("OperateJetways", "OperateStairs") and not own.parking_brake:
            return self._speech("brake_for_bridge", t)
        return None

    def _relayed_request(self, service: str, t: float) -> list[Any]:
        """Boarding and deboarding: the cabin crew's. The ramp starts GSX's side and they tell the captain about it."""
        svc = self._service(service)
        if svc is None:
            return [self._speech("no_gsx" if not self.state.services else "not_here", t, name=self._name(service))]
        if svc.active or service in self._queue:
            return [self._speech("already", t, name=self._name(service))]
        self._queue.append(service)
        said = self.link.relay("boarding_start" if service == "Boarding" else "deboarding_start", t)
        if said is not None:
            return [self._speech("relay_boarding", t), *said]
        return [self._speech("ack", t, list=self._name(service))]

    def called_by_cabin(self, service: str, t: float) -> bool:
        """The cabin crew told to start boarding or deboarding: called without a word from the ramp. False: it can't be."""
        svc = self._service(service)
        if not self.parked or svc is None or svc.state == "unavailable":
            return False
        if not svc.active and service not in self._queue:
            self._queue.append(service)
        return True

    def ask_for(self, service: str, t: float) -> list[Any]:
        """Someone else asking (the cabin crew told "start boarding", the phone): the same checks as the pilot's voice."""
        self._t = t
        if service == "Departure":
            return self._ask_push("", t)
        return self._request((service,), t) if self.parked else [self._speech("on_stand_first", t)]

    def _status(self, named: tuple[str, ...], t: float) -> list[Any]:
        services = self.state.services
        if named:
            parts = []
            for s in named:
                svc = services.get(s)
                parts.append(self._said_status(svc, s) if svc is not None else f"{self._name(s)}: {self._term(5, 'not called')}.")
            return [self._speech("status", t, report=" ".join(parts))]
        active = [s for s in services.values() if s.active and not s.waiting]
        waiting = [s for s in services.values() if s.active and s.waiting]
        done = [s for s in self._finished if s in services and not services[s].active]
        if not active and not waiting and not done and not self._queue:
            return [self._speech("status_none", t)]
        parts = [self._said_status(s, s.id) for s in active + waiting]
        if self._queue:
            parts.append(f"{self._term(3, 'Still to call')}: {self._names([self._name(s) for s in self._queue])}.")
        if done:
            parts.append(f"{self._term(4, 'Done')}: {self._names([self._name(s) for s in done])}.")
        return [self._speech("status", t, report=" ".join(parts))]

    def _said_status(self, svc: Service, service: str) -> str:
        name = self._name(service)
        name = name[0].upper() + name[1:]
        first = next((line.strip() for line in svc.status.splitlines() if line.strip()), "")
        if svc.progress and first:
            first = f"{first}, {svc.progress}"
        if svc.state == "completed":
            return f"{name}: {self._term(6, 'done')}."
        if not svc.active:
            return f"{name}: {self._term(5, 'not called')}."
        return f"{name}: {first}." if first else f"{name}: {svc.state}."

    def _cancel(self, service: str, t: float) -> list[Any]:
        svc = self._service(service)
        if service in self._queue:
            self._queue.remove(service)
            return [self._speech("cancelled", t, name=self._name(service))]
        if service == "Departure" and self._push is not None and self._push.stage == "armed":
            self._push = None
            return [self._speech("cancelled", t, name=self._name(service))]
        if svc is None or not svc.active:
            return [self._speech("cancel_nothing", t, name=self._name(service))]
        self._cancelled.add(service)
        return self._start_run((row_of(service), Want(("abort service", "abort"))), "cancel", service, t)

    def _finish(self, service: str, t: float) -> list[Any]:
        svc = self._service(service)
        if svc is None or not svc.active:
            return [self._speech("cancel_nothing", t, name=self._name(service))]
        self._cancelled.add(service)  # (its own "done" isn't said as well)
        return self._start_run((row_of(service), Want(("complete now",))), "finish", service, t)

    def _hurry(self, service: str, t: float) -> list[Any]:
        svc = self._service(service)
        if svc is None or not svc.active:
            return [self._speech("cancel_nothing", t, name=self._name(service))]
        self._offer = ("finish", service)
        return [self._speech("cant_hurry", t, name=self._name(service))]

    def _off(self, service: str, t: float) -> list[Any]:
        svc = self._service(service)
        if service in ("OperateJetways", "OperateStairs"):
            if service in self._queue:
                return [self._speech("already", t, name=self._name(service))]
            if self._bridge.get(service) is False:
                return [self._speech("off_nothing", t, name=self._name(service))]
            self._queue.append(service)  # (the same call moves them either way)
            return [self._speech("off", t, name=self._name(service))]
        if svc is None or not svc.active:
            return [self._speech("off_nothing", t, name=self._name(service))]
        self._cancelled.add(service)
        return self._start_run((Want(("dismiss " + (SPOKEN_ROW.get(service, service.lower())),), service == "GPU" or service in ("Water", "Lavatory")),), "off", service, t)

    def _gate(self, label: str, t: float) -> list[Any]:
        ok, why = self.link.request_gate(label)
        out = [self._speech("gate_ok" if ok else "gate_refused", t, why=why)]
        if ok and "gate" not in self.state.capabilities and not self._gate_told:
            self._gate_told = True
            out.append(self._speech("gate_told", t))
        return out

    def _answer(self, yes: bool, t: float) -> list[Any]:
        offer, self._offer = self._offer, None
        if offer is not None:
            if not yes:
                return [self._speech("ok", t)]
            if offer[0] == "push":
                return self._begin_or_arm(offer[1], t)
            if offer[0] == "finish":
                return self._finish(offer[1], t)
        menu = self.state.menu
        if self._asking is not None and menu is not None:
            word = ("yes", "confirm", "ok") if yes else ("no", "cancel", "back")
            found = find(menu, word)
            if found.index is not None:
                return self._picked(menu, found.index, t)
        return [self._speech("ok", t)] if not yes else [self._speech("say_again", t)]

    def _option(self, number: int, t: float) -> list[Any]:
        menu = self.state.menu
        if self._asking is None or menu is None:
            return [self._speech("say_again", t)]
        real = real_entries(menu)
        if not 1 <= number <= len(real):
            return [self._speech("say_again", t)]
        return self._picked(menu, real[number - 1][0], t)

    def _by_label(self, text: str, t: float) -> list[Any] | None:
        """The answer to a question read out, said by name: "Swissport"."""
        menu = self.state.menu
        if self._asking is None or menu is None:
            return None
        said = norm(g.without_address(text))
        hits = [i for i, e in real_entries(menu) if said and (said in norm(e) or norm(e) in said)]
        return self._picked(menu, hits[0], t) if len(hits) == 1 else None

    def _picked(self, menu: Menu, index: int, t: float) -> list[Any]:
        self._asking = None
        self._seen_question = None
        return [Pick(index, menu.entries[index]), self._speech("picked", t, choice=_plain(menu.entries[index]))]

    # ---- the push --------------------------------------------------------------------------------------------------

    def _ask_push(self, direction: str, t: float) -> list[Any]:
        if self._push is not None:
            if direction and self._asking is not None and (said := self._direction_given(direction, t)):
                return said
            if direction and not self._push.direction:
                self._push.direction = direction
                return [self._speech("direction", t, direction=self._said_direction(direction))]
            return [self._speech("push_under_way", t)]
        svc = self._service("Departure")
        if svc is not None and svc.active:
            return [self._speech("push_under_way", t)]
        own = self.own
        if own is not None:
            if not (own.on_ground and own.gs_kt < 1.0):
                return [self._speech("stopped_first", t)]
            if not own.parking_brake:
                return [self._speech("brakes_set_first", t)]
            if own.engine_running:
                return [self._speech("engines_off_first", t)]
        running = [s for s in self.state.services.values() if s.active and s.id not in ("Departure",)]
        if running:
            self._offer = ("push", direction)
            return [self._speech("warn_push", t, list=self._names([self._name(s.id) for s in running]))]
        return self._begin_or_arm(direction, t)

    def _begin_or_arm(self, direction: str, t: float) -> list[Any]:
        said = [self._speech("direction", t, direction=self._said_direction(direction))] if direction else []
        self._push = Push(direction=direction, t=t)
        if self.wait_for_clearance and self.link.atc_working() and not self.link.pushback_cleared():
            return [*said, self._speech("armed", t)]
        return [*said, *self._call_push(t)]

    def _call_push(self, t: float) -> list[Any]:
        assert self._push is not None
        self._push.stage = "called"
        self._queue.insert(0, "Departure")
        return []

    def _stop_push(self, t: float) -> list[Any]:
        push = self._push
        if push is None or push.stage == "armed":
            self._push = None
            return [self._speech("no_push", t)]
        self._cancelled.add("Departure")
        return [self._speech("stopping", t), *self._start_run((row_of("Departure"), Want(("stop", "abort service", "abort"))), "cancel", "Departure", t)]

    def _confirmation(self, what: str, t: float) -> list[Any]:
        menu = self.state.menu
        if what == g.GOOD_START and menu is not None and self._is_engine_question(menu):
            found = find(menu, GOOD_START_ROW)
            if found.index is not None:
                return self._picked(menu, found.index, t)
        return []  # (the brakes are read from the aircraft: said or not, that's what counts)

    def _push_moves(self, t: float) -> list[Any]:
        """The push as it goes: what the aircraft and GSX are doing, the standard words at each point."""
        push, own = self._push, self.own
        if push is None:
            return []
        svc = self._service("Departure")
        out: list[Any] = []
        if push.stage == "armed":
            if self.link.pushback_cleared():
                out.append(self._speech("cleared_push", t))
                out += self._call_push(t)
            return out
        if own is None or svc is None:
            return out
        message = norm(self.state.message)
        if push.stage in ("called", "asked") and svc.active:
            push.stage = "connecting"
        if push.stage == "connecting" and "release" not in push.told and ("release" in message or svc.state == "performing"):
            push.told.add("release")
            if own.parking_brake:
                out.append(self._speech("release_brakes", t))
        if push.stage == "connecting" and not own.parking_brake and "release" in push.told:
            push.stage = "pushing"
            out.append(self._speech("pushing", t))
        if push.stage == "pushing":
            if "start" not in push.told and own.gs_kt > 0.5:
                push.told.add("start")
                out.append(self._speech("cleared_start", t))
            if "brake" in message or svc.state == "completing":
                push.stage = "set_brake"
        if push.stage == "set_brake":
            if not own.parking_brake and ("brake" not in push.told or t - push.brake_said_t >= BRAKE_AGAIN_S):
                out.append(self._speech("brake_again" if "brake" in push.told else "set_brake", t))
                push.told.add("brake")
                push.brake_said_t = t
            elif own.parking_brake:
                push.stage = "start"
        if push.stage == "start" and not svc.active:
            side = SIDE.get(push.direction)
            out.append(self._speech("disconnecting" if side else "disconnecting_no_side", t, side=side or ""))
            self._push = None
        return out

    # ---- GSX's questions -------------------------------------------------------------------------------------------

    def _is_ours(self, t: float) -> bool:
        if self._push is not None or self._sending is not None or self._run is not None:
            return True
        for service, asked_t in self._ours.items():
            svc = self._service(service)
            if t - asked_t <= OURS_S or (svc is not None and svc.active):
                return True
        return False

    @staticmethod
    def _is_engine_question(menu: Menu) -> bool:
        return has_words(menu.title, "engine") or any(has_words(e, "good engine start") for e in menu.entries)

    def _question(self, menu: Menu, t: float) -> list[Any]:
        """A page of GSX's that's asking something. Answered when it's known and ours to answer, otherwise read out."""
        if menu == self._seen_question or not self._is_ours(t):
            return []
        self._seen_question = menu
        if has_words(menu.title, "operator"):
            real = [(i, e) for i, e in real_entries(menu) if not (i < len(menu.disabled) and menu.disabled[i])]
            choice = next((i for i, e in real if OPERATOR_CHOICE in norm(e)), real[0][0] if real else None)
            if choice is not None:
                return [Pick(choice, menu.entries[choice])]
        if has_words(menu.title, "refueling level"):
            return self._fuel_question(menu, t)
        for words, answer in SETTLED:
            if has_words(menu.title, words) and (found := find(menu, answer)).index is not None:
                return [Pick(found.index, menu.entries[found.index])]
        rows = {d: find(menu, words) for d, words in DIRECTIONS.items()}
        if any(f.index is not None for f in rows.values()):
            return self._direction_question(menu, rows, t)
        if (gsx_choice := find(menu, (OPERATOR_CHOICE,))).index is not None:
            return [Pick(gsx_choice.index, menu.entries[gsx_choice.index])]  # GSX's own recommendation, whatever it's asking
        if self._is_engine_question(menu):
            if self.own is not None and self.own.engine_running and (found := find(menu, GOOD_START_ROW)).index is not None:
                return [Pick(found.index, menu.entries[found.index])]
            self._asking = menu
            return [self._speech("confirm_start", t)]
        self._asking = menu
        real = real_entries(menu)
        choices = ", ".join(f"{n}: {_plain(e)}" for n, (_, e) in enumerate(real, 1))
        return [self._speech("question", t, title=_plain(menu.title), choices=choices)]

    @staticmethod
    def _fuel_rows(menu: Menu) -> list[tuple[int, int]]:
        """GSX's fuel levels: (row, percent)."""
        rows = []
        for i, entry in enumerate(menu.entries):
            if m := FUEL_ROW.search(entry):
                rows.append((i, int(m.group(1))))
        return rows

    def _fuel_question(self, menu: Menu, t: float) -> list[Any]:
        """The truck's in position and GSX wants to know how much. It gives up in half a minute and sends the truck away, so
        it's said at once, not held behind other calls. Asked for already: the level at or above it."""
        rows = self._fuel_rows(menu)
        if self._fuel_wanted and rows:
            return self._pick_fuel(menu, rows, self._fuel_wanted, t)
        self._asking = menu
        percents = [p for _, p in rows]
        if not percents:
            return [self._speech("question", t, title=_plain(menu.title), choices=", ".join(_plain(e) for _, e in real_entries(menu)))]
        return [self._speech("fuel_level", t, low=str(min(percents)), high=str(max(percents)))]

    def _pick_fuel(self, menu: Menu, rows: list[tuple[int, int]], wanted: int, t: float) -> list[Any]:
        at_least = [r for r in rows if r[1] >= wanted]
        index, percent = min(at_least, key=lambda r: r[1]) if at_least else max(rows, key=lambda r: r[1])
        self._asking = None
        self._fuel_wanted = 0
        return [Pick(index, menu.entries[index]), self._speech("fuel_loading", t, percent=str(percent))]

    def _fuel_level(self, percent: int, t: float) -> list[Any]:
        """How much fuel, said on its own: for the question that's up, or for when it comes."""
        menu = self.state.menu
        if menu is not None and has_words(menu.title, "refueling level") and (rows := self._fuel_rows(menu)):
            return self._pick_fuel(menu, rows, percent, t)
        self._fuel_wanted = percent
        return [self._speech("fuel_noted", t, percent=str(percent))]

    def _engines_answer(self, t: float) -> list[Any]:
        """The good-start question up and waiting, and now the engines are running: that's the answer."""
        menu, own = self._asking, self.own
        if menu is None or own is None or not own.engine_running or self.state.menu != menu or not self._is_engine_question(menu):
            return []
        found = find(menu, GOOD_START_ROW)
        if found.index is None:
            return []
        self._asking = None
        return [Pick(found.index, menu.entries[found.index])]

    def _direction_question(self, menu: Menu, rows: dict, t: float) -> list[Any]:
        push = self._push
        wanted = push.direction if push is not None else ""
        if wanted and rows[wanted].index is not None:
            return [Pick(rows[wanted].index, menu.entries[rows[wanted].index])]
        available = [self._said_direction(d).replace("，", ",").split(",")[0] for d, f in rows.items() if f.index is not None]
        self._asking = menu
        if push is not None:
            push.stage = "asked"
        if wanted:
            return [self._speech("no_direction", t, direction=self._said_direction(wanted), choices=self._names(available))]
        return [self._speech("ask_direction", t, choices=self._names(available))]

    def _direction_given(self, direction: str, t: float) -> list[Any]:
        """The pilot's answer to which way: the row picked now, while the question's up."""
        menu = self.state.menu
        if self._push is not None:
            self._push.direction = direction
        if menu is not None and (found := find(menu, DIRECTIONS[direction])).index is not None:
            self._asking = None
            self._seen_question = None
            return [Pick(found.index, menu.entries[found.index]), self._speech("direction", t, direction=self._said_direction(direction))]
        return []

    # ---- the menu --------------------------------------------------------------------------------------------------

    def _start_run(self, wants: tuple[Want, ...], kind: str, service: str, t: float) -> list[Any]:
        self._run = MenuRun(wants, t, goal=kind)
        self._run_after = (kind, service)
        return self._step_run(t)

    def _step_run(self, t: float) -> list[Any]:
        run = self._run
        if run is None:
            return []
        action = run.step(self.state.menu, t)
        out: list[Any] = [action] if action is not None else []
        if run.finished:
            self._run = None
            kind, service = self._run_after or ("", "")
            name = self._name(service)
            if run.failed:
                self._cancelled.discard(service)
                out.append(self._speech({"cancel": "cancel_failed", "finish": "finish_failed", "off": "off_failed"}.get(kind, "menu_failed"),
                                        t, name=name, why=self._why(run.failed)))
            else:
                out.append(self._speech({"cancel": "cancelled", "finish": "finished", "off": "off"}[kind], t, name=name))
        return out

    # ---- what's going on -------------------------------------------------------------------------------------------

    def observe(self, own: OwnshipState) -> None:
        self.own = own

    def update(self, state: GroundState, t: float) -> list[Any]:
        """GSX's state changed."""
        self._t = t
        before, self.state = self.state, state
        out: list[Any] = []
        for service, new in state.services.items():
            old = before.services.get(service)
            if service == "GPU" and new.state == "performing" and (old is None or old.state != "performing"):
                self._calls.append((t, ROUTINE, "done_GPU", {}))  # (it stays "performing" for as long as it's connected)
            if service in ("OperateJetways", "OperateStairs") and new.status:
                said = norm(new.status)
                self._bridge[service] = not ("undocked" in said or "not docked" in said) if "docked" in said else self._bridge.get(service, None)
            if old is not None and old.active and not new.active and (new.state in ("completed", "available")):
                out += self._done(service, t)
        if state.message != before.message and state.message:
            out += self._message(state.message, t)
        menu = state.menu
        if menu is None:
            self._asking = None
            self._seen_question = None
        elif is_question(menu):
            out += self._question(menu, t)
        return out + self.tick(t)

    def _message(self, text: str, t: float) -> list[Any]:
        low = text.lower()
        if low.startswith("waiting for your action"):
            door = text.split(":", 1)[-1].strip()
            if t - self._door_t.get(door, -1e9) >= DOOR_AGAIN_S:
                self._door_t[door] = t
                self._calls.append((t, ROUTINE, "door", {"door": door}))
        elif low.startswith(PASSED_ON):
            return [self._speech("gsx_says", t, message=text)]
        return []

    def _done(self, service: str, t: float) -> list[Any]:
        if service in self._cancelled:
            self._cancelled.discard(service)
            return []
        self._ours.pop(service, None)
        if service not in self._finished:
            self._finished.append(service)
        if service == "GPU":
            return []  # (said when it connected: this is it going away)
        if service in RELAYED:
            said = self.link.relay("boarding_done" if service == "Boarding" else "deboarding_done", t)
            if said is not None:
                return said
        values = {}
        if service == "Refueling":
            fuel = self.own.fuel_lb if self.own is not None else None
            amount = f"{int(round(fuel, -1)):,}" if fuel else ""
            values["fuel"] = "" if not fuel else foreign.FUEL_ON_BOARD[self.language].format(x=amount) if self.language in foreign.FUEL_ON_BOARD else f", {amount} pounds on board"
        self._calls.append((t, ROUTINE if service != "Departure" else CHATTER, f"done_{service}", values))
        return []

    def _busy(self, t: float) -> bool:
        return self.keyed or t < self.atc_until

    def _sterile(self) -> bool:
        own = self.own
        return own is not None and own.alt_indicated_ft < STERILE_FT and (own.engine_running or not own.on_ground)

    def _due_calls(self, t: float) -> list[Any]:
        """The crew's own calls: by the pilot's verbosity, never over ATC or the pilot, none of the chatter once it's sterile."""
        self._calls = [c for c in self._calls if t - c[0] <= CALL_WAIT_S and c[1] >= self.level]
        if not self._calls or self._busy(t) or t - self._call_t < CALL_GAP_S:
            return []
        queued, level, key, values = self._calls.pop(0)
        if level <= CHATTER and self._sterile():
            return []
        self._call_t = t
        return [self._speech(key, t, **values)]

    def not_sent(self, service: str, code: str, t: float) -> list[Any]:
        """GSX refused the call, or wasn't there to take it."""
        self._sending = None
        self._ours.pop(service, None)
        return [self._speech("no_gsx" if code in ("not_connected", "gsx_not_running") else "no_take", t, name=self._name(service))]

    def tick(self, t: float) -> list[Any]:
        """A moment passing: the next service called, the menu driven, the push followed, the crew's own calls."""
        self._t = t
        out: list[Any] = self._step_run(t)
        out += self._push_moves(t)
        out += self._engines_answer(t)
        out += self._send_next(t)
        out += self._due_calls(t)
        out += self._parked_elsewhere(t)
        return out

    def _send_next(self, t: float) -> list[Any]:
        if self._sending is not None:
            service, sent = self._sending
            svc = self._service(service)
            if svc is not None and (svc.active or svc.state == "completed"):
                self._sending = None
            elif self.state.menu is not None and is_question(self.state.menu):
                self._sending = (service, t)  # (answering GSX's questions about it: not stuck)
            elif t - sent >= SEND_S:
                self._sending = None
                self._ours.pop(service, None)
                out: list[Any] = [Close()] if self.state.menu is not None else []
                return [*out, self._speech("no_take", t, name=self._name(service))]
            else:
                return []
        if self._run is not None or not self._queue or (self.state.menu is not None and is_question(self.state.menu)):
            return []
        service = self._queue.pop(0)
        self._sending = (service, t)
        self._ours[service] = t
        return [Send(service)]

    def _parked_elsewhere(self, t: float) -> list[Any]:
        """On a stand that isn't the one ATC gave: where the aircraft is, is where the services go."""
        gate, parked = self.link.gate(), self.state.parking
        if self._said_differs or not gate or not parked or not self.parked or self.own is None or self.own.engine_running:
            return []
        if _stand(gate) != _stand(parked):
            self._said_differs = True
            self._calls.append((t, ROUTINE, "gate_differs", {"parked": parked, "gate": gate}))
        return []


@dataclass(frozen=True)
class Send:
    """Call this service."""

    service: str


def _plain(entry: str) -> str:
    """A menu row as said aloud: without GSX's marks."""
    return " ".join(entry.replace("[GSX choice]", "").replace("▶", "").replace("↑", "").split()).rstrip("?. ")


def _stand(name: str) -> str:
    """A stand's name to compare by: "Gate B 37", "B37"."""
    return norm(name).replace("gate", "").replace("stand", "").replace("parking", "").replace(" ", "")


SPOKEN_ROW = {"GPU": "ground power", "Water": "water", "Lavatory": "lavatory", "Catering": "catering", "Refueling": "refueling",
              "Cleaning": "cleaning", "DeIce": "deice"}

__all__ = ["Close", "Open", "Pick", "RampCrew", "RampLink", "Send"]
