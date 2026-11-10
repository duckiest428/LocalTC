"""Cabin incidents: now and then a real problem behind the door, played by the cabin crew, and the captain's call what to do.

Off unless asked for (``[cabin] incidents``: off, rare, normal, frequent). When one comes is decided, not rolled at the
moment: the flight's chances (``slots``: every few minutes of boarding and deboarding, every 20 of the cruise) are each
looked at once, and a seeded hash of the flight and the chance says yes or no. The same flight flown or replayed again has
the same incidents.

The odds come from the flight (``rates``): how long it is, how many are on board, the weather on the way, how late it's
running, whether the passengers are boarding. Each incident keeps its reasons (``why``). At most one at a time, an hour apart
at the least, one a flight (two on "frequent").

They only start at a quiet moment, at the stand or in the cruise, and never with an emergency running. What follows waits
for the same: below the sterile phases only a worsening fire is called in, and it says why it's calling.

The captain decides: carry on, get assistance, divert, or declare. The cabin crew say what they see and what they'll do;
the ATC, Airport Ops and the emergency services hear of it through ``CabinIncident``; and it ends with a recorded outcome.
"""

import zlib
from dataclasses import dataclass
from typing import Any

from localtc.sim_api import CabinIncident

FREQUENCY = {"off": 0.0, "rare": 0.35, "normal": 1.0, "frequent": 3.0}
MOST = {"off": 0, "rare": 1, "normal": 1, "frequent": 2}
COOLDOWN_S = 3600.0
CRUISE_SLOT_S = 1200.0
CRUISE_FIRST_S = 900.0
CRUISE_LAST_NM = 200.0  # no new incident this near the destination
GATE_SLOT_S = 300.0
REMIND_S = 150.0
NO_ANSWER_S = 360.0
UPDATE_S = 420.0
KINDS = ("medical", "unruly", "smoke", "suspicious", "lavatory", "gate_assist")
QUIET = ("PARKED", "CRUISE")
STERILE = ("PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF", "APPROACH", "LANDING", "TAXI_IN")
DECISIONS = ("continue", "assist", "divert", "escalate")
SEVERITIES = (("minor", 0.6), ("moderate", 0.9), ("serious", 1.0))
ASK = "Do you want us to carry on, get assistance, divert, or declare an emergency?"
ASK_GROUND = "Do you want us to carry on, or get assistance out to the aircraft?"
HEARD = (("escalate", r"\b(?:declare|mayday|pan[- ]?pan|emergency|escalate)\b"),
         ("divert", r"\b(?:divert\w*|nearest (?:suitable )?airport|land (?:as soon|at the nearest)|put (?:it|us|her) down)\b"),
         ("assist", r"\b(?:assist\w*|medics?|paramedics?|police|security|maintenance|engineers?|someone to meet|meet (?:us|the aircraft)|ambulance|fire (?:crew|service)s?|wheelchair)\b"),
         ("continue", r"\b(?:carry on|continue|keep going|press on|monitor|handle it|you've got it|proceed|as planned|stay on course)\b"))


@dataclass(frozen=True)
class Inputs:
    """What decides the odds."""

    phase: str = ""
    planned_min: float = 0.0
    seats: int = 0
    pax: int = 0
    rough: bool = False  # bad weather on the way: turbulence, thunderstorms
    late_min: float = 0.0
    boarding: bool = False  # the turnaround: passengers on the move
    deboarding: bool = False
    level_for_s: float = 0.0  # in the cruise this long
    to_go_nm: float | None = None
    busy: bool = False  # an emergency, or something else that mustn't be interrupted


def rates(i: Inputs) -> dict[str, tuple[float, list[str]]]:
    """Each kind's chance at this slot, and what made it that."""
    size = min(2.0, max(0.5, (i.pax or i.seats or 150) / 180.0))
    people = f"{i.pax or i.seats} on board"
    long_haul = i.planned_min >= 300
    length = f"a {i.planned_min / 60:.1f} hour flight" if i.planned_min else "the flight"
    out: dict[str, tuple[float, list[str]]] = {}
    if i.boarding or i.deboarding:
        out["gate_assist"] = (0.03 * size, [people, "passengers on the move at the gate"])
        if i.boarding:
            late = 1.6 if i.late_min >= 15 else 1.0
            out["unruly"] = (0.006 * size * late, [people, *(["running late"] if late > 1 else []), "boarding"])
            out["suspicious"] = (0.005 * size, [people, "boarding"])
    elif i.phase == "CRUISE":
        rough = 1.3 if i.rough else 1.0
        length_factor = 1.3 if long_haul else 1.0
        out["medical"] = (0.012 * size * rough * length_factor, [people, length, *(["rough air on the way"] if i.rough else [])])
        late = 1.5 if i.late_min >= 20 else 1.0
        out["unruly"] = (0.006 * size * late * length_factor, [people, length, *(["a late departure"] if late > 1 else [])])
        wide = i.seats >= 230
        out["smoke"] = (0.003 * (1.3 if wide else 1.0), ["a wide body's galleys" if wide else "the galleys and the ovens"])
        out["suspicious"] = (0.002 * size, [people])
        out["lavatory"] = (0.010 * size * (1.5 if long_haul else 1.0), [people, length])
    return out


def _unit(*parts: Any) -> float:
    return zlib.crc32("|".join(str(p) for p in parts).encode()) / 0xFFFFFFFF


def slot_of(i: Inputs, t_in_service: float) -> str | None:
    """The chance this moment belongs to, or None."""
    if i.boarding:
        return f"boarding:{int(t_in_service // GATE_SLOT_S)}"
    if i.deboarding:
        return f"deboarding:{int(t_in_service // GATE_SLOT_S)}"
    if i.phase == "CRUISE" and i.level_for_s >= CRUISE_FIRST_S and (i.to_go_nm is None or i.to_go_nm > CRUISE_LAST_NM):
        return f"cruise:{int(i.level_for_s // CRUISE_SLOT_S)}"
    return None


# What each is, in the crew's words: severity -> the report; then what each decision leads to.
PLAYS: dict[str, dict[str, Any]] = {
    "medical": {
        "what": "a medical problem on board", "services": "paramedics",
        "report": {"minor": "Captain, a passenger in row {row} has fainted. They've come round and we're keeping an eye on them.",
                   "moderate": "Captain, a passenger in row {row} has chest pains. We've put out a call for a doctor and started oxygen.",
                   "serious": "Captain, a passenger in row {row} has collapsed and isn't responding well. We have a doctor with them and the defibrillator out."},
        "continue": "Understood, we'll stay with them and let you know of any change.",
        "assist": "Thanks, Captain. We'll keep them comfortable until the paramedics meet us.",
        "divert": "Thank you, Captain. We'll tell the family and get them ready for the paramedics.",
        "escalate": "Thank you, Captain. We'll keep working on them. Every minute counts.",
        "worse": "Captain, they aren't getting better. The doctor would really like them on the ground soon.",
        "better": "Captain, good news: they're sitting up and talking. Colour's come back.",
        "ended": {"continue": "seen by a doctor after landing", "assist": "met by paramedics on arrival",
                  "divert": "taken to hospital from the diversion airport", "escalate": "taken to hospital after a priority landing"},
    },
    "unruly": {
        "what": "a disruptive passenger", "services": "police",
        "report": {"minor": "Captain, we've got a passenger in row {row} getting loud with the people around them. We've had a word.",
                   "moderate": "Captain, a passenger in row {row} has been drinking and is refusing to follow instructions. We've stopped serving them.",
                   "serious": "Captain, a passenger in row {row} has become aggressive and grabbed one of the crew. We've got help holding them."},
        "continue": "Will do. We'll keep them apart from the others and keep you posted.",
        "assist": "Thanks, Captain. We'll keep them calm until the police meet the aircraft.",
        "divert": "Understood, Captain. We'll have them restrained and ready to be taken off.",
        "escalate": "Understood. We'll keep the flight deck door secure and the cabin calm.",
        "worse": "Captain, it's getting worse back here. They're threatening the other passengers.",
        "better": "Captain, they've calmed down and gone to sleep. It's quiet now.",
        "ended": {"continue": "calmed by the crew and warned", "assist": "taken off by police on arrival",
                  "divert": "removed by police at the diversion airport", "escalate": "removed by police after a priority landing"},
    },
    "smoke": {
        "what": "smoke in the cabin", "services": "the fire crews",
        "report": {"minor": "Captain, there's a burning smell near the aft galley. No smoke to see. We've switched the ovens off and we're looking for it.",
                   "moderate": "Captain, there's light smoke from an oven in the aft galley. We've pulled its breaker and have an extinguisher out.",
                   "serious": "Captain, we've got smoke in the cabin, around row {row}, and we can't find where it's coming from. Extinguishers are ready."},
        "continue": "Understood. We'll stay on it and tell you the moment it changes.",
        "assist": "Thank you, Captain. We'll keep watching it till the fire crews have had a look.",
        "divert": "Thank you, Captain. We'll get the cabin ready for landing.",
        "escalate": "Thank you, Captain. Cabin's being prepared.",
        "worse": "Captain, I know you're busy, but I have to call: the smoke's getting worse.",
        "better": "Captain, the smell's gone since the oven went off. Nothing further showing.",
        "ended": {"continue": "no further smoke, the oven left off", "assist": "checked by the fire crews on arrival",
                  "divert": "checked by the fire crews at the diversion airport", "escalate": "met by the fire crews on a priority landing"},
    },
    "suspicious": {
        "what": "a security concern on board", "services": "security",
        "report": {"minor": "Captain, a passenger in row {row} keeps going back and forth to the door area. Probably nothing, but I wanted you to know.",
                   "moderate": "Captain, a passenger in row {row} is taking pictures of the crew and asking about the flight deck door. We're keeping an eye on them.",
                   "serious": "Captain, there's a bag in the lockers near row {row} nobody will say is theirs. We've moved people away from it."},
        "continue": "Understood. We'll keep watching and tell you if anything changes.",
        "assist": "Thanks, Captain. We'll carry on as normal until security meets the aircraft.",
        "divert": "Understood, Captain. We'll keep it calm and quiet back here.",
        "escalate": "Understood. The flight deck door stays locked.",
        "worse": "Captain, they won't sit down when asked. I'm not comfortable with this.",
        "better": "Captain, we've talked to them. Nervous flyer, nothing more.",
        "ended": {"continue": "nothing found", "assist": "spoken to by security on arrival",
                  "divert": "met by security at the diversion airport", "escalate": "met by security on a priority landing"},
    },
    "lavatory": {
        "what": "a cabin system fault", "services": "maintenance",
        "report": {"minor": "Captain, the forward lavatory won't flush. We've locked it off and sent people to the others.",
                   "moderate": "Captain, two of the lavatories are out and the aft one's starting to leak onto the floor. We've shut its water off.",
                   "serious": "Captain, we've lost most of the lavatories and there's a long way still to go. It's getting uncomfortable back here."},
        "continue": "No problem. We'll make the best of it.",
        "assist": "Thanks, Captain. We'll write it up for maintenance to meet us.",
        "divert": "Understood, Captain. I'll let the passengers know.",
        "escalate": "Understood, Captain.",
        "worse": "Captain, the queue's getting long and people are grumbling. Nothing more than that.",
        "better": "Captain, we've got one going again.",
        "ended": {"continue": "written up in the cabin log", "assist": "fixed by maintenance on arrival",
                  "divert": "fixed by maintenance at the diversion airport", "escalate": "fixed by maintenance on arrival"},
    },
    "gate_assist": {
        "what": "a passenger needing assistance", "services": "the airport's assistance team",
        "report": {"minor": "Captain, one of our passengers needs a wheelchair and it hasn't come. We've called for it.",
                   "moderate": "Captain, an elderly passenger on the jetway has had a dizzy turn. We've sat them down.",
                   "serious": "Captain, a passenger has fallen on the jetway and hurt their leg. We've stopped people coming past them."},
        "continue": "Understood. We'll work round them, it'll slow us down a bit.",
        "assist": "Thanks, Captain. Airport Ops are sending someone out.",
        "divert": "We're at the gate, Captain, so that'll be assistance.",
        "escalate": "Understood. Airport Ops are calling for the paramedics.",
        "worse": "Captain, it's taking a while, it's holding the line up.",
        "better": "Captain, they're all sorted. Things are moving again.",
        "ended": {"continue": "helped by the crew", "assist": "helped by the airport's team",
                  "divert": "helped by the airport's team", "escalate": "treated by paramedics at the gate"},
    },
}
GATE_DELAY_MIN = {"minor": 2.0, "moderate": 4.0, "serious": 7.0}  # what it costs the boarding, and why
ON_THE_GROUND_ENDS = {"medical", "smoke"}  # these last until the aircraft's down


@dataclass
class Incident:
    id: str
    kind: str
    severity: str
    started: float
    why: str
    row: int
    phase: str
    stage: str = "reported"  # reported, deciding, handling, resolved
    decision: str = ""
    decided_t: float | None = None
    reminded: bool = False
    updated: bool = False
    asked_again: bool = False
    ends_at: float | None = None


class IncidentDirector:
    """What the cabin crew go by. ``tick`` and ``decide`` return what's to be said and recorded: (words, CabinIncident)."""

    def __init__(self, frequency: str, seed: int, callsign: str = "") -> None:
        self.frequency, self.seed, self.callsign = frequency, seed, callsign
        self.current: Incident | None = None
        self.history: list[Incident] = []
        self._looked: set[str] = set()
        self._last_end = -1e12
        self.service_started: dict[str, float] = {}

    @property
    def on(self) -> bool:
        return FREQUENCY.get(self.frequency, 0.0) > 0.0

    @property
    def waiting_for_captain(self) -> bool:
        return self.current is not None and self.current.stage in ("reported", "deciding")

    def _event(self, i: Incident, t: float, stage: str, *, outcome: str = "", delay_s: float = 0.0) -> CabinIncident:
        return CabinIncident(t=t, incident=i.id, kind=i.kind, severity=i.severity, stage=stage, decision=i.decision, outcome=outcome,
                             why=i.why, phase=i.phase, delay_s=delay_s, airborne=i.kind != "gate_assist")

    def _choose(self, i: Inputs, slot: str) -> tuple[str, list[str]] | None:
        scale = FREQUENCY.get(self.frequency, 0.0)
        best: tuple[float, str, list[str]] | None = None
        for kind, (rate, why) in rates(i).items():
            ratio = _unit(self.seed, self.callsign, slot, kind) / max(rate * scale, 1e-9)
            if ratio < 1.0 and (best is None or ratio < best[0]):
                best = (ratio, kind, why)
        return None if best is None else (best[1], best[2])

    def _start(self, i: Inputs, t: float, slot: str) -> tuple[str, Any] | None:
        got = self._choose(i, slot)
        if got is None:
            return None
        kind, why = got
        draw = _unit(self.seed, self.callsign, slot, "severity")
        severity = next(name for name, top in SEVERITIES if draw <= top)
        row = 4 + int(_unit(self.seed, self.callsign, slot, "row") * max(10, ((i.seats or 150) // 6 - 5)))
        why_text = f"{self.frequency} frequency, {', '.join(why)}"
        incident = Incident(f"{kind}:{slot}", kind, severity, t, why_text, row, i.phase)
        self.current = incident
        ask = ASK_GROUND if i.phase == "PARKED" else ASK
        return f"{PLAYS[kind]['report'][severity].format(row=row)} {ask}", self._event(incident, t, "reported")

    def tick(self, i: Inputs, t: float, in_service_s: float = 0.0) -> list[tuple[str, Any]]:
        if not self.on:
            return []
        if self.current is not None:
            return self._follow(i, t)
        if i.busy or t - self._last_end < COOLDOWN_S or len(self.history) >= MOST.get(self.frequency, 0) or i.phase not in QUIET:
            return []
        slot = slot_of(i, in_service_s)
        if slot is None or slot in self._looked:
            return []
        self._looked.add(slot)
        started = self._start(i, t, slot)
        return [started] if started is not None else []

    def _quiet(self, i: Inputs) -> bool:
        return i.phase not in STERILE

    def _follow(self, i: Inputs, t: float) -> list[tuple[str, Any]]:
        c = self.current
        out: list[tuple[str, Any]] = []
        if c.stage in ("reported", "deciding"):
            if not self._quiet(i):
                return []  # (the sterile part of the flight: it can wait for a moment you can answer)
            if not c.reminded and t - c.started >= REMIND_S:
                c.reminded = True
                out.append(("Captain, sorry to chase. I still need your call on this. " + (ASK_GROUND if i.phase == "PARKED" else ASK), None))
            elif t - c.started >= NO_ANSWER_S:
                out += self.decide("continue", t, i, answered=False)
            return out
        if c.stage != "handling":
            return out
        play = PLAYS[c.kind]
        if not c.updated and c.decided_t is not None and t - c.decided_t >= UPDATE_S:
            bad = c.severity == "serious" and c.decision == "continue" or c.severity == "moderate" and c.kind == "smoke" and c.decision == "continue"
            if bad and not self._quiet(i) and not (c.kind == "smoke" and i.phase not in ("TAKEOFF", "LANDING")):
                return out  # held for a moment that isn't critical; a worsening fire is the one thing that isn't
            c.updated = True
            out.append((play["worse" if bad else "better"] + (f" {ASK}" if bad else ""), self._event(c, t, "update", outcome="worse" if bad else "better")))
            if bad and not c.asked_again:
                c.asked_again = True
                c.stage, c.started, c.reminded = "deciding", t, False
                return out
        if c.ends_at is not None and t >= c.ends_at and self._quiet(i):
            out.append(self._end(c, t))
        return out

    def _end(self, c: Incident, t: float) -> tuple[str, Any]:
        c.stage = "resolved"
        outcome = PLAYS[c.kind]["ended"].get(c.decision, "handled by the crew")
        self.history.append(c)
        self.current = None
        self._last_end = t
        return f"Captain, that's all sorted: {outcome}.", self._event(c, t, "resolved", outcome=outcome)

    def arrived(self, t: float) -> list[tuple[str, Any]]:
        """The aircraft's at the gate: what was waiting for that is over."""
        c = self.current
        if c is None:
            return []
        if c.stage in ("reported", "deciding"):
            c.decision = "continue"
        return [self._end(c, t)]

    def decide(self, decision: str, t: float, i: Inputs, *, answered: bool = True) -> list[tuple[str, Any]]:
        """The captain's call (or, unanswered, the crew carrying on as best they can)."""
        c = self.current
        if c is None:
            return []
        on_ground = i.phase in ("PARKED", "PUSHBACK", "TAXI_OUT", "TAXI_IN") or c.kind == "gate_assist"
        if decision == "divert" and on_ground:
            decision = "assist"
        c.decision, c.decided_t, c.stage = decision, t, "handling"
        play = PLAYS[c.kind]
        delay = GATE_DELAY_MIN[c.severity] * 60.0 * (0.6 if decision == "assist" else 1.0) if c.kind == "gate_assist" else 0.0
        reason = f"{play['what']}, {c.severity}, {'with' if decision == 'assist' else 'without'} the airport's help" if delay else c.why
        event = self._event(c, t, "decided", delay_s=delay)
        if delay:
            event = CabinIncident(**{**{f: getattr(event, f) for f in event.__struct_fields__}, "why": reason})
        # How long it runs: what it is, not luck. Those that need the ground end when it's reached.
        if c.kind not in ON_THE_GROUND_ENDS and decision == "continue":
            c.ends_at = t + {"minor": 480.0, "moderate": 900.0, "serious": 1500.0}[c.severity]
        elif c.kind == "gate_assist":
            c.ends_at = t + delay
        words = play[decision] if answered else "Haven't heard from you, Captain, so we'll carry on and do what we can. " + play["continue"]
        return [(words, event)]


def decision_in(text: str) -> str | None:
    """What the captain's words decide, or None."""
    import re

    return next((d for d, words in HEARD if re.search(words, text, re.I)), None)
