"""The Pilot Monitoring: hears the pilot on the intercom, works the aircraft, and says what it did.

Every command goes the same way: the safety rules (``actions.safety``) first, then the sim commands, then a look at
the sim within ``CHECK_S``: "Flaps 2." once the handle shows it, "Flaps 2 didn't take, check it." if it never does.
A command the rules want confirmed waits up to ``CONFIRM_S`` for "confirm". A radio call said on the intercom by
mistake is offered to be sent ("That was on the intercom. Send it?").

Pure: ``observe`` takes each bus event (with its ``t``) and returns what to publish (``CrewSpeech``, ``CrewAction``,
the copilot's ``Transcript`` on the radio) and the ``SimCommand``s to send, so a recording replays through it the
same way.
"""

import logging
from dataclasses import dataclass
from typing import Any

from localtc.crew.actions import Cockpit, Plan, plan, safety
from localtc.crew.commands import Command, parse
from localtc.crew.profiles import Profile, for_aircraft, load_all
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
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


class PilotMonitoring:
    def __init__(self, engine: Any = None, *, profiles: list[Profile] | None = None) -> None:
        self.engine = engine  # the ATC engine, read only: what's been cleared, the stations, the callsign
        self.profiles = profiles if profiles is not None else load_all()
        self.cockpit = Cockpit(profile=for_aircraft(self.profiles, "", ""))
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
        elif isinstance(ev, IntercomHeard):
            self._read_clearance()
            out += self._heard(ev.t, ev.text)
        out += self._tick(ev.t)
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
            return [self._say(t, "Say again?", "reply")]
        out: list[Any] = []
        for cmd in commands:
            out += self._command(t, cmd)
        return out

    def _command(self, t: float, cmd: Command) -> list[Any]:
        if cmd.action == "check":
            return [self._say(t, "Loud and clear.")]
        if cmd.action in ("yes", "no"):
            return self._answer(t, cmd.action == "yes")
        verdict = safety(cmd, self.cockpit)
        if verdict.kind == "refuse":
            return [self._say(t, verdict.reason, "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=verdict.reason)]
        if verdict.kind == "confirm":
            self._confirm = (t + CONFIRM_S, cmd)
            return [self._say(t, verdict.reason, "confirm"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="confirm", detail=verdict.reason)]
        return self._do(t, cmd)

    def _answer(self, t: float, yes: bool) -> list[Any]:
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
                out += [self._say(t, w.plan.done, "done", w.plan.done_spoken),
                        CrewAction(t=t, action=w.plan.action, value=w.plan.value, outcome="done")]
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


def _describe(command: SimCommand) -> str:
    name = getattr(command, "name", "") or type(command).__name__
    value = getattr(command, "value", None)
    if value is None:
        value = getattr(command, "hz", None)
    return f"{name} {value}" if value not in (None, 0) else name
