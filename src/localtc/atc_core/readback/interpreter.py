"""Interpreting pilot transmissions: the grammar interpreter, the fallback hook, and the chain.

``atc_core.llm.LlmInterpreter`` wraps the grammar with a local language model and
returns the same ``Interpretation``, so the dialogue engine doesn't care which one ran.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from localtc.atc_core.readback.extract import (
    CONTRARY_TO_HOLDING,
    ELEMENTS,
    _has_any,
    candidates,
    values_close,
    values_equal,
    without_callsign,
)
from localtc.atc_core.readback.intents import EMERGENCY, match_intents, resolve
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign

Kind = Literal["readback", "request", "unknown"]
# A transmission with one of these asks for something; it isn't a readback even if it repeats a value
# ("negative, request to maintain 1,500").
ASKING = {"request", "requesting", "unable"}
Status = Literal["correct", "incorrect", "unclear", "incomplete", "no_match"]
# "Affirm" answers a "confirm ...".
AFFIRM = {"affirm", "affirmative", "correct", "yes", "yep", "confirmed", "confirm"}


@dataclass(frozen=True)
class PendingReadback:
    instruction_id: str
    controller: str
    expected: dict[str, Any]
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    issued_t: float = 0.0
    attempts: int = 0
    confirming: bool = False  # ATC asked "confirm ...": "affirm" is enough
    nudged: bool = False  # ATC asked "how do you read?" about it


@dataclass(frozen=True)
class InterpretContext:
    callsign: Callsign | None = None
    phase: str | None = None
    strict_callsign: bool = False
    t: float = 0.0  # event time of the transmission
    station: str | None = None  # who the pilot is talking to, e.g. "Montreal Tower"
    last_atc: str | None = None  # what that controller said last
    confidence: float | None = None  # speech-to-text confidence (None: typed)
    patient: bool = False  # ATC said "stand by": the model may take its time (``LlmInterpreter.patience_s``)
    # The rest of the moment, for the language model (the grammar doesn't need it): the controller's role, what
    # this flight is cleared for, the runway in use, and the traffic ATC called in the last few minutes.
    station_role: str | None = None  # clearance, ground, tower, departure, approach, center
    cleared_altitude_ft: int | None = None
    cleared_heading: int | None = None
    squawk: str | None = None
    runway: str | None = None
    traffic: str | None = None


@dataclass(frozen=True)
class Interpretation:
    kind: Kind
    intent: str | None = None  # request intent, or the instruction id for readbacks
    values: dict[str, Any] = field(default_factory=dict)
    status: Status = "no_match"
    missing: tuple[str, ...] = ()
    mismatched: dict[str, Any] = field(default_factory=dict)  # element -> what the pilot said
    unclear: dict[str, Any] = field(default_factory=dict)  # element -> a near miss ATC should have confirmed
    callsign_heard: bool = False
    confidence: float = 0.0
    needs_fallback: bool = False
    source: str = "grammar"  # grammar, llm, fallback
    text: str = ""
    trigger: str | None = None  # why the language model was asked (atc_core.llm.triggers)
    exchanges: tuple[Any, ...] = ()  # LlmExchange events from interpreting this transmission


class Interpreter(Protocol):
    def interpret(self, text: str, pending: PendingReadback | None, context: InterpretContext) -> Interpretation: ...


CORRECTION = {"sorry", "correction"}


def after_correction(tokens: list) -> list:
    """What the pilot said after correcting themselves ("via Charlie. Sorry, at Charlie, via Golf Charlie"), or
    ``tokens`` itself when nothing, or only a word or two, follows the correction."""
    last = max((i for i, t in enumerate(tokens) if t.kind == "word" and t.text in CORRECTION), default=None)
    return tokens[last + 1:] if last is not None and len(tokens) - last - 1 >= 3 else tokens


class GrammarInterpreter:
    """Deterministic: element extractors for readbacks, keyword intents for requests."""

    max_attempts = 2

    def interpret(self, text: str, pending: PendingReadback | None, context: InterpretContext) -> Interpretation:
        tokens = normalize(text)
        callsign_heard = bool(ELEMENTS["callsign"](tokens, context.callsign))
        matches = match_intents(tokens)
        chosen, ambiguous = resolve(matches)

        if chosen is not None and chosen.intent == EMERGENCY:
            return Interpretation(
                kind="request", intent=EMERGENCY, callsign_heard=callsign_heard, confidence=1.0, needs_fallback=True, text=text
            )

        words = {t.text for t in tokens if t.kind == "word"}
        if pending is not None and not words & ASKING:
            readback = self._readback(tokens, pending, context, callsign_heard, text)
            corrected = after_correction(tokens)
            if corrected is not tokens and (readback is None or readback.status != "correct"):
                # "Via Charlie. Sorry, at Charlie, via Golf Charlie": what follows the correction, if that's right.
                # (A correction of one value, "cleared to land 14 left, correction 14 right", is already read right.)
                again = self._readback(corrected, pending, context, callsign_heard, text)
                if again is not None and again.status == "correct":
                    readback = again
            if readback is not None:
                return readback
            if pending.confirming and words & AFFIRM:
                return Interpretation(kind="readback", intent=pending.instruction_id, status="correct",
                                      values={e: pending.expected.get(e, True) for e in pending.required},
                                      callsign_heard=callsign_heard, confidence=0.9, text=text)

        if chosen is not None:
            return Interpretation(
                kind="request",
                intent=chosen.intent,
                values=chosen.values,
                status="no_match",
                callsign_heard=callsign_heard,
                confidence=0.5 if ambiguous else 0.9,
                needs_fallback=ambiguous,
                text=text,
            )
        return Interpretation(kind="unknown", callsign_heard=callsign_heard, needs_fallback=True, text=text)

    def _readback(
        self, tokens, pending: PendingReadback, context: InterpretContext, callsign_heard: bool, text: str
    ) -> Interpretation | None:
        heard: dict[str, Any] = {}
        mismatched: dict[str, Any] = {}
        unclear: dict[str, Any] = {}
        missing: list[str] = []
        present = 0
        values_only = without_callsign(tokens, context.callsign)
        for element in (*pending.required, *pending.optional):
            expected = pending.expected.get(element, True)
            found = candidates(element, values_only, expected)
            if not found:
                if element in pending.required:
                    missing.append(element)
                continue
            present += 1
            match = next((c for c in found if values_equal(element, c, expected)), None)
            close = next((c for c in found if values_close(element, c, expected)), None)
            if match is not None:
                heard[element] = match
            elif close is not None:
                unclear[element] = close
            else:
                mismatched[element] = found[0]
        if "hold_short" in missing and _has_any(values_only, *CONTRARY_TO_HOLDING):
            # Told to hold short, read back as going onto the runway ("cleared for takeoff runway 27"):
            # the one readback that must be corrected, never let through or answered with "say again".
            missing.remove("hold_short")
            mismatched["hold_short"] = "onto the runway"
            present += 1
        if "hold_short" in missing and _has_any(values_only, ("hold", "short"), ("holding", "short")):
            # "Hold short, Air Canada 779": the instruction, without the runway it's for, which a hold short
            # readback has to name. Incomplete ("read back hold short runway 06L"), not "say again".
            present += 1
        if present == 0:
            return None  # not a readback of the pending instruction
        if context.strict_callsign and not callsign_heard:
            missing.append("callsign")
        if mismatched:
            status: Status = "incorrect"
        elif unclear:
            status = "unclear"
        elif missing:
            status = "incomplete"
        else:
            status = "correct"
        total = len(pending.required) + len(pending.optional)
        return Interpretation(
            kind="readback",
            intent=pending.instruction_id,
            values=heard,
            status=status,
            missing=tuple(missing),
            mismatched=mismatched,
            unclear=unclear,
            callsign_heard=callsign_heard,
            confidence=present / total if total else 1.0,
            needs_fallback=status != "correct" and pending.attempts + 1 >= self.max_attempts,
            text=text,
        )


class SayAgainInterpreter:
    """Phase 1 fallback: anything the grammar can't resolve gets "say again"."""

    def interpret(self, text: str, pending: PendingReadback | None, context: InterpretContext) -> Interpretation:
        return Interpretation(kind="unknown", intent="say_again", source="fallback", text=text)


class ChainInterpreter:
    def __init__(self, primary: Interpreter, fallback: Interpreter) -> None:
        self.primary, self.fallback = primary, fallback

    def interpret(self, text: str, pending: PendingReadback | None, context: InterpretContext) -> Interpretation:
        result = self.primary.interpret(text, pending, context)
        if not result.needs_fallback:
            return result
        if result.intent == EMERGENCY:
            return result  # handled deterministically; the fallback may add detail in Phase 2
        fallback = self.fallback.interpret(text, pending, context)
        if result.kind == "readback" and fallback.intent == "say_again":
            # A readback the grammar knows is incomplete or wrong ("hold short", no runway): what's missing is
            # the answer, as when the language model times out; "say again" would lose it.
            return result
        return fallback
