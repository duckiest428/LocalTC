"""Pilot-initiated requests and reports, matched by keyword phrases plus slot extraction."""

from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core.readback.extract import _find_phrase, _has_any, _number, altitudes, hold_short, runways
from localtc.atc_core.readback.normalize import Token

EMERGENCY = "emergency"


@dataclass(frozen=True)
class IntentMatch:
    intent: str
    values: dict[str, Any] = field(default_factory=dict)


def _atis(tokens: list[Token]) -> str | None:
    for phrase in (("information",), ("atis",), ("info",)):
        for i in _find_phrase(tokens, phrase):
            if i < len(tokens) and (tokens[i].kind == "letter" or (tokens[i].kind == "word" and len(tokens[i].text) == 1)):
                return tokens[i].text.upper()
    return None


def _reported_altitudes(tokens: list[Token]) -> list[int]:
    """Altitudes in check-ins: "2,000 climbing 5,000", "level five thousand", "with you at 3000"."""
    found = []
    for i, token in enumerate(tokens):
        if token.kind == "number" and token.text.isdigit() and 500 <= int(token.text) <= 60000 and int(token.text) % 100 == 0:
            found.append(int(token.text))
        if token.text == "level" and i + 1 < len(tokens) and (num := _number(tokens, i + 1)) and num.isdigit():
            value = int(num) * 100 if int(num) < 1000 else int(num)  # "level 120" is flight level 120
            if value not in found:
                found.append(value)
    return found


def _any_runway(tokens: list[Token]) -> str | None:
    """A runway mentioned in a report ("holding short runway 34L", "clear of 14R", "final runway 14R")."""
    return next(iter(runways(tokens) + hold_short(tokens)), None)


def match_intents(tokens: list[Token]) -> list[IntentMatch]:
    """All intents present, most specific first."""
    matches: list[IntentMatch] = []

    def add(intent: str, **values: Any) -> None:
        matches.append(IntentMatch(intent, {k: v for k, v in values.items() if v is not None}))

    if _has_any(tokens, ("mayday",), ("pan", "pan"), ("emergency",)):
        add(EMERGENCY)
    if _has_any(tokens, ("say", "again"), ("repeat",), ("didn't", "copy"), ("did", "not", "copy"), ("say", "that", "again")):
        add("say_again")
    if _has_any(tokens, ("clearance",), ("ifr", "to"), ("i", "f", "r", "to"), ("ready", "to", "copy")) and not _has_any(
        tokens, ("cleared",)
    ):
        add("request_ifr_clearance", atis=_atis(tokens))
    parking = _has_any(tokens, ("to", "parking"), ("to", "the", "ramp"), ("to", "ramp"), ("to", "the", "gate"), ("to", "gate"))
    if _has_any(tokens, ("clear", "of", "runway"), ("clear", "of", "the", "runway"), ("clear", "runway"), ("clear", "of")):
        add("clear_of_runway", runway=_any_runway(tokens))
    if parking and _has_any(tokens, ("taxi",)):
        add("request_taxi_parking")
    elif _has_any(tokens, ("ready", "to", "taxi"), ("request", "taxi"), ("taxi", "with"), ("ready", "for", "taxi")):
        add("ready_to_taxi", atis=_atis(tokens))
    if _has_any(
        tokens, ("ready", "for", "departure"), ("ready", "for", "takeoff"), ("ready", "to", "go"), ("ready", "for", "take", "off")
    ):
        add("ready_for_departure", runway=_any_runway(tokens))
    elif hold_short(tokens) and not _has_any(tokens, ("taxi",), ("via",), ("cleared",)):
        # "Tower, holding short runway 06L" is a departure request; a taxi readback names a route instead.
        add("ready_for_departure", runway=_any_runway(tokens))
    if _has_any(tokens, ("mile", "final"), ("miles", "final"), ("on", "final"), ("short", "final")):
        add("report_final", runway=_any_runway(tokens))
    checkin_words = (("climbing",), ("descending",), ("level",), ("with", "you"), ("checking", "in"), ("leaving",),
                     ("passing",), ("through",), ("out", "of"))
    if _has_any(tokens, *checkin_words) and not altitudes(tokens):
        reported = _reported_altitudes(tokens)
        add("checkin", altitude=reported[0] if reported else None, assigned=reported[1] if len(reported) > 1 else None)
    if not matches and _has_any(tokens, ("roger",), ("wilco",), ("copy",), ("will", "comply"), ("disregard",)):
        add("acknowledge")
    return matches


# Intents that legitimately appear together; the first one is used.
COMPATIBLE = [
    {"request_taxi_parking", "clear_of_runway"},
    {"ready_for_departure", "checkin"},  # "holding short, ready" can contain "level"-like noise
]


def resolve(matches: list[IntentMatch]) -> tuple[IntentMatch | None, bool]:
    """Pick the intent; returns (match, ambiguous)."""
    if not matches:
        return None, False
    emergency = next((m for m in matches if m.intent == EMERGENCY), None)
    if emergency is not None:
        return emergency, False
    intents = {m.intent for m in matches}
    if len(intents) == 1 or any(intents <= group for group in COMPATIBLE):
        preferred = next((m for m in matches if m.intent == "request_taxi_parking"), matches[0])
        return preferred, False
    return matches[0], True
