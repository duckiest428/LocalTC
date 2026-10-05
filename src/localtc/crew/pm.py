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
from dataclasses import dataclass
from typing import Any

from localtc.atc_core import region as regions
from localtc.config import PlanPerf
from localtc.crew.actions import Cockpit, Plan, plan, safety
from localtc.crew.answers import Picture, answer, facts
from localtc.crew.commands import Command, parse
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
    SimCommand,
    Transcript,
)

log = logging.getLogger(__name__)

CHECK_S = 3.0  # the sim shows a command within this, or it didn't take
CONFIRM_S = 10.0  # a "confirm?" waits this long for the answer
OFFER_S = 15.0  # "send it?" for a radio call said on the intercom
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

    @property
    def profile(self) -> Profile:
        return self.cockpit.profile

    def observe(self, ev: Any) -> list[Any]:
        """``ev`` happened; returns the events to publish and the sim commands to send, in order."""
        out: list[Any] = []
        if isinstance(ev, OwnshipState):
            self.cockpit.own = ev
            self._read_clearance()
        elif isinstance(ev, AircraftSystems):
            self.cockpit.systems = ev
        elif isinstance(ev, AircraftIdentity):
            profile = for_aircraft(self.profiles, ev.title, ev.atc_model)
            if profile is not self.cockpit.profile:
                log.info("Copilot: %s profile for %s", profile.name, ev.title or ev.atc_model)
                self.cockpit.profile = profile
        elif isinstance(ev, AtcTransmission):
            self.picture.last_atc = ev
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
        out.append(CrewSpeech(t=t, text=call.text, spoken=call.spoken, kind=kind))
        if call.radio:  # then on the radio: the copilot asking ATC
            out.append(Transcript(t=t, text=call.radio, source="copilot"))
        return out

    # --- the pilot ------------------------------------------------------------------------------------------------

    def _heard(self, t: float, text: str) -> list[Any]:
        if not text.strip():
            return []
        commands = parse(text)
        if not commands:
            if self._radio_call(text):
                self._offer = (t + OFFER_S, text)
                return [self._say(t, "That was on the intercom. Want me to send it?", "confirm")]
            if (said := answer(text, self.picture)) is not None:
                return [self._say(t, said)]
            return self._ask_model(t, text)
        out: list[Any] = []
        for cmd in commands:
            out += self._command(t, cmd)
        return out

    def _ask_model(self, t: float, text: str) -> list[Any]:
        """What neither the grammar nor the data answers could take: the model's reply, or its reading of a command,
        which waits for the pilot's "confirm"."""
        if self.model is None:
            return [self._say(t, "Say again?")]
        more = None
        if getattr(self.model.backend, "rich", False) and self.engine is not None and hasattr(self.engine, "_flight_more"):
            more = self.engine._flight_more(None, t)  # a cloud model: the whole flight, not just the copilot's facts
        reading, exchanges = self.model.ask(t, text, facts(self.picture), more=more)
        if reading is None:
            return [*exchanges, self._say(t, "Say again?")]
        if reading.command is None:
            return [*exchanges, self._say(t, reading.reply)]
        cmd = reading.command
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
                out.append(CrewAction(t=t, action=w.plan.action, value=w.plan.value, outcome="done"))
            elif t >= w.deadline:
                what = w.plan.done.rstrip(".").replace(" set", "")
                out += [self._say(t, f"{what} didn't take, check it.", "alert"),
                        CrewAction(t=t, action=w.plan.action, value=w.plan.value, outcome="failed",
                                   detail=f"the sim didn't show it within {CHECK_S:.0f} s")]
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

    @staticmethod
    def _say(t: float, text: str, kind: str = "reply", spoken: str = "") -> CrewSpeech:
        return CrewSpeech(t=t, text=text, spoken=spoken, kind=kind)


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
