"""The Pilot Monitoring: hears the pilot on the intercom, works the aircraft, and says what it did.

Every command goes the same way: the safety rules (``actions.safety``) first, then the sim commands, then a look at
the sim within ``CHECK_S``: "Flaps 2." once the handle shows it, "Flaps 2 didn't take, check it." if it never does.
A command the rules want confirmed waits up to ``CONFIRM_S`` for "confirm". A radio call said on the intercom by
mistake is offered to be sent ("That was on the intercom. Send it?"). A question is answered from the copilot's facts
(``answers``): the common ones directly, the rest by the language model (``model``), as is a command the grammar
couldn't read, which the copilot then asks the pilot to confirm before doing.

Pure: ``observe`` takes each bus event (with its ``t``) and returns what to publish (``CrewSpeech``, ``CrewAction``,
the copilot's ``Transcript`` on the radio) and the ``SimCommand``s to send, so a recording replays through it the
same way.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

from localtc.atc_core import region as regions
from localtc.config import PlanPerf
from localtc.crew.actions import Cockpit, Plan, plan, safety
from localtc.crew.answers import Picture, answer, facts
from localtc.crew.commands import Command, parse
from localtc.crew import monitor as monitors
from localtc.crew.monitor import SAFETY, Call, Monitor
from localtc.crew.profiles import Profile, for_aircraft, load_all
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AtcTransmission,
    CrewAction,
    CrewSpeech,
    IntercomHeard,
    OwnshipState,
    PhaseChanged,
    SimCommand,
    Transcript,
)

log = logging.getLogger(__name__)

CHECK_S = 3.0  # the sim shows a command within this, or it didn't take
CONFIRM_S = 10.0  # a "confirm?" waits this long for the answer
OFFER_S = 15.0  # "send it?" for a radio call said on the intercom
ACK = ("check", "checked", "set", "checks", "noted", "copy", "roger", "okay", "ok")  # the last word: a statement, said
# Words that only acknowledge ("check", "roger that", "yep"): nothing to answer. A first officer doesn't reply to them.
ACK_ONLY = re.compile(r"^(?:(?:check(?:ed)?|copy(?: that)?|roger(?: that)?|ok(?:ay)?|yep|yup|yeah|got it|gotcha|alright|"
                      r"all right|noted|thanks|thank you|cool|good|sounds good|understood|affirm|right|sure)[\s,.!]*)+$")
# "Later", "not now", "quiet please": the copilot's suggestions wait (crew.monitor.DECLINE_QUIET_S).
DECLINE = re.compile(r"\b(?:later|not (?:yet|now|right now)|no,? not|stop (?:talking|it|that)|shut up|be quiet|"
                     r"enough|i didn'?t ask)\b")
OWN_HANDS_FAILED = 2  # the copilot's own switches not taking this many times, none ever taking: it can't reach them
SHORT_COMMAND_WORDS = 5  # a command this short is clear: done straight away, whatever the mode
STATION_WORDS = ("tower", "ground", "approach", "center", "centre", "departure", "clearance", "delivery", "unicom",
                 "radio", "traffic")


@dataclass
class _Waiting:
    deadline: float
    plan: Plan
    quiet: bool = False  # the copilot's own action, already said: only a failure is reported


class PilotMonitoring:
    def __init__(self, engine: Any = None, *, profiles: list[Profile] | None = None, model: Any = None,
                 verbosity: str = "standard", hands: str = "pm", perf: PlanPerf | None = None, plan_source: str = "",
                 radio_mode=lambda: "off", alternate: str = "", repeat_atc: bool = False) -> None:
        self.engine = engine  # the ATC engine, read only: what's been cleared, the stations, the callsign
        self.profiles = profiles if profiles is not None else load_all()
        self.model = model  # crew.model.CrewModel, or None: the grammar and the data answers only
        self.cockpit = Cockpit(profile=for_aircraft(self.profiles, "", ""))
        self.picture = Picture(self.cockpit, engine)
        # The copilot speaking first: callouts, reminders, relays, its own side of the cockpit (crew.monitor).
        self.monitor = Monitor(self.picture, verbosity=verbosity, hands=hands, perf=perf, plan_source=plan_source,
                               radio_mode=radio_mode, seed=_seed(engine), alternate=alternate, repeat_atc=repeat_atc)
        self._waiting: list[_Waiting] = []
        self._confirm: tuple[float, Command] | None = None  # (deadline, the command waiting for "confirm")
        self._offer: tuple[float, str] | None = None  # (deadline, a radio call to send)
        self._failed_said: set[str] = set()  # "didn't take" said once per control
        self._own_failed = self._own_took = 0
        self._no_hands_said = False

    @property
    def profile(self) -> Profile:
        return self.cockpit.profile

    def observe(self, ev: Any) -> list[Any]:
        """``ev`` happened; returns the events to publish and the sim commands to send, in order."""
        out: list[Any] = []
        if isinstance(ev, OwnshipState):
            self._note_own(ev)
            self.cockpit.own = ev
            self._read_clearance()
        elif isinstance(ev, AircraftSystems):
            self._note_systems(ev)
            self.cockpit.systems = ev
        elif isinstance(ev, PhaseChanged):
            self._note(ev.t, f"phase now {ev.phase.lower().replace('_', ' ')}")
        elif isinstance(ev, AircraftIdentity):
            profile = for_aircraft(self.profiles, ev.title, ev.atc_model)
            if profile is not self.cockpit.profile:
                log.info("Copilot: %s profile for %s", profile.name, ev.title or ev.atc_model)
                self.cockpit.profile = profile
        elif isinstance(ev, AtcTransmission):
            self.picture.last_atc = ev
            self._note(ev.t, f'{ev.station} said: "{ev.text}"')
        elif isinstance(ev, Transcript) and ev.radio != 0 and ev.text:
            self._note(ev.t, f'{"the copilot" if ev.source == "copilot" else "you"} said on the radio: "{ev.text}"')
        elif isinstance(ev, IntercomHeard):
            self._read_clearance()
            out += self._heard(ev.t, ev.text)
        self.monitor.observe(ev)
        out += self._tick(ev.t)
        for call in self.monitor.due(ev.t):
            out += self._callout(call, ev.t)
        if self.monitor.requests:  # what the monitor wants from the sim: an arrival's restrictions, airports around
            out += self.monitor.requests
            self.monitor.requests = []
        return out

    def _callout(self, call: Call, t: float) -> list[Any]:
        """One of the monitor's calls: the copilot's own hands first (each checked, as a command is), then the words."""
        out: list[Any] = []
        for cmd in call.commands:
            p = plan(cmd, self.cockpit)
            if isinstance(p, str) or safety(cmd, self.cockpit).kind != "ok":
                continue
            if p.check(self.cockpit) is True:
                continue  # already so
            out += [*p.writes, CrewAction(t=t, action=p.action, value=p.value, outcome="sent",
                                          detail=f"{call.key}: " + "; ".join(_describe(w) for w in p.writes))]
            if cmd.action != "altimeter":  # an airliner's own STD and QNH buttons often leave the sim's setting alone
                self._waiting.append(_Waiting(t + CHECK_S, p, quiet=True))
        kind = "alert" if call.priority >= SAFETY else "callout"
        text, spoken = call.text, call.spoken
        if not text:  # a hand only (the altimeter to the ATIS): nothing said
            return out
        # Only the copilot's talk is put in its own words; a callout or a warning keeps its words (reworded,
        # "moderate turbulence" became "turbulence ahead" and a heading reminder "heading's set").
        if self.model is not None and call.priority < SAFETY and not call.urgent and not call.before_readback \
                and call.key.startswith(REWORDED):
            words, exchanges = self.model.reword(t, text)
            out += exchanges
            if words:
                text, spoken = words, ""
        if self.model is not None:
            self.model.heard("You", text)
        out.append(CrewSpeech(t=t, text=text, spoken=spoken, kind=kind))
        if call.radio:  # then on the radio: the copilot asking ATC
            out.append(Transcript(t=t, text=call.radio, source="copilot"))
        return out

    # --- the pilot ------------------------------------------------------------------------------------------------

    def _heard(self, t: float, text: str) -> list[Any]:
        """What the captain said on the intercom, read as the mode says (``crew.model``): the grammar and the data
        first in the scripted modes, the model first in the LLM ones."""
        if not text.strip():
            return []
        mode = self.model.mode if self.model is not None else "off"
        commands = parse(text)
        words = text.lower().strip(" .!?").replace(",", " ").split()
        acked = bool(words) and words[-1] in ACK
        lowered = text.lower().strip()
        if not monitors.CHECKLISTS and any(c.action == "checklist" for c in commands):  # none to read yet
            return [self._say(t, "No checklists from me yet; I'll read them once we have the real ones for this aircraft.")]
        if DECLINE.search(lowered) and self._confirm is None and self._offer is None:
            self.monitor.declined(t)
            if self.model is not None:
                self.model.heard("Captain", text)
            return [self._say(t, "Copy.")]
        if ACK_ONLY.match(lowered) and not commands and self._confirm is None and self._offer is None \
                and self.monitor.offer is None and self.monitor.cr is None:
            if self.model is not None:
                self.model.heard("Captain", text)
            # Nothing to answer: in the scripted modes a word back, as before; otherwise silence, as a crew does.
            return [self._say(t, self.monitor.rng.choice(["Check.", "Copy."]))] if mode in ("off", "scripted") else []
        # An answer to a checklist item ("on", "set", "one plus F, checked") is taken as that, whatever the words.
        if self.monitor.cr is not None and not any(c.action in ("checklist", "brief", "status", "verbosity")
                                                   for c in commands) and len(words) <= 8:
            done: list[Any] = []
            for cmd in commands:  # "flaps one" as the answer to "Flaps?": set too
                if cmd.action not in ("yes", "no", "check"):
                    done += self._command(t, cmd)
            if self.monitor.respond(text, t):
                return done + self._now(t)
        if acked and not commands and (self._confirm is not None or self.monitor.offer is not None):
            return self._answer(t, True)  # "beacon, check": yes to what was asked
        if acked and not commands and mode in ("off", "scripted", "semi"):
            return [self._say(t, self.monitor.rng.choice(["Check.", "Checked.", "Copy."]))]
        if commands:
            control = [c for c in commands if c.action in ("checklist", "brief", "status", "verbosity", "check",
                                                            "yes", "no")]
            clear = len(words) <= SHORT_COMMAND_WORDS or mode in ("off", "scripted", "semi")
            if control or clear:
                out: list[Any] = []
                for cmd in commands:
                    out += self._command(t, cmd)
                if self.model is not None:
                    self.model.heard("Captain", text)
                return out
        if self._radio_call(text) and mode != "llm":
            self._offer = (t + OFFER_S, text)
            return [self._say(t, "That was on the intercom. Want me to send it?", "confirm")]
        if mode in ("off", "scripted") and (said := answer(text, self.picture)) is not None:
            return [self._say(t, said)]
        if mode == "off" and commands:
            out = []
            for cmd in commands:
                out += self._command(t, cmd)
            return out
        return self._ask_model(t, text, grammar=commands)

    def _ask_model(self, t: float, text: str, grammar: list[Command] | None = None) -> list[Any]:
        """The model's reply, or its reading of a command: done straight away when the grammar read the same command,
        otherwise said back for the pilot's "confirm"."""
        if self.model is None:
            return [self._say(t, "Say again?")]
        more = None
        if getattr(self.model.backend, "rich", False) and self.engine is not None and hasattr(self.engine, "_flight_more"):
            more = self.engine._flight_more(None, t)  # a cloud model: the whole flight, not just the copilot's facts
        known = facts(self.picture)
        if (said := answer(text, self.picture)) is not None:
            known["the answer from the instruments"] = said  # the data's own answer, for the model to put in words
        reading, exchanges = self.model.ask(t, text, known, more=more)
        if reading is None:
            if grammar:  # the model had nothing; the grammar did
                out = list(exchanges)
                for cmd in grammar:
                    out += self._command(t, cmd)
                return out
            if said is not None:
                return [*exchanges, self._say(t, said)]
            why = exchanges[-1].detail if exchanges else ""
            if why.startswith(("claims", "reassures", "names a place")):  # it said what isn't so: what fits instead
                asked = "?" in text or re.match(r"^\s*(?:[a-z']+[,\s]+){0,2}(?:what|how|where|which|who|do|did|are|is|will|can)\b",
                                                 text.lower())
                return [*exchanges, self._say(t, "Can't tell that from here." if asked else "Copy.")]
            return [*exchanges, self._say(t, "Say again?")]
        if reading.command is None:
            return [*exchanges, self._say(t, reading.reply)]
        cmd = reading.command
        if grammar and any(g.action == cmd.action and g.value.lower() == cmd.value.lower() for g in grammar):
            return [*exchanges, *self._command(t, cmd)]  # the model and the grammar read the same: no need to ask
        verdict = safety(cmd, self.cockpit)
        if verdict.kind == "refuse":
            return [*exchanges, self._say(t, verdict.reason, "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=verdict.reason)]
        self._confirm = (t + CONFIRM_S, cmd)
        question = verdict.reason if verdict.kind == "confirm" else f"{reading.reply.rstrip('.!')}, confirm?"
        return [*exchanges, self._say(t, question, "confirm"),
                CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="confirm", detail="read by the language model")]

    def _command(self, t: float, cmd: Command) -> list[Any]:
        if cmd.action == "check":
            return [self._say(t, "Loud and clear.")]
        if cmd.action in ("yes", "no"):
            return self._answer(t, cmd.action == "yes")
        if cmd.action == "checklist":
            name = cmd.value or self._next_checklist()
            if name is None:
                return [self._say(t, "Which checklist?")]
            self.monitor.checklist(name, t)
            return self._now(t)
        if cmd.action == "brief":
            self.monitor.briefing(cmd.value, t)
            return self._now(t)
        if cmd.action == "status":
            self.monitor.status(t)
            return self._now(t)
        if cmd.action == "verbosity":
            self.monitor.verbosity = cmd.value
            return [self._say(t, {"quiet": "Copy, only what matters.", "chatty": "Copy, I'll keep you posted.",
                                  "standard": "Copy, the usual calls."}[cmd.value])]
        self.monitor.pilot_said(cmd, t)
        if not self.monitor.hands and self.monitor.hands_setting and cmd.action != "com_active":
            # Can't reach this aircraft's switches: said, not pretended (once fully, then briefly).
            if self._no_hands_said:
                return [self._say(t, "That one's yours.", "refused")]
            self._no_hands_said = True
            return [self._say(t, "I can't move this aircraft's switches from here; they're yours.", "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail="no hands in this aircraft")]
        verdict = safety(cmd, self.cockpit)
        if verdict.kind == "refuse":
            return [self._say(t, verdict.reason, "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=verdict.reason)]
        if verdict.kind == "confirm":
            self._confirm = (t + CONFIRM_S, cmd)
            return [self._say(t, verdict.reason, "confirm"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="confirm", detail=verdict.reason)]
        return self._do(t, cmd)

    def _now(self, t: float) -> list[Any]:
        """What the pilot just asked the monitor for, said at once (they asked: no waiting for a gap)."""
        out: list[Any] = []
        for call in [q for q in self.monitor.queue if q.t <= t and q.key.startswith(("run:", "brief:", "status:", "landing:"))]:
            self.monitor.queue.remove(call)
            out += self._callout(call, t)
        return out

    def _next_checklist(self) -> str | None:
        offer = self.monitor.offer
        if offer is not None and offer.kind == "checklist":
            return offer.value
        phase = self.monitor.phase or "PARKED"
        return {"PARKED": "before_start", "PUSHBACK": "after_start", "TAXI_OUT": "before_takeoff",
                "RUNWAY_HOLD": "before_takeoff", "DEPARTURE": "after_takeoff", "CRUISE": "descent", "ARRIVAL": "descent",
                "APPROACH": "landing", "LANDING": "landing", "TAXI_IN": "shutdown"}.get(phase)

    def _answer(self, t: float, yes: bool) -> list[Any]:
        if (offer := self.monitor.take_offer(t)) is not None:
            if not yes:
                self.monitor.declined(t)
                return [self._say(t, "Copy, later then.")]
            if offer.kind == "checklist":
                self.monitor.checklist(offer.value, t)
                return self._now(t)
            if offer.kind == "brief":
                self.monitor.briefing(offer.value, t)
                return self._now(t)
            if offer.kind == "squawk":
                return self._command(t, Command("squawk", offer.value))
            if offer.kind in ("step", "divert", "sight"):
                return self._ask_atc(t, offer.kind, offer.value)
        if self._confirm is not None and t <= self._confirm[0]:
            cmd = self._confirm[1]
            self._confirm = None
            return self._do(t, cmd) if yes else [self._say(t, "Copy, leaving it.")]
        if self._offer is not None and t <= self._offer[0]:
            text = self._offer[1]
            self._offer = None
            if yes:  # the copilot says it on the radio, in the pilot's words
                return [Transcript(t=t, text=text, source="copilot")]
            return [self._say(t, "Copy.")]
        return [self._say(t, "Copy.")] if yes else []

    def _ask_atc(self, t: float, kind: str, value: str) -> list[Any]:
        """The pilot said yes to asking ATC (a step climb, a diversion, the field in sight): the copilot asks when it
        works the radio, else says who to tell."""
        words = self.monitor.radio_request(kind, value)
        if self.monitor.radio_mode() == "full" and words:
            return [self._say(t, "Asking."), Transcript(t=t, text=words, source="copilot")]
        st = self.engine.state if self.engine is not None else None
        station = st.comms.tuned.station if st is not None and st.comms.tuned is not None else "ATC"
        if kind == "sight":
            return [self._say(t, f"Tell {station}, and they'll clear us for the visual.")]
        return [self._say(t, f"Your radios: ask {station}" + (f", \"{words.split(', ', 2)[-1]}\"." if words else "."))]

    def _do(self, t: float, cmd: Command) -> list[Any]:
        p = plan(cmd, self.cockpit)
        if isinstance(p, str):
            return [self._say(t, p, "refused"), CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=p)]
        shown = p.check(self.cockpit)
        if shown and p.already:
            return [self._say(t, p.already), CrewAction(t=t, action=p.action, value=p.value, outcome="done", detail="already")]
        out: list[Any] = [*p.writes, CrewAction(t=t, action=p.action, value=p.value, outcome="sent",
                                                 detail="; ".join(_describe(w) for w in p.writes))]
        if shown is None:  # nothing to read it back from (an add-on that doesn't report it): said as done
            out.append(self._say(t, p.done, "done", p.done_spoken))
        else:
            self._waiting = [w for w in self._waiting if w.plan.action != p.action or w.plan.action == "light"]
            self._waiting.append(_Waiting(t + CHECK_S, p))
        return out

    # --- the sim ---------------------------------------------------------------------------------------------------

    def _tick(self, t: float) -> list[Any]:
        out: list[Any] = []
        still = []
        for w in self._waiting:
            shown = w.plan.check(self.cockpit)
            if shown or shown is None:
                if not w.quiet:
                    out.append(self._say(t, w.plan.done, "done", w.plan.done_spoken))
                if shown and w.quiet:
                    self._own_took += 1
                out.append(CrewAction(t=t, action=w.plan.action, value=w.plan.value, outcome="done"))
            elif t >= w.deadline:
                out.append(CrewAction(t=t, action=w.plan.action, value=w.plan.value, outcome="failed",
                                      detail=f"the sim didn't show it within {CHECK_S:.0f} s"))
                if w.quiet:
                    self._own_failed += 1
                if w.quiet and self._own_failed >= OWN_HANDS_FAILED and not self._own_took and not self.monitor.hands_dead:
                    # Its switches never reach this aircraft: calls only from now on, said once.
                    self.monitor.hands_dead = True
                    out.append(self._say(t, "My switches aren't reaching this aircraft. I'll leave them to you and call.", "alert"))
                elif w.plan.action not in self._failed_said and not self.monitor.hands_dead:
                    self._failed_said.add(w.plan.action)  # once for each control: then it's known
                    what = w.plan.done.rstrip(".").replace(" set", "")
                    out.append(self._say(t, f"{what} didn't take, check it.", "alert"))
            else:
                still.append(w)
        self._waiting = still
        if self._confirm is not None and t > self._confirm[0]:
            self._confirm = None
        if self._offer is not None and t > self._offer[0]:
            self._offer = None
        return out

    # --- what ATC knows --------------------------------------------------------------------------------------------

    def _read_clearance(self) -> None:
        if self.engine is None:
            return
        # Fuel in kilograms, the way airlines outside the US count it.
        self.picture.metric = regions.region_for(self.engine.state.flight.origin) is not regions.FAA
        a = self.engine.state.assignments
        self.cockpit.cleared_altitude_ft = a.altitude_ft
        self.cockpit.assigned_squawk = a.squawk

    def _radio_call(self, text: str) -> bool:
        """Words that are a radio call, not a word to the copilot: a station's name, or the callsign first."""
        lowered = text.lower()
        stations = [f.station.lower() for f in self.engine.facilities] if self.engine is not None else []
        if any(s and s in lowered for s in stations):
            return True
        first = lowered.replace(",", " ").split()[:4]
        if any(w in STATION_WORDS for w in first) and len(lowered.split()) >= 4:
            return True
        callsign = self.engine.state.flight.callsign if self.engine is not None else None
        telephony = getattr(callsign, "telephony", "") or ""
        return bool(telephony) and lowered.startswith(telephony.lower())

    def _say(self, t: float, text: str, kind: str = "reply", spoken: str = "") -> CrewSpeech:
        if self.model is not None and kind != "reply":  # its replies the model remembers itself
            self.model.heard("You", text)
        return CrewSpeech(t=t, text=text, spoken=spoken, kind=kind)

    # --- what the model remembers of the flight deck ----------------------------------------------------------------

    def _note(self, t: float, what: str) -> None:
        if self.model is not None:
            self.model.event(t, what)

    def _note_own(self, own: OwnshipState) -> None:
        was = self.cockpit.own
        if was is None:
            return
        if was.parking_brake != own.parking_brake:
            self._note(own.t, "parking brake set" if own.parking_brake else "parking brake released")
        if was.on_ground and not own.on_ground:
            self._note(own.t, "airborne")
        elif not was.on_ground and own.on_ground:
            self._note(own.t, "touched down")
        if was.gear_down != own.gear_down:
            self._note(own.t, "gear down" if own.gear_down else "gear up")
        if was.flaps_index != own.flaps_index:
            self._note(own.t, f"flaps to {self.profile.detent_name(own.flaps_index, self.cockpit.flap_positions, on_ground=own.on_ground)}")

    def _note_systems(self, s: AircraftSystems) -> None:
        was = self.cockpit.systems
        if was is None:
            return
        if s.engines_running != was.engines_running:
            self._note(s.t, f"{s.engines_running} engine{'s' if s.engines_running != 1 else ''} running")
        if s.ap_master != was.ap_master:
            self._note(s.t, "autopilot on" if s.ap_master else "autopilot off")


REWORDED = ("greeting", "summary", "brief:", "status", "tod", "fuel_check", "divert", "step:", "clear")  # its own talk


def _seed(engine: Any) -> int:
    """The copilot's wording varies, the same way each time for the same flight (replays, tests)."""
    callsign = getattr(getattr(getattr(engine, "state", None), "flight", None), "callsign", None)
    return sum(map(ord, str(callsign))) if callsign else 0


def _describe(command: SimCommand) -> str:
    name = getattr(command, "name", "") or type(command).__name__
    value = getattr(command, "value", None)
    if value is None:
        value = getattr(command, "hz", None)
    return f"{name} {value}" if value not in (None, 0) else name
