"""Checks that a value the model reports was actually said.

The model may understand a transmission; it may not invent one. Every number it
returns must appear in the pilot's words (after number normalization), and every
yes/no phrase needs a keyword from the transmission. Anything else is dropped.
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
    digits = digits.lstrip("0") or "0"
    if any((n.split(".")[0].lstrip("0") or "0") == digits for n in number_tokens(tokens)):
        return True
    return digits in digit_stream(tokens)


def grounded(element: str, value: Any, tokens: list[Token]) -> bool:
    """True if ``value`` (already parsed to the element's type) was said in ``tokens``."""
    words = {t.text for t in tokens if t.kind == "word"}
    if element in PHRASE_STEMS:
        return value is True and bool(words & set(PHRASE_STEMS[element]))
    if element in ("runway", "hold_short"):
        digits = "".join(c for c in str(value) if c.isdigit())
        return bool(digits) and _said_number(digits, tokens)
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
