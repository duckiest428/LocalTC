"""The language model on the intercom: the copilot understanding the captain and putting its own words to things.

``[crew] mode``, like ATC's modes (``[llm] mode``):
- ``scripted``: the listed commands and the common questions straight from the flight's data; the model only for what
  nothing else understood. The copilot's own calls are its fixed words.
- ``semi``: the listed commands from the grammar; everything else the captain says (questions included) goes to the
  model, with the data's answer among its facts.
- ``mostly_llm``: only a short, clear command ("flaps two", "gear up") skips the model; everything else is the
  model's. The copilot's routine calls (briefings, relays, reminders, the ATIS) are put in its own words.
- ``llm``: the model reads every word the captain says; a command it reads is still checked against the grammar, and
  one the grammar didn't read the same is confirmed first. Every call but the safety ones is in its own words.
- ``off``: no model at all.
``auto`` picks ``mostly_llm`` with a cloud model, ``scripted`` with the one on this PC (``effective_mode``).

Whatever the model says is checked: a number in a reply must be in the facts or the captain's words, a command's value
must have been said, a reworded call keeps every number and name of the original. What fails isn't said: the captain
hears "say again?", or the call in its fixed words. The model is given the conversation so far and what's happened on
the flight deck, so "no, I said the clearance" and "we're still at the gate" make sense to it.
"""

import json
import logging
import re
from collections import deque
from dataclasses import dataclass

from localtc.atc_core.llm.backend import LlmRequest, waits
from localtc.crew.answers import numbers_in
from localtc.crew.commands import Command
from localtc.sim_api import LlmExchange

log = logging.getLogger(__name__)

MODES = ("off", "scripted", "semi", "mostly_llm", "llm")
ACTIONS = ("gear", "flaps", "light", "spoilers", "autopilot", "ap_mode", "autothrottle", "heading", "altitude",
           "speed", "vs", "squawk", "com_active", "com_standby", "com_swap", "altimeter", "parking_brake")
SYSTEM = """You are the first officer, pilot monitoring, in an airliner cockpit, talking with the captain on the \
intercom. Sound like a real, calm, experienced airline first officer: short, plain, accurate. A colleague, not an \
assistant and not an actor.

How you talk:
- One short sentence, two at most. Spoken words, no lists. Don't put "Captain" in every line.
- Answer what was asked, nothing more. Never prompt the captain to do the next thing ("ground's next", "ready when \
you are", "let's brief"), never suggest calling ATC, never volunteer the cockpit state.
- A remark or a check ("engine two started", "beautiful sunset", "flaps one, check"): a word or two ("Check.", \
"Copy.", "Yeah, nice."). Don't repeat facts back.
- The captain telling you what HE did or is doing ("I've deployed the speedbrakes", "I'm going to slow down", \
"applying takeoff thrust") is not a request: acknowledge it ("Check."), never act on it.
- {facts_rule}
- Only what the facts show about the aircraft. Never say something is armed, set, captured, on the glideslope, \
stable, fine or safe unless the facts say exactly that. If the facts say a thing can't be read, say you can't see it.
- You can't see the weather radar, outside, or anything not in the facts. Asked about weather or whether things will \
be fine, give what the facts have (the ATIS, the wind) and say what you can't tell; don't reassure.
- Where we are is the "position" fact: use it word for word; never guess a place.
- Know the phase from the facts; never say we're cleared or doing something the facts don't say.
- Use the conversation so far: if the captain corrects you, take the correction.

Doing things: if the captain asks you to set, move or switch something, answer with kind "command" and the action:
gear (value up|down), flaps (value: the setting, e.g. 1, 2, full, up), light (target landing|taxi|strobe|beacon|nav|logo, \
value on|off), spoilers (value arm|disarm|extend|retract), autopilot (value on|off), ap_mode (target \
heading|nav|approach|altitude|vs|flc, value on), autothrottle (value on|off), heading (value degrees), altitude \
(value feet), speed (value knots), vs (value feet per minute, negative down), squawk (value 4 digits), com_active or \
com_standby (value MHz), com_swap, altimeter (value inches like 29.92, or target hpa and value like 1013), \
parking_brake (value on|off).
Only those, and only when the captain names the thing (gear, flaps, lights...). Not on the list (the autobrake, the \
flight director, the packs, anti-ice, the seatbelt sign, the fuel pumps, the EFB): say it's the captain's, or that \
you can't from your seat. Never pick another action instead, never claim you did something you didn't return as a \
command. A value you weren't given is a reply, not a command. Words that make no sense: kind "reply", ask "Say \
again?". Otherwise kind "reply". Your words go in "reply" either way."""
SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["reply", "command"]},
        "action": {"type": "string", "enum": ["", *ACTIONS]},
        "value": {"type": "string"},
        "target": {"type": "string"},
        "reply": {"type": "string"},
    },
    "required": ["kind", "reply"],
}
EXAMPLES = (
    ('Facts:\nphase: cruise\nfuel: 41,300 pounds\nendurance: 6 hours 10 minutes\n\nCaptain: "are we good on gas?"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "Yes, 41,300 pounds, a bit over six hours at this burn."}'),
    ('Facts:\nphase: approach\naltitude: 4,980 feet\ngear: up\n\nCaptain: "drop the gear for me would you"',
     '{"kind": "command", "action": "gear", "value": "down", "target": "", "reply": "Gear down."}'),
    ('Facts:\nphase: parked\n\nCaptain: "engine two started"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "Check."}'),
    ('Facts:\nphase: parked\n\nCaptain: "go ahead and turn on your flight director"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "Flight director on my side is yours to switch, I can\'t from here."}'),
    ('Facts:\nphase: cruise\naltitude: 35,000 feet\n\nCaptain: "how\'s the weather looking at the destination"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "No ATIS for them yet, I\'ll get it once we\'re closer."}'),
    ('Facts:\nphase: arrival\nspoilers: stowed\n\nCaptain: "we\'re high, I\'ve deployed the speed brakes"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "Check."}'),
    ('Facts:\nphase: cruise\nposition: about 20 miles north-northeast of Charlotte, North Carolina\n\n'
     'Captain: "what city are we over"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "About 20 miles north-northeast of Charlotte."}'),
    ('Facts:\nphase: arrival\ndestination weather: information D, wind 100 at 6, runway 18R in use\n'
     'what I can\'t see: the weather radar, the view outside\n\nCaptain: "look at that weather, are we going to be alright?"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "Can\'t see the radar from here; the ATIS has '
     'wind 100 at 6 for 18R."}'),
)
WORD_VALUES = {"up", "down", "on", "off", "arm", "disarm", "extend", "retract", "full"}
REWORD_SYSTEM = """You are an airline first officer saying something to the captain on the intercom. Say the given \
words your own way, the way a person would say them, short and natural. Keep every number, setting, frequency, \
runway, procedure and name exactly as given (you may say them the usual way: "FL280", "29.92"). Don't add \
anything that isn't in the words, don't drop anything. Answer with JSON: {"reply": "..."}."""
REWORD_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}
HISTORY = 10  # intercom turns the model is shown
EVENTS = 12  # flight-deck events the model is shown


@dataclass(frozen=True)
class Reading:
    reply: str
    command: Command | None = None


FACTS_RULE = "Use ONLY the facts given; if the answer isn't in them, say you don't have it. Never invent numbers."
BEYOND_RULE = ("Use the facts first. Where they say nothing about the flight, you may answer from what a first officer "
               "knows about flying and this aircraft in general; if you don't know, say so. Never contradict the facts, "
               "never invent anything about this flight (where we are, the weather, what's set).")


def system(beyond_facts: bool = False) -> str:
    return SYSTEM.replace("{facts_rule}", BEYOND_RULE if beyond_facts else FACTS_RULE)


def effective_mode(mode: str, backend) -> str:
    """``auto``: mostly the model's with a cloud model (quick, and good with words), scripted with a local one (slow on
    a PC that's running the sim). The older settings ("full", "questions") are the scripted use they always were."""
    if mode in ("full", "questions"):
        return "scripted"
    if mode == "auto":
        return "mostly_llm" if getattr(backend, "rich", False) else "scripted"
    return mode if mode in MODES else "scripted"


class CrewModel:
    def __init__(self, backend, *, mode: str = "auto", timeout_s: float = 8.0, patience_s: float = 20.0,
                 beyond_facts: bool = False) -> None:
        self.backend, self.timeout_s, self.patience_s = backend, timeout_s, patience_s
        self.setting = mode
        self.beyond_facts = beyond_facts  # [crew] beyond_facts: replies may go past what the copilot knows
        self.history: deque[tuple[str, str]] = deque(maxlen=HISTORY)  # ("Captain" or "You", words)
        self.events: deque[tuple[float, str]] = deque(maxlen=EVENTS)  # what happened on the flight deck

    @property
    def mode(self) -> str:
        return effective_mode(self.setting, self.backend)

    @mode.setter
    def mode(self, value: str) -> None:
        self.setting = value

    # --- what the model is told --------------------------------------------------------------------------------------

    def heard(self, who: str, text: str) -> None:
        """A line on the intercom, the captain's or the copilot's own, for the model's memory."""
        if text.strip():
            self.history.append((who, text.strip()))

    def event(self, t: float, text: str) -> None:
        """Something that happened on the flight deck (an engine started, the pushback, a phase), for the model."""
        if not self.events or self.events[-1][1] != text:
            self.events.append((t, text))

    def _context_lines(self, t: float) -> str:
        parts = []
        if self.events:
            parts.append("What's happened lately:\n" + "\n".join(
                f"- {max(0, round((t - when) / 60))} min ago: {what}" for when, what in self.events))
        if self.history:
            parts.append("The conversation so far:\n" + "\n".join(f'{who}: "{words}"' for who, words in self.history))
        return "\n\n".join(parts)

    # --- asking -------------------------------------------------------------------------------------------------------

    def ask(self, t: float, text: str, facts: dict[str, str],
            more: dict[str, str] | None = None) -> tuple[Reading | None, list[LlmExchange]]:
        """The model's answer to ``text`` (checked), and the exchange to record; None when it has nothing usable.
        ``more``: the rest of the flight, for a model that can take it (a cloud one); a reply may use it too."""
        if self.mode == "off" or self.backend is None:
            return None, []
        context = self._context_lines(t)
        prompt = ("Facts:\n" + "\n".join(f"{k}: {v}" for k, v in facts.items())
                  + (f"\n\n{context}" if context else "") + f'\n\nCaptain: "{text}"')
        messages = tuple(m for q, a in EXAMPLES for m in (("user", q), ("assistant", a))) + (("user", prompt),)
        request = LlmRequest("crew", system(self.beyond_facts), messages, SCHEMA, max_tokens=120,
                             context="\n".join(f"- {k}: {v}" for k, v in (more or {}).items()))
        known = {**(more or {}), **facts}  # what the reply is checked against
        timeout_s, _ = waits(self.backend, self.timeout_s, self.timeout_s, self.patience_s)
        result = self.backend.complete(request, timeout_s=timeout_s)
        model = getattr(self.backend, "model", "")
        self.heard("Captain", text)
        if result.text is None:
            return None, [LlmExchange(t=t, purpose="crew", model=model, key=request.key(model), prompt=prompt,
                                      response="", outcome="timeout" if result.error == "timeout" else "error",
                                      detail=result.error, latency_ms=result.latency_ms)]
        reading, why = self._check(result.text, text, known)
        exchange = LlmExchange(t=t, purpose="crew", model=model, key=request.key(model), prompt=prompt,
                               response=result.text, outcome="used" if reading is not None else "rejected", detail=why,
                               latency_ms=result.latency_ms)
        return reading, [exchange]

    def reword(self, t: float, text: str) -> tuple[str | None, list[LlmExchange]]:
        """The copilot's own call ``text`` in its own words, checked to keep every number and name; None: the fixed
        words stand (no model, the mode doesn't reword, or the model's words lost something)."""
        if self.backend is None or self.mode not in ("mostly_llm", "llm") or len(text) < 12:
            return None, []
        recent = "\n".join(f'{who}: "{words}"' for who, words in list(self.history)[-4:])
        prompt = (f"Recent on the intercom:\n{recent}\n\n" if recent else "") + f'Say: "{text}"'
        request = LlmRequest("crew_reword", REWORD_SYSTEM, (("user", prompt),), REWORD_SCHEMA, max_tokens=100)
        timeout_s, _ = waits(self.backend, min(self.timeout_s, 4.0), min(self.timeout_s, 4.0), min(self.patience_s, 6.0))
        result = self.backend.complete(request, timeout_s=timeout_s)
        model = getattr(self.backend, "model", "")
        if result.text is None:
            return None, [LlmExchange(t=t, purpose="crew_reword", model=model, key=request.key(model), prompt=prompt,
                                      response="", outcome="timeout" if result.error == "timeout" else "error",
                                      detail=result.error, latency_ms=result.latency_ms)]
        words, why = check_reworded(result.text, text)
        exchange = LlmExchange(t=t, purpose="crew_reword", model=model, key=request.key(model), prompt=prompt,
                               response=result.text, outcome="used" if words else "rejected", detail=why,
                               latency_ms=result.latency_ms)
        return words, [exchange]

    def _check(self, raw: str, said: str, facts: dict[str, str]) -> tuple[Reading | None, str]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None, "not JSON"
        reply = str(data.get("reply", "")).strip()
        if data.get("kind") == "command":
            action, value, target = (str(data.get(k, "")).strip().lower() for k in ("action", "value", "target"))
            if action not in ACTIONS:
                return None, f"no such action {action!r}"
            heard = said.lower()
            if is_report(heard):  # "I've deployed the speed brakes": what he did, not a request
                self.heard("You", "Check.")
                return Reading("Check."), "a report of the captain's own, not a request: acknowledged"
            if not re.search(ACTION_WORDS[action], heard):
                return None, f"the captain didn't name the {action.replace('_', ' ')}"
            if value and value not in WORD_VALUES and not (set(re.findall(r"\d+", value)) <= numbers_in(said) | _spoken_digits(heard)):
                return None, f"the captain didn't say {value}"
            return Reading(reply or f"{action} {value}".strip(), Command(action, value, target)), ""
        if not reply:
            return None, "no reply"
        if (claim := unsupported_claim(reply, facts)) is not None:
            return None, claim
        if not self.beyond_facts:
            known = numbers_in(said) | {n for v in facts.values() for n in numbers_in(v)}
            made_up = {n for n in numbers_in(reply) if n not in known}
            if made_up:
                return None, f"reply has {', '.join(sorted(made_up))}, which isn't in the facts"
        self.heard("You", reply)
        return Reading(reply), ""


# The words that name each action: a command read by the model must have one ("auto break off" is no autopilot).
ACTION_WORDS = {
    "gear": r"\bgear\b|\bwheels\b", "flaps": r"\bflaps?\b|\bconfig\b",
    "light": r"\blights?\b|\bbeacon\b|\bstrobes?\b|\blogo\b",
    "spoilers": r"\bspoilers?\b|speed ?brakes?", "autopilot": r"auto ?pilot|\bap\b|\ba p\b",
    "ap_mode": r"\bmode\b|\bheading\b|\bnav\b|\bapproach\b|\bloc\b|\bapp\b|vertical speed|level change|altitude hold",
    "autothrottle": r"auto ?throttle|auto ?thrust|\bathr\b|\ba thr\b", "heading": r"\bheading\b|\bhdg\b",
    "altitude": r"altitude|\blevel\b|thousand|\bfl\b|\bfeet\b", "speed": r"\bspeed\b|\bknots\b",
    "vs": r"vertical speed|per minute|\bfpm\b|\bv ?s\b", "squawk": r"squawk|transponder|\bcode\b",
    "com_active": r"frequency|\btune\b|\bcom\b|\bradio\b|\bpoint\b|decimal|\d{3}\.\d",
    "com_standby": r"standby|frequency|\btune\b|\bcom\b|\bpoint\b|decimal|\d{3}\.\d", "com_swap": r"\bswap\b|\bflip\b",
    "altimeter": r"altimeter|\bqnh\b|\bbaro|\bstandard\b|\bstd\b", "parking_brake": r"parking brake|park(?:ing)? brakes?",
}
REPORT = re.compile(r"\b(?:i've|i have|i'm|i am|i just|i'll|i will|i deployed|i set|i put|we've|we just)\b")
REQUEST = re.compile(r"\b(?:can you|could you|would you|please|go ahead and|give me|set|put|let's)\b")


def is_report(heard: str) -> bool:
    """The captain saying what he did or is doing himself, not asking for it."""
    return bool(REPORT.search(heard)) and not REQUEST.search(heard)


# Claims about the aircraft a reply may only make when the facts say so (the model's habit of reassuring: "auto land's
# armed", "we're on the glideslope", "we'll be fine").
_NEGATED = re.compile(r"\b(?:not|no|n't|isn't|aren't|can't|cannot|don't)\b")


def unsupported_claim(reply: str, facts: dict[str, str]) -> str | None:
    """Why ``reply`` says something about this flight the facts don't show (None: it doesn't)."""
    get = lambda k: str(facts.get(k, "")).lower()  # noqa: E731
    for sentence in re.split(r"(?<=[.!?;])\s+|,\s+(?:and|but)\s+", reply.replace("\u2019", "'")):
        low = sentence.lower()
        if _NEGATED.search(low):
            continue
        if re.search(r"auto ?land", low) and re.search(r"armed|set|engaged|ready|on\b", low):
            return "claims the autoland is armed (nothing shows it)"
        if re.search(r"on (?:the )?(?:glide ?slope|glide ?path)|on profile|established", low) \
                and get("glideslope") != "on it" and get("localizer") != "captured":
            return "claims we're on the glideslope (the facts don't show it)"
        if re.search(r"spoilers?\b.*\barmed|\barmed\b.*spoilers?", low) and get("spoilers") != "armed":
            return "claims the spoilers are armed (the facts don't show it)"
        if re.search(r"auto ?brakes?\b.*\b(?:set|armed|ready|low|medium|max)\b", low) and not get("autobrake"):
            return "claims the autobrake is set (the facts don't show it)"
        if re.search(r"(?:landing|full) flaps?\b.*\bset|flaps (?:full|\d)\b.*\bset", low) and "flaps" in facts \
                and not re.search(r"full|\d", get("flaps")):
            return "claims the flaps are set (the facts don't show it)"
        if re.search(r"\b(?:we'll be|we will be|we're|we are|it'll be) (?:fine|ok|okay|alright|all right|safe|good)\b"
                     r"|no problem|nothing to worry|tracking (?:well|fine|nicely)", low):
            return "reassures without facts to back it"
        if re.search(r"\b(?:over|near|miles (?:\w+ )?of|south of|north of|east of|west of)\s+[A-Z]", sentence):
            places = re.findall(r"\b(?:over|near|of)\s+((?:[A-Z][\w.'-]+\s?)+)", sentence)
            known = get("position") + " " + " ".join(str(v).lower() for v in facts.values())
            if any(place.strip().lower() not in known for place in places):
                return "names a place the facts don't give"
    return None


NAMED = re.compile(r"\b(?:[A-Z]{2,}\d*[A-Z]*|[A-Z]\d+[A-Z]?|FL\d{3}|\d{1,2}[LRC])\b")  # RADYR2, KLAS, C1, FL280, 26R


def check_reworded(raw: str, original: str) -> tuple[str | None, str]:
    """A reworded call: every number and every name (a procedure, an airport, a taxiway, a runway) of the original
    still there, and nothing new; not much longer than it was."""
    try:
        reply = str(json.loads(raw).get("reply", "")).strip()
    except (ValueError, AttributeError):
        return None, "not JSON"
    if not reply:
        return None, "no reply"
    if numbers_in(reply) != numbers_in(original):
        return None, f"numbers changed: {sorted(numbers_in(original))} -> {sorted(numbers_in(reply))}"
    missing = {n for n in NAMED.findall(original)} - {n for n in NAMED.findall(reply)}
    if missing:
        return None, f"left out {', '.join(sorted(missing))}"
    if len(reply) > max(2 * len(original), len(original) + 60):
        return None, "too long"
    return reply, ""


def _spoken_digits(text: str) -> set[str]:
    """The numbers in words as the grammar reads them ("three one zero" -> "310")."""
    from localtc.atc_core.readback.normalize import normalize

    return {t.text.split(".")[0] for t in normalize(text) if t.kind == "number"}
