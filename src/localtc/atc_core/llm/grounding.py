"""Checks that what the model reports was actually said.

The model may understand a transmission; it may not invent one. Every number it
returns must appear in the pilot's words (after number normalization, with the
callsign taken out), every yes/no phrase needs a keyword from the transmission,
and every intent needs a word that signals it.
"""

from typing import Any

from localtc.atc_core.readback.normalize import Token

# Readback phrases the model can confirm, and words at least one of which the pilot must have said.
PHRASE_STEMS: dict[str, tuple[str, ...]] = {
    "cleared_for_takeoff": ("takeoff", "take", "departure"),
    "line_up_and_wait": ("line", "lineup", "lining", "position"),
    "cleared_to_land": ("land", "landing"),
    "hold_position": ("hold", "holding"),
}


def number_tokens(tokens: list[Token]) -> list[str]:
    return [t.text.replace(",", "") for t in tokens if t.kind == "number"]


def digit_stream(tokens: list[Token]) -> str:
    return "".join(n.replace(".", "") for n in number_tokens(tokens))


def _said_number(digits: str, tokens: list[Token]) -> bool:
    """A whole number the pilot said. Not a substring: runway "6" is not in callsign "69"."""
    digits = digits.lstrip("0") or "0"
    return any((n.split(".")[0].lstrip("0") or "0") == digits for n in number_tokens(tokens))


def grounded(element: str, value: Any, tokens: list[Token]) -> bool:
    """True if ``value`` (already parsed to the element's type) was said in ``tokens``."""
    words = {t.text for t in tokens if t.kind == "word"}
    if element in PHRASE_STEMS:
        return value is True and bool(words & set(PHRASE_STEMS[element]))
    if element in ("runway", "hold_short"):
        digits = "".join(c for c in str(value) if c.isdigit())
        side = str(value)[len(digits):].upper()
        sides = {"L": {"left", "l"}, "R": {"right", "r"}, "C": {"center", "centre", "c"}}
        said_side = not side or bool(sides[side] & ({t.text for t in tokens}))  # "06" must not come back as "06R"
        return bool(digits) and _said_number(digits, tokens) and said_side
    if element == "frequency":
        full = f"{float(value):.3f}".replace(".", "").rstrip("0")
        name = full[:5] if len(full) == 6 else full  # 120.425 is also said "one two zero point four two"
        stream = digit_stream(tokens)
        return full in stream or name in stream
    if element == "squawk":
        return str(value) in digit_stream(tokens)
    if element in ("altitude", "cruise"):
        feet = int(value)
        if _said_number(str(feet), tokens):
            return True
        return "level" in words and feet % 100 == 0 and _said_number(str(feet // 100), tokens)
    if element == "heading":
        return _said_number(str(int(value)), tokens)
    if element == "atis":
        letter = str(value).lower()
        return any(t.text == letter for t in tokens if t.kind == "letter") or letter in words
    if element == "approach":
        kind_words = {"ILS": {"ils", "i", "localizer"}, "RNAV": {"rnav", "gps", "r", "area"}, "VISUAL": {"visual"}}
        return grounded("runway", value.runway, tokens) and bool(words & kind_words.get(value.kind, set()))
    return False


# Words that must appear for the model's intent to be believed. A small model reaches for
# request_altitude whenever an altitude is mentioned, including plain check-ins.
REQUEST_WORDS = {"request", "requesting", "could", "can", "like", "want", "chance", "higher", "lower", "unable"}
ALTITUDE_WORDS = {"higher", "lower", "climb", "descend", "descent", "altitude", "level", "thousand", "hundred", "maintain",
                  "feet"}
INTENT_CUES: dict[str, tuple[set[str], ...]] = {  # every set needs at least one word
    "request_altitude": (REQUEST_WORDS, ALTITUDE_WORDS),
    "request_ifr_clearance": ({"ifr", "clearance", "copy", "cleared", "plan"},),
    "ready_to_taxi": ({"taxi", "ready", "push", "pushback"},),
    "ready_for_departure": ({"ready", "holding", "hold", "departure", "takeoff", "go", "short"},),
    "report_final": ({"final", "mile", "miles", "out", "inbound", "ils", "approach", "established", "localizer"},),
    "clear_of_runway": ({"clear", "vacated", "off", "exited"},),
    "request_taxi_parking": ({"parking", "gate", "ramp", "stand", "apron", "taxi"},),
    # A request ATC can't grant still has to be a request; noise the model can't place is not one.
    "other": (REQUEST_WORDS | {"requesting", "direct", "deviation", "deviate", "vectors", "hold", "permission", "we'd",
                               "would", "need", "may"},),
}


def missing_cue(intent: str, tokens: list[Token]) -> str | None:
    """None if the words fit the intent; otherwise what's missing, for the retry message."""
    words = {t.text for t in tokens if t.kind == "word"}
    for cues in INTENT_CUES.get(intent, ()):
        if not words & cues:
            return f"{intent} needs a word like {', '.join(sorted(cues)[:5])}, and the pilot said none"
    return None
