"""The language model as the primary reader of pilot transmissions, with the grammar behind it.

The model gets a narrow job: fill in a small JSON form (what kind of call, which intent,
which values the pilot said) for one transmission, given only the context it needs. It
never talks to the pilot and never decides anything; the engine does that.

Its answer is trusted only as far as it can be checked:
- the form is validated (schema, enums, value formats), and an invalid answer is retried
  once with the problem named;
- every value must have been said (``grounding``), so the model can't invent a squawk; an
  answer with an invented value is treated as invalid;
- readback values are compared by the same rules as the grammar, and a value the grammar
  heard wins over the model's;
- an intent that makes no sense in the current phase is rejected.
When the model times out, is unavailable or keeps answering badly, the grammar's result is
used exactly as before the model existed.
"""

import json
import re
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from importlib import resources
from typing import Any, Literal

from localtc.atc_core.llm.backend import LlmBackend, LlmRequest
from localtc.atc_core.llm.grounding import (
    PHRASE_STEMS,
    REQUEST_WORDS,
    grounded,
    missing_cue,
)
from localtc.atc_core.llm.triggers import is_question, question_topic
from localtc.atc_core.llm.triggers import trigger as find_trigger
from localtc.atc_core.phraseology import slots as slot_types
from localtc.atc_core.phraseology import speech
from localtc.atc_core.readback.expected import expected_intents, is_expected
from localtc.atc_core.readback.extract import candidates as find_candidates
from localtc.atc_core.readback.extract import (
    normalize_runway,
    values_close,
    values_equal,
    without_callsign,
)
from localtc.atc_core.readback.intents import EMERGENCY
from localtc.atc_core.readback.interpreter import (
    GrammarInterpreter,
    Interpretation,
    InterpretContext,
    PendingReadback,
    SayAgainInterpreter,
    Status,
)
from localtc.atc_core.readback.normalize import PHONETIC, Token, normalize
from localtc.atc_core.values import Approach
from localtc.sim_api import LlmExchange

Mode = Literal["primary", "fallback", "off"]

SYSTEM = """You read one pilot radio transmission and fill in a JSON form for an air traffic control computer. \
You are not ATC and never reply to the pilot. The computer decides everything; you only say what the pilot said.

Rules:
- Report only what the PILOT said in this transmission. Never copy a value from "Recent", "Cleared", \
"Callsign" or "Readback expected" unless the pilot said it too. If the pilot said a different number, report \
the pilot's number. The callsign's digits are never a value.
- Leave out a field the pilot did not say.
- Write numbers as digits: runway "06L", frequency "120.425", squawk "5015", altitude in feet "12000" \
(flight level 240 is "24000").
- The text comes from speech recognition and has mistakes ("clear for take off" means cleared for takeoff). \
When the pilot corrects themselves ("sorry", "I mean"), report what they said last.

kind:
- "readback": the pilot repeats an ATC instruction back. If they also ask for something in the same call, \
intent says what (or topic, for a question).
- "request": the pilot asks for or reports something; intent says what.
- "question": the pilot asks ATC for information; topic says what.
- "unintelligible": you cannot tell what the pilot wants.

intent: request_ifr_clearance = asks for the IFR clearance; request_pushback = push back or push and start; \
ready_to_taxi; request_crossing = asks to cross a runway (runway); ready_for_departure = holding short or ready \
for takeoff; request_turn = a turn after departure; need_time = not ready yet, needs a moment; checkin = first \
call to a new controller, like "with you at 6000"; report_final = "5 mile final", "established"; \
position_report = a traffic pattern position, like "left downwind"; request_option = touch and go, low \
approach, stop and go; clear_of_runway; request_taxi_parking; request_altitude = asks for higher, lower or a \
new altitude; request_direct = direct to a fix or airport (fix: its name); request_vectors = vectors or a \
heading; request_runway = a different runway or approach type (runway, approach: "ILS", "LOC", "RNAV", "VOR", \
"NDB" or "VISUAL"); request_return = back to the departure airport; request_diversion = divert, to the nearest \
airport or one it names (fix); going_around = go around or missed approach; report_conditions = turbulence, \
icing or the ride (conditions: the words used); traffic_report = "traffic in sight", "looking", "negative \
contact"; report_standard = altimeter set to standard (STD, QNE, 29.92, 1013); request_flight_following; \
request_class_b; radio_check; pleasantry = small talk like "how's your day"; report_problem = a failure short \
of an emergency; say_again = asks ATC to repeat; acknowledge = roger, wilco, thanks; emergency = mayday, \
pan-pan, smoke, fire, a medical emergency or any other emergency; other = any other request.
topic (for a question): altimeter, wind, weather, runway, squawk, altitude, frequency, atis, other."""

KINDS = ["readback", "request", "question", "unintelligible"]
INTENTS = ["request_ifr_clearance", "request_pushback", "ready_to_taxi", "request_crossing", "ready_for_departure",
           "request_turn", "need_time", "checkin", "report_final", "position_report", "request_option", "clear_of_runway",
           "request_taxi_parking", "request_altitude", "request_direct", "request_vectors", "request_runway",
           "request_return", "request_diversion", "going_around", "report_conditions", "traffic_report",
           "report_standard", "request_flight_following", "request_class_b", "radio_check", "pleasantry",
           "report_problem", "say_again", "acknowledge", "emergency", "other"]
TOPICS = ["altimeter", "wind", "weather", "runway", "squawk", "altitude", "frequency", "atis", "other"]
# Readback elements the model reports; the rest (taxi route, destination, callsign) stay with the grammar.
VALUE_ELEMENTS = ("runway", "hold_short", "altitude", "cruise", "frequency", "squawk", "heading", "approach")
PHRASE_ELEMENTS = tuple(PHRASE_STEMS)
REQUEST_FIELDS = ("runway", "atis", "altitude", "fix", "approach", "conditions", "emergency", "souls", "fuel")
APPROACH_KINDS = ("ILS", "LOC", "RNAV", "GPS", "VOR", "NDB", "LDA", "SDF", "VISUAL")


class AnswerError(ValueError):
    """The model's answer can't be used; the message is shown to the model on retry."""


class IntentError(AnswerError):
    """A request whose intent the pilot's words contradict (the kind "request" itself may be right)."""


@dataclass(frozen=True)
class Answer:
    kind: str
    intent: str = ""
    topic: str = ""
    values: dict[str, Any] = field(default_factory=dict)  # typed, and every one was said
    guessed: bool = False  # not the model's answer: inferred from the words after its answers failed


# --- the prompt -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Example:
    mode: str
    phase: str
    station: str
    pilot: str
    answer: dict[str, Any]
    callsign: str = ""
    recent: tuple[str, ...] = ()
    expect: tuple[str, ...] = ()
    cleared: dict[str, str] = field(default_factory=dict)  # altitude, heading, squawk, runway, approach
    traffic: str = ""


def load_examples() -> list[Example]:
    data = tomllib.loads((resources.files("localtc.atc_core.llm") / "examples.toml").read_text(encoding="utf-8"))
    return [Example(**{**e, "expect": tuple(e.get("expect", ())), "recent": tuple(e.get("recent", ())),
                       "cleared": dict(e.get("cleared", {}))}) for e in data["example"]]


def _label(element: str) -> str:
    return element.replace("_", " ")


def _expect_line(items: list[tuple[str, str | None]]) -> str:
    if not items:
        return "none"
    return "; ".join(f"{_label(k)} {v}" if v is not None else _label(k) for k, v in items)


ROLES = {"clearance": "clearance", "delivery": "clearance", "ground": "ground", "tower": "tower", "departure": "departure",
         "approach": "approach", "center": "center", "centre": "center"}
CLEARED = ("altitude", "heading", "squawk", "runway", "approach")


def role_of(station: str | None) -> str | None:
    """ "Montreal Tower" -> "tower": what a station's name says it is."""
    last = (station or "").split()[-1:] or [""]
    return ROLES.get(last[0].lower())


def user_message(*, callsign: str | None = None, phase: str | None, station: str | None, role: str | None = None,
                 cleared: dict[str, str] | None = None, traffic: str | None = None, recent: tuple[str, ...] = (),
                 expect: list[tuple[str, str | None]], pilot: str) -> str:
    """The moment, as a small model reads best: the same keys in the same order every time, "none" when empty.
    The examples are written this way too, so the real call looks like one of them."""
    cleared = cleared or {}
    role = role or role_of(station)
    return "\n".join([
        f"Callsign: {callsign or 'unknown'}",
        f"Phase: {phase or 'unknown'}",
        f"Station: {station or 'unknown'}" + (f" ({role})" if role else ""),
        "Cleared: " + ("; ".join(f"{k} {cleared[k]}" for k in CLEARED if cleared.get(k)) or "none"),
        f"Traffic called: {traffic or 'none'}",
        "Recent: " + (" | ".join(recent) if recent else "none"),
        f"Readback expected: {_expect_line(expect)}",
        f'Pilot: "{pilot}"',
    ])


def _callsign_line(context: InterpretContext) -> str | None:
    c = context.callsign
    if c is None:
        return None
    said = speech.callsign_display(c)
    return said if said.replace(" ", "").upper() == c.ident.upper() else f"{said} ({c.ident})"


def _cleared(context: InterpretContext) -> dict[str, str]:
    out = {}
    if context.cleared_altitude_ft:
        out["altitude"] = str(context.cleared_altitude_ft)
    if context.cleared_heading:
        out["heading"] = f"{context.cleared_heading:03d}"
    if context.squawk:
        out["squawk"] = context.squawk
    if context.runway:
        out["runway"] = context.runway
    if context.approach:
        out["approach"] = context.approach
    return out


def _recent(context: InterpretContext) -> tuple[str, ...]:
    if context.recent:
        return context.recent
    return (f'ATC: "{context.last_atc}"',) if context.last_atc else ()


def _expected_items(pending: PendingReadback) -> list[tuple[str, str | None]]:
    items: list[tuple[str, str | None]] = []
    for element in (*pending.required, *pending.optional):
        if element == "callsign":
            continue
        value = pending.expected.get(element, True)
        if value is True:
            items.append((element, None))
        else:
            slot = slot_types.SLOTS.get(element)
            items.append((element, slot.display(value) if slot and isinstance(value, slot.value_type) else str(value)))
    return items


def _model_elements(pending: PendingReadback) -> list[str]:
    return [e for e in (*pending.required, *pending.optional) if e in VALUE_ELEMENTS or e in PHRASE_ELEMENTS]


def allowed_intents(context: InterpretContext | None) -> list[str]:
    """The intents the model may answer with: those that make sense to this controller in this phase
    (``readback.expected``), in the prompt's order."""
    if context is None:
        return list(INTENTS)
    fitting = set(expected_intents(context.station_role or role_of(context.station), context.phase))
    return [i for i in INTENTS if i in fitting]


def schema(pending: PendingReadback | None, context: InterpretContext | None = None) -> dict[str, Any]:
    """The answer's form. Only ``kind`` is required: the model writes the fields the pilot said and stops
    (every empty field it had to write out cost a CPU tenths of a second, a dozen of them seconds)."""
    props: dict[str, Any] = {
        "kind": {"type": "string", "enum": KINDS},
        "intent": {"type": "string", "enum": allowed_intents(context)},
        "topic": {"type": "string", "enum": TOPICS},
    }
    if pending is not None:
        for element in _model_elements(pending):
            props[element] = {"type": "boolean"} if element in PHRASE_ELEMENTS else {"type": "string"}
    else:
        for name in REQUEST_FIELDS:
            props[name] = {"type": "string"}
    return {"type": "object", "properties": props, "required": ["kind"], "additionalProperties": False}


def example_messages(examples: list[Example], mode: str) -> tuple[tuple[str, str], ...]:
    """The examples for a readback (one is expected) or a request (none is), in the file's order: the same on
    every call of that mode, so Ollama reads them once and reuses them; only the last message is new to it."""
    messages: list[tuple[str, str]] = []
    for ex in examples:
        if ex.mode != mode:
            continue
        expect = [tuple(item.split("=", 1)) if "=" in item else (item, None) for item in ex.expect]
        messages.append(("user", user_message(callsign=ex.callsign or None, phase=ex.phase, station=ex.station,
                                              cleared=ex.cleared, traffic=ex.traffic or None, recent=ex.recent,
                                              expect=expect, pilot=ex.pilot)))
        messages.append(("assistant", json.dumps(ex.answer, separators=(",", ":"))))
    return tuple(messages)


def build_request(text: str, pending: PendingReadback | None, context: InterpretContext,
                  examples: list[Example]) -> LlmRequest:
    mode = "readback" if pending is not None else "request"
    messages = list(example_messages(examples, mode))
    messages.append(("user", user_message(
        callsign=_callsign_line(context), phase=context.phase, station=context.station, role=context.station_role,
        cleared=_cleared(context), traffic=context.traffic, recent=_recent(context),
        expect=_expected_items(pending) if mode == "readback" else [], pilot=text,
    )))
    return LlmRequest("understand", SYSTEM, tuple(messages), schema(pending if mode == "readback" else None, context))


# --- reading the answer ----------------------------------------------------------------------------------

RUNWAY_RE = re.compile(r"^(?:rwy|runway)?\s*0?(\d{1,2})\s*(l|r|c|left|right|center|centre)?$", re.IGNORECASE)
APPROACH_RE = re.compile(r"^(ils|loc|rnav|gps|rnp|vor|ndb|lda|sdf|visual)\s*(?:\(?(?:gps|rnp)\)?)?\s*(?:rwy|runway)?\s*"
                         r"(\d{1,2}\s*[lrc]?)$", re.IGNORECASE)


def parse_value(element: str, raw: Any) -> Any:
    """The model's string for an element, as the typed value the readback rules compare. None: empty."""
    if element in PHRASE_ELEMENTS:
        if not isinstance(raw, bool):
            raise AnswerError(f"{element} must be true or false")
        return True if raw else None
    if not isinstance(raw, str):
        raise AnswerError(f"{element} must be a string")
    value = raw.strip()
    if not value:
        return None
    if element in ("runway", "hold_short"):
        match = RUNWAY_RE.match(value)
        if not match or not 1 <= int(match.group(1)) <= 36:
            raise AnswerError(f"{element} {raw!r} is not a runway like \"06L\"")
        side = (match.group(2) or "")[:1].upper()
        return normalize_runway(match.group(1) + side)
    if element == "frequency":
        try:
            mhz = float(value)
        except ValueError:
            raise AnswerError(f"frequency {raw!r} is not a number like \"120.425\"") from None
        if not 118.0 <= mhz <= 137.0:
            raise AnswerError(f"frequency {raw!r} is not an airband frequency")
        return round(mhz, 3)
    if element == "squawk":
        if not re.fullmatch(r"[0-7]{4}", value):
            raise AnswerError(f"squawk {raw!r} is not four digits 0-7")
        return value
    if element in ("altitude", "cruise"):
        digits = value.upper().removeprefix("FL").replace(",", "").replace("FT", "").strip()
        if not digits.isdigit():
            raise AnswerError(f"{element} {raw!r} is not a number of feet")
        feet = int(digits)
        # "FL150", or "015" (flight level zero one five: 1,500 ft)
        feet = feet * 100 if feet < 1000 and (value.upper().startswith("FL") or digits.startswith("0")) else feet
        if not 100 <= feet <= 60000:
            raise AnswerError(f"{element} {raw!r} is out of range")
        return feet
    if element == "heading":
        if not value.isdigit() or not 1 <= int(value) <= 360:
            raise AnswerError(f"heading {raw!r} is not 1-360")
        return int(value)
    if element == "approach":
        match = APPROACH_RE.match(value)
        if not match:
            raise AnswerError(f"approach {raw!r} is not like \"ILS 06\"")
        kind = {"GPS": "RNAV", "RNP": "RNAV"}.get(match.group(1).upper(), match.group(1).upper())
        return Approach(kind, normalize_runway(match.group(2).replace(" ", "").upper()))
    if element == "atis":
        letter = value.upper().removeprefix("INFORMATION").strip()
        if not re.fullmatch(r"[A-Z]", letter):
            raise AnswerError(f"atis {raw!r} is not a single letter")
        return letter
    return value


def _said_words(value: str, tokens: list[Token]) -> bool:
    words = {t.text for t in tokens}
    words |= {name for t in tokens if t.kind == "letter" for name, letter in PHONETIC.items() if letter == t.text}  # "Quebec"
    wanted = [w for w in re.findall(r"[a-z]+", value.lower()) if len(w) > 2]
    return bool(wanted) and any(w in words for w in wanted)


def _said_digits(value: str, tokens: list[Token]) -> bool:
    digits = re.findall(r"\d+", value)
    return all(any(t.kind == "number" and t.text.split(".")[0] == d for t in tokens) for d in digits)


def parse_answer(raw: str, pending: PendingReadback | None, tokens: list[Token]) -> Answer:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnswerError(f"not valid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise AnswerError("the answer must be a JSON object")
    kind, intent = data.get("kind"), data.get("intent", "") or ""
    if kind not in KINDS:
        raise AnswerError(f"kind must be one of {', '.join(KINDS)}")
    if intent and intent not in INTENTS:
        raise AnswerError(f"intent {intent!r} is not allowed")
    topic = data.get("topic", "") or ""
    if kind == "request" and not intent and topic in TOPICS:
        kind = "question"  # small models file questions as requests with a topic; the meaning is clear
    if kind == "request" and not intent:
        raise AnswerError("a request needs an intent")
    if kind == "question":
        topic = topic if topic in TOPICS else "other"
    if kind == "readback":
        # What they asked for on the back of it, if anything: kept only when the words back it up (a small
        # model fills in an intent for every readback given the chance).
        if intent in ("acknowledge", "say_again") or (intent and missing_cue(intent, tokens)):
            intent = ""
        topic = topic if topic in TOPICS and is_question(" ".join(t.text for t in tokens)) else ""

    values: dict[str, Any] = {}
    dropped: list[str] = []
    fields = _model_elements(pending) if pending is not None else REQUEST_FIELDS
    for name in fields:
        if name not in data:
            continue
        if name in ("fix", "conditions"):
            text = str(data[name]).strip()
            if text and _said_words(text, tokens):
                values[name] = text
            elif text:
                dropped.append(f"{name}={text}")
            continue
        if name == "approach" and kind == "request":
            text = str(data[name]).strip().upper()
            kind_word = next((k for k in APPROACH_KINDS if k in text), "")
            said = {t.text for t in tokens}
            if kind_word and (kind_word.lower() in said or (kind_word in ("RNAV", "GPS") and {"rnav", "nav", "gps"} & said)):
                values[name] = {"GPS": "RNAV", "LOC": "ILS"}.get(kind_word, kind_word)
            elif text:
                dropped.append(f"{name}={text}")
            continue
        if name in ("emergency", "souls", "fuel"):
            text = str(data[name]).strip()
            if text and ((name == "emergency" and _said_words(text, tokens)) or (name != "emergency" and _said_digits(text, tokens))):
                values[name] = text
            # (Souls and fuel the pilot didn't give are left out, not a reason to lose the emergency: ATC asks.)
            continue
        value = parse_value(name, data[name])
        if value is None:
            continue
        if grounded(name, value, tokens):
            values[name] = value
        else:
            dropped.append(f"{name}={data[name]}")
    if kind == "request" and (problem := missing_cue(intent, tokens)):
        raise IntentError(problem + "; pick the intent that matches the words, or other if none does")
    if dropped:
        # A model that invents one value has probably misread the whole call ("request direct" taken
        # as an altitude request with a made-up level), so the answer is retried, not patched.
        raise AnswerError("the pilot did not say " + ", ".join(dropped) + "; report only what the pilot said")
    return Answer(kind=kind, intent=intent, topic=topic, values=values)


# --- the interpreter --------------------------------------------------------------------------------------


class LlmInterpreter:
    """Model first, grammar as the check and the fallback. Same interface as ``GrammarInterpreter``."""

    def __init__(
        self,
        backend: LlmBackend | None,
        *,
        mode: Mode = "primary",
        timeout_s: float = 2.5,
        max_attempts: int = 2,
        budget_s: float = 4.0,
        patience_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.mode: Mode = mode if backend is not None else "off"
        self.timeout_s, self.max_attempts, self.budget_s = timeout_s, max_attempts, budget_s
        self.patience_s = patience_s  # after "stand by": one long try for a model the sim is keeping busy
        self.grammar = GrammarInterpreter()
        self.say_again = SayAgainInterpreter()
        self.examples = load_examples()
        self._clock = clock

    def interpret(self, text: str, pending: PendingReadback | None, context: InterpretContext) -> Interpretation:
        grammar = self.grammar.interpret(text, pending, context)
        reason = find_trigger(grammar, text, context.confidence, context=context)
        # A readback the grammar finds correct is the script read back: nothing for the model to add (unless
        # something rides along with it), and every call to it costs the sim frames (it shares the machine) and
        # up to a few seconds of waiting.
        scripted = pending is not None and grammar.kind == "readback" and grammar.status == "correct" and reason is None
        if self.mode == "off" or scripted or (self.mode == "fallback" and reason is None):
            return self._grammar_only(grammar, text, pending, context, reason)
        answer, exchanges = self._ask(text, pending, context, reason)
        # The grammar's reading counts when it makes sense here: a check-in heard on the ground isn't one.
        grammar_knows = grammar.kind != "unknown" and not grammar.needs_fallback and (
            grammar.kind == "readback" or is_expected(grammar.intent, context.station_role or role_of(context.station),
                                                      context.phase))
        if answer is None or (answer.guessed and grammar_knows):
            # No usable answer from the model: the grammar's reading, if it has one, beats a guess.
            result = self._grammar_only(grammar, text, pending, context, reason)
        else:
            result = self._merge(grammar, answer, text, pending, context)
        return replace(result, trigger=reason, exchanges=tuple(exchanges))

    # -- asking ---------------------------------------------------------------------------------------

    def _ask(self, text: str, pending: PendingReadback | None, context: InterpretContext,
             reason: str | None) -> tuple[Answer | None, list[LlmExchange]]:
        assert self.backend is not None
        tokens = without_callsign(normalize(text), context.callsign)  # its digits are not values
        request = build_request(text, pending, context, self.examples)
        exchanges: list[LlmExchange] = []
        intent_errors = 0
        timeout_s, budget_s = (self.patience_s, self.patience_s) if context.patient else (self.timeout_s, self.budget_s)
        deadline = self._clock() + budget_s
        for attempt in range(1, self.max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining < 0.2:
                break
            reply = self.backend.complete(request, timeout_s=min(timeout_s, remaining))

            def record(outcome: str, detail: str = "", _reply=reply, _request=request, _attempt=attempt) -> None:
                exchanges.append(LlmExchange(
                    t=context.t, purpose="understand", model=self.backend.model, key=_request.key(self.backend.model),
                    prompt=_request.prompt, response=_reply.text or "", outcome=outcome, detail=detail,
                    trigger=reason or "", latency_ms=round(_reply.latency_ms, 1), attempt=_attempt,
                ))

            if reply.text is None:
                record(reply.error.split(":")[0] or "error", reply.error)
                break  # a slow or missing model won't be faster on a second try
            try:
                answer = parse_answer(reply.text, pending, tokens)
                answer = self._check_phase(answer, context)
            except AnswerError as exc:
                intent_errors += isinstance(exc, IntentError)
                record("invalid", str(exc))
                request = replace(request, messages=(
                    *request.messages, ("assistant", reply.text),
                    ("user", f"That answer can't be used: {exc}. Reply with the corrected JSON only."),
                ))
                continue
            record("used")
            return answer, exchanges
        words = {t.text for t in tokens}
        if exchanges and (topic := question_topic(text)) is not None:
            return Answer(kind="question", topic=topic, guessed=True), exchanges  # "say the winds": clear enough
        if exchanges and intent_errors == len(exchanges) and words & (REQUEST_WORDS - {"higher", "lower"}):
            # Every answer said "a request" and every intent it tried was contradicted by the words:
            # it's a request the engine has no procedure for, which ATC declines.
            return Answer(kind="request", intent="other", guessed=True), exchanges
        if exchanges and is_question(text):
            return Answer(kind="question", topic="other", guessed=True), exchanges  # heard fine, just off-script
        return None, exchanges

    @staticmethod
    def _check_phase(answer: Answer, context: InterpretContext) -> Answer:
        """The schema already limits the intents to this controller and phase; a model that ignores the
        schema (or a recorded answer from before) is held to it here."""
        if answer.kind == "request" and answer.intent not in allowed_intents(context):
            raise IntentError(f"{answer.intent} is not possible in phase {context.phase}")
        return answer

    # -- combining with the grammar -------------------------------------------------------------------

    def _grammar_only(self, grammar: Interpretation, text: str, pending: PendingReadback | None,
                      context: InterpretContext, reason: str | None) -> Interpretation:
        if grammar.kind == "unknown" and (topic := question_topic(text)) is not None:
            return Interpretation(kind="request", intent="question", values={"topic": topic}, confidence=0.6,
                                  callsign_heard=grammar.callsign_heard, text=text, trigger=reason)
        if grammar.needs_fallback and grammar.intent != EMERGENCY:
            return replace(self.say_again.interpret(text, pending, context), trigger=reason)
        return replace(grammar, trigger=reason)

    def _merge(self, grammar: Interpretation, answer: Answer, text: str, pending: PendingReadback | None,
               context: InterpretContext) -> Interpretation:
        tokens = normalize(text)
        if grammar.intent == EMERGENCY or answer.intent == "emergency" or answer.values.get("emergency"):
            # Keywords like "mayday" always count; the model adds the details.
            details = {k: answer.values[k] for k in ("emergency", "souls", "fuel") if k in answer.values}
            return Interpretation(kind="request", intent=EMERGENCY, values=details, confidence=1.0, source="llm",
                                  callsign_heard=grammar.callsign_heard, text=text)
        if grammar.kind == "readback" and answer.kind in ("request", "unintelligible") and answer.intent != "say_again":
            # The pilot repeated the pending instruction; that's a readback whatever the model says. What the
            # model heard asked for rides along with a readback that was right.
            asked = self._follow_up(answer, text) if answer.kind == "request" and grammar.status == "correct" else None
            return replace(grammar, then=grammar.then or asked)
        if pending is not None and answer.kind == "readback":
            readback = self._readback(without_callsign(tokens, context.callsign), answer, pending, grammar, text,
                                      context.confidence)
            if readback is not None:
                return replace(readback, then=grammar.then or self._follow_up(answer, text))
        if answer.kind == "question":
            return Interpretation(kind="request", intent="question", values={"topic": answer.topic}, confidence=0.8,
                                  source="llm", callsign_heard=grammar.callsign_heard, text=text)
        if answer.kind == "request":
            values = {**grammar.values, **{k: v for k, v in answer.values.items()
                                          if k in ("runway", "atis", "altitude", "fix", "approach", "conditions")}}
            if answer.intent == "checkin" and grammar.intent == "checkin" and "altitude" in grammar.values:
                values["altitude"] = grammar.values["altitude"]  # "passing 6,000 for 12,000": the first is where we are
            return Interpretation(kind="request", intent=answer.intent, values=values, confidence=0.8, source="llm",
                                  callsign_heard=grammar.callsign_heard, text=text)
        # Unintelligible to the model (or a readback with nothing pending): the grammar may still know.
        if grammar.kind != "unknown" and not grammar.needs_fallback:
            return grammar
        return replace(self.say_again.interpret(text, pending, context), source="llm")

    @staticmethod
    def _follow_up(answer: Answer, text: str) -> Interpretation | None:
        """The request or question the model heard along with a readback, or None."""
        if answer.intent and answer.intent not in ("acknowledge", "say_again", "emergency"):
            values = {k: v for k, v in answer.values.items() if k in ("runway", "altitude", "fix", "approach", "conditions")}
            return Interpretation(kind="request", intent=answer.intent, values=values, confidence=0.8, source="llm", text=text)
        if answer.topic:
            return Interpretation(kind="request", intent="question", values={"topic": answer.topic}, confidence=0.8,
                                  source="llm", text=text)
        return None

    @staticmethod
    def _readback(tokens: list[Token], answer: Answer, pending: PendingReadback, grammar: Interpretation,
                  text: str, confidence: float | None = None) -> Interpretation | None:
        heard: dict[str, Any] = {}
        mismatched: dict[str, Any] = {}
        unclear: dict[str, Any] = {}
        missing: list[str] = []
        for element in (*pending.required, *pending.optional):
            expected = pending.expected.get(element, True)
            candidates = find_candidates(element, tokens, expected)
            from_grammar = next((c for c in candidates if values_equal(element, c, expected)), None)
            from_model = answer.values.get(element)
            if from_grammar is not None:
                heard[element] = from_grammar
            elif from_model is not None and values_equal(element, from_model, expected):
                heard[element] = from_model
            elif (close := next((c for c in candidates if values_close(element, c, expected, confidence)), None)) is not None:
                unclear[element] = close
            elif candidates:
                mismatched[element] = candidates[0]
            elif from_model is not None:
                mismatched[element] = from_model
            elif element in pending.required:
                missing.append(element)
        if not heard and not mismatched and not unclear:
            return None
        if "callsign" in grammar.missing:
            missing.append("callsign")
        status: Status = "incorrect" if mismatched else ("unclear" if unclear else ("incomplete" if missing else "correct"))
        total = len(pending.required) + len(pending.optional)
        return Interpretation(
            kind="readback", intent=pending.instruction_id, values=heard, status=status, missing=tuple(missing),
            mismatched=mismatched, unclear=unclear, callsign_heard=grammar.callsign_heard,
            confidence=(len(heard) + len(mismatched) + len(unclear)) / total if total else 1.0,
            needs_fallback=False, source="llm", text=text,
        )
