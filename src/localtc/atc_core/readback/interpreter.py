"""Interpreting pilot transmissions: the grammar interpreter, the fallback hook, and the chain.

Phase 2 replaces only the fallback with an LLM that returns the same
``Interpretation``, so the dialogue engine doesn't change.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from localtc.atc_core.readback.extract import ELEMENTS, values_equal
from localtc.atc_core.readback.intents import EMERGENCY, match_intents, resolve
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign

Kind = Literal["readback", "request", "unknown"]
Status = Literal["correct", "incorrect", "incomplete", "no_match"]


@dataclass(frozen=True)
class PendingReadback:
    instruction_id: str
    controller: str
    expected: dict[str, Any]
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    issued_t: float = 0.0
    attempts: int = 0


@dataclass(frozen=True)
class InterpretContext:
    callsign: Callsign | None = None
    phase: str | None = None
    strict_callsign: bool = False


@dataclass(frozen=True)
class Interpretation:
    kind: Kind
    intent: str | None = None  # request intent, or the instruction id for readbacks
    values: dict[str, Any] = field(default_factory=dict)
    status: Status = "no_match"
    missing: tuple[str, ...] = ()
    mismatched: dict[str, Any] = field(default_factory=dict)  # element -> what the pilot said
    callsign_heard: bool = False
    confidence: float = 0.0
    needs_fallback: bool = False
    source: str = "grammar"
    text: str = ""


class Interpreter(Protocol):
    def interpret(self, text: str, pending: PendingReadback | None, context: InterpretContext) -> Interpretation: ...


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

        if pending is not None:
            readback = self._readback(tokens, pending, context, callsign_heard, text)
            if readback is not None:
                return readback

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
        missing: list[str] = []
        present = 0
        for element in (*pending.required, *pending.optional):
            expected = pending.expected.get(element, True)
            candidates = ELEMENTS[element](tokens, expected)
            if not candidates:
                if element in pending.required:
                    missing.append(element)
                continue
            present += 1
            match = next((c for c in candidates if values_equal(element, c, expected)), None)
            if match is not None:
                heard[element] = match
            else:
                mismatched[element] = candidates[0]
        if present == 0:
            return None  # not a readback of the pending instruction
        if context.strict_callsign and not callsign_heard:
            missing.append("callsign")
        if mismatched:
            status: Status = "incorrect"
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
        return self.fallback.interpret(text, pending, context)
