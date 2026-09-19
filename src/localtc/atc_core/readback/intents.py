"""Pilot-initiated requests and reports, matched by keyword phrases plus slot extraction."""

from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core.readback.extract import _find_phrase, _has_any, _number, _runway_at, altitudes, hold_short, runways
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
    """A runway mentioned in a report ("holding short runway 34L", "clear of 14R", "final runway 14R");
    after "correction", the corrected one."""
    for i in reversed(range(len(tokens))):
        if tokens[i].text == "correction":
            if found := runways(tokens[i:]) + hold_short(tokens[i:]):
                return found[0]
            if (hit := _runway_at(tokens, i + 1)) is not None:  # "..., correction 08"
                return hit[0]
    return next(iter(runways(tokens) + hold_short(tokens)), None)


FIX_STOP = {"to", "the", "a", "direct", "please", "for", "if", "able", "possible", "when", "request", "requesting"}
CONDITION_WORDS = ("turbulence", "chop", "choppy", "icing", "ice", "bumpy", "smooth", "shear", "rough")
APPROACH_WORDS = {"ils": "ILS", "localizer": "ILS", "rnav": "RNAV", "gps": "RNAV", "visual": "VISUAL"}


def _fix(tokens: list[Token]) -> str | None:
    """What follows "direct": "request direct BLAKO" -> "BLAKO", "direct to the airport" -> "airport"."""
    for i in _find_phrase(tokens, ("direct",)):
        words = []
        for token in tokens[i:]:
            if token.text in FIX_STOP and not words:
                continue
            if token.kind != "word" or token.text in FIX_STOP or len(words) == 2:
                break
            words.append(token.text)
        if words:
            return " ".join(words).upper() if len(words[0]) <= 5 and len(words) == 1 else " ".join(words).title()
    return None


def _traffic(tokens: list[Token]) -> str | None:
    if _has_any(tokens, ("negative", "contact"), ("not", "in", "sight"), ("no", "joy")):
        return "negative"
    if _has_any(tokens, ("in", "sight"), ("have", "the", "traffic"), ("got", "the", "traffic"), ("traffic", "insight")):
        return "in_sight"
    if _has_any(tokens, ("looking",)):
        return "looking"
    return None


def match_intents(tokens: list[Token]) -> list[IntentMatch]:
    """All intents present, most specific first."""
    matches: list[IntentMatch] = []

    def add(intent: str, **values: Any) -> None:
        matches.append(IntentMatch(intent, {k: v for k, v in values.items() if v is not None}))

    if _has_any(tokens, ("mayday",), ("pan", "pan"), ("emergency",)):
        add(EMERGENCY)
    if _has_any(tokens, ("say", "again"), ("repeat",), ("didn't", "copy"), ("did", "not", "copy"), ("say", "that", "again")):
        add("say_again")
    landing = _has_any(tokens, ("clear", "to", "land"), ("cleared", "to", "land"), ("clearance", "to", "land"),
                       ("landing", "clearance"), ("clear", "for", "landing"), ("cleared", "for", "landing"))
    if _has_any(tokens, ("clearance",), ("ifr", "to"), ("i", "f", "r", "to"), ("ready", "to", "copy")) and not _has_any(
        tokens, ("cleared",)
    ) and not landing:
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
    if _has_any(tokens, ("mile", "final"), ("miles", "final"), ("on", "final"), ("short", "final"), ("inbound",)) or landing:
        add("report_final", runway=_any_runway(tokens))  # also "are we cleared to land?"
    wanted = altitudes(tokens) or ([n for n in _reported_altitudes(tokens) if n >= 1000]
                                   if _has_any(tokens, ("higher",), ("lower",), ("climb",), ("descend",)) else [])
    if _has_any(tokens, ("request",), ("requesting",), ("like",), ("can", "we"), ("could", "we")) and wanted:
        add("request_altitude", altitude=wanted[0])  # "request to maintain 1,500", "request higher, 7000"
    asking = _has_any(tokens, ("request",), ("requesting",), ("like",), ("can", "we"), ("could", "we"), ("able", "to"),
                      ("want",), ("need",))
    if _has_any(tokens, ("going", "around"), ("go", "around"), ("missed", "approach"), ("executing", "missed")):
        add("going_around")
    if (traffic := _traffic(tokens)) is not None and (_has_any(tokens, ("traffic",)) or traffic != "in_sight"):
        add("traffic_report", traffic=traffic)
    if _has_any(tokens, ("direct",)) and (fix := _fix(tokens)):
        add("request_direct", fix=fix)
    approach = next((APPROACH_WORDS[t.text] for t in tokens if t.text in APPROACH_WORDS), None)
    if _has_any(tokens, ("vectors",), ("vector",)):
        add("request_vectors", runway=next(iter(runways(tokens)), None), approach=approach)
    if asking and _has_any(tokens, ("return",), ("divert",), ("back", "to", "the", "field"), ("back", "to", "the", "airport")):
        add("request_return")
    if asking and (approach or runways(tokens)) and not _has_any(tokens, ("taxi",), ("final",)):
        add("request_runway", runway=next(iter(runways(tokens)), None), approach=approach)
    if any(t.text in CONDITION_WORDS for t in tokens) and not asking:
        add("report_conditions", conditions=" ".join(t.text for t in tokens if t.kind == "word" and t.text in (
            *CONDITION_WORDS, "light", "moderate", "severe", "occasional", "continuous")))
    checkin_words = (("climbing",), ("descending",), ("level",), ("with", "you"), ("checking", "in"), ("leaving",),
                     ("passing",), ("through",), ("out", "of"))
    if _has_any(tokens, *checkin_words) and not altitudes(tokens):
        reported = _reported_altitudes(tokens)
        add("checkin", altitude=reported[0] if reported else None, assigned=reported[1] if len(reported) > 1 else None,
            atis=_atis(tokens))
    if not matches and _has_any(tokens, ("roger",), ("wilco",), ("copy",), ("will", "comply"), ("disregard",), ("thanks",),
                                ("thank", "you"), ("affirm",), ("affirmative",), ("copy", "that"), ("good", "day")):
        add("acknowledge")
    return matches


# Intents that legitimately appear together; the first one is used.
COMPATIBLE = [
    {"request_taxi_parking", "clear_of_runway"},
    {"going_around", "report_final"},  # "going around, runway 08": no longer a final report
    {"request_vectors", "request_runway"},  # "request vectors for the ILS 26"
    {"request_direct", "request_runway"},
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
