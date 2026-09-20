"""Readback element extractors: find values for each element anywhere in a normalized transmission.

Each extractor returns every candidate it finds. The parser then decides:
the expected value among the candidates means correct, other candidates mean
incorrect, and no candidates means missing.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from localtc.atc_core.readback.normalize import Token
from localtc.atc_core.values import Approach, Callsign

Extractor = Callable[[list[Token], Any], list[Any]]


@dataclass(frozen=True)
class Unclear:
    """A value that isn't a valid one but is probably the expected one misheard ("12.1" for 120.1)."""

    heard: str

    def __str__(self) -> str:
        return self.heard

SIDE_WORDS = {"left": "L", "right": "R", "center": "C", "centre": "C", "l": "L", "r": "R", "c": "C"}
NOT_CALLSIGN_WORDS = {
    "and", "to", "via", "for", "at", "of", "on", "in", "is", "by", "up", "we", "i", "a", "an", "it", "as", "or", "so",
    "go", "be", "do", "no", "ok", "hi", "my",
}


def _words(tokens: list[Token]) -> list[str]:
    return [t.text for t in tokens]


def _find_phrase(tokens: list[Token], phrase: tuple[str, ...], start: int = 0) -> list[int]:
    """Indices just after each occurrence of ``phrase`` (word tokens)."""
    words = _words(tokens)
    n = len(phrase)
    return [i + n for i in range(start, len(words) - n + 1) if tuple(words[i : i + n]) == phrase]


def _has_any(tokens: list[Token], *phrases: tuple[str, ...]) -> bool:
    return any(_find_phrase(tokens, p) for p in phrases)


def _number(tokens: list[Token], i: int) -> str | None:
    return tokens[i].text if 0 <= i < len(tokens) and tokens[i].kind == "number" else None


# --- runways ------------------------------------------------------------------------


def _runway_at(tokens: list[Token], i: int) -> tuple[str, int] | None:
    """A runway ident starting at token ``i``: <34> [left|l|L]."""
    num = _number(tokens, i)
    if num is None or not num.isdigit() or not 1 <= int(num) <= 36:
        return None
    ident, used = f"{int(num):02d}", 1
    if i + 1 < len(tokens) and tokens[i + 1].text in SIDE_WORDS:
        ident += SIDE_WORDS[tokens[i + 1].text]
        used = 2
    return ident, used


def runways(tokens: list[Token], expected: Any = None) -> list[str]:
    """Runway assignments; a runway named in "hold short (of) runway X" is a hold-short, not an assignment."""
    words = _words(tokens)
    found = []
    for i in _find_phrase(tokens, ("runway",)):
        before = words[max(0, i - 3) : i - 1]
        if before[-1:] == ["short"] or before[-2:] == ["short", "of"]:
            continue
        if (hit := _runway_at(tokens, i)) is not None:
            found.append(hit[0])
    # A side makes a bare number a runway too: "34 left", or "34L" written (a word "l", not the letter "Lima").
    for i, token in enumerate(tokens):
        if i > 0 and tokens[i - 1].text in ("runway", "short", "of"):
            continue  # handled above, or a hold-short
        side = tokens[i + 1] if i + 1 < len(tokens) else None
        if token.kind == "number" and side is not None and side.kind == "word" and side.text in SIDE_WORDS:
            hit = _runway_at(tokens, i)
            if hit and hit[0] not in found:
                found.append(hit[0])
    # Straight after the clearance, a number is the runway: "cleared to land 06".
    for phrase in (("to", "land"), ("for", "takeoff"), ("for", "take", "off")):
        for i in _find_phrase(tokens, phrase):
            if i < len(tokens) and tokens[i].kind == "number" and (hit := _runway_at(tokens, i)) and hit[0] not in found:
                found.append(hit[0])
    return found


def hold_short(tokens: list[Token], expected: Any = None) -> list[str]:
    found = []
    for i in _find_phrase(tokens, ("hold", "short")) + _find_phrase(tokens, ("holding", "short")):
        j = i
        while j < len(tokens) and tokens[j].text in ("of", "runway"):
            j += 1
        if (hit := _runway_at(tokens, j)) is not None:
            found.append(hit[0])
    return found


def normalize_runway(ident: str) -> str:
    digits = "".join(c for c in ident if c.isdigit())
    return f"{int(digits):02d}{ident[len(digits):].upper()}" if digits else ident.upper()


# --- numbers ---------------------------------------------------------------------------


MAX_ALTITUDE_FT = 60000


def _altitude_value(tokens: list[Token], i: int) -> int | None:
    if i < len(tokens) and tokens[i].text == "flight" and i + 1 < len(tokens) and tokens[i + 1].text == "level":
        num = _number(tokens, i + 2)
        if num is None or not num.isdigit():
            return None
        # A flight level is three digits. Words that follow run into it when they are digits too:
        # "flight level three five zero, one zero minutes after departure" is FL350, not FL35010.
        return int(num[:3]) * 100
    num = _number(tokens, i)
    if num is None or not num.isdigit():
        return None
    value = int(num)
    if value < 100:  # "maintain one zero" is not an altitude; "maintain 5" isn't either
        return None
    return value if value <= MAX_ALTITUDE_FT else None


def altitudes(tokens: list[Token], expected: Any = None) -> list[int]:
    found = []
    starts = _find_phrase(tokens, ("maintain",)) + [  # "climb to 3,200", "descend 3,000"
        i for word in ("climb", "descend")  # not "climbing 5,000": that's a check-in report
        for i in _find_phrase(tokens, (word, "to")) + _find_phrase(tokens, (word,))
    ]
    for i in sorted(set(starts)):
        if (value := _altitude_value(tokens, i)) is not None and value not in found:
            found.append(value)
    for i, token in enumerate(tokens):  # "5000 feet"
        if token.kind == "number" and i + 1 < len(tokens) and tokens[i + 1].text in ("feet", "ft"):
            if (value := _altitude_value(tokens, i)) is not None and value not in found:
                found.append(value)
    return found


def cruise_altitudes(tokens: list[Token], expected: Any = None) -> list[int]:
    return [v for i in _find_phrase(tokens, ("expect",)) if (v := _altitude_value(tokens, i)) is not None]


def _as_frequency(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    if "." not in text and text.isdigit():
        # "1202" / "124675": pilots often drop "point"
        if len(text) in (4, 5, 6) and 118 <= int(text[:3]) <= 136 and not text.endswith("00"):
            value = float(f"{text[:3]}.{text[3:]}")
        else:
            return None
    return round(value, 3) if 118.0 <= value <= 136.975 else None


def _dropped_digit(text: str, expected: Any) -> bool:
    """ "12.1" for 120.1, "119.7" for 119.75: speech-to-text lost one digit of the expected frequency."""
    try:
        want = f"{float(expected):.3f}".rstrip("0").rstrip(".").replace(".", "")
    except (TypeError, ValueError):
        return False
    heard = text.replace(".", "").replace(",", "")
    if not heard.isdigit() or len(heard) != len(want) - 1 or len(heard) < 3:
        return False
    return any(want[:i] + want[i + 1:] == heard for i in range(len(want)))


def frequencies(tokens: list[Token], expected: Any = None) -> list[float]:
    found = []
    for i, token in enumerate(tokens):
        if token.kind != "number":
            continue
        if i > 0 and tokens[i - 1].text in ("squawk", "code", "runway", "maintain", "heading", "expect", "level", "climbing"):
            continue
        if (value := _as_frequency(token.text)) is not None:
            found.append(value)
        elif token.text.isdigit() and isinstance(expected, float) and expected == int(token.text):
            found.append(expected)  # "tower on 121" for 121.0
        elif "." in token.text and expected is not None and _dropped_digit(token.text, expected):
            found.append(Unclear(token.text))
    return found


def squawks(tokens: list[Token], expected: Any = None) -> list[str]:
    found = []
    for i in _find_phrase(tokens, ("squawk",)) + _find_phrase(tokens, ("code",)) + _find_phrase(tokens, ("transponder",)):
        num = _number(tokens, i)
        if num and len(num) == 4 and all(c in "01234567" for c in num):
            found.append(num)
    return found


def headings(tokens: list[Token], expected: Any = None) -> list[int]:
    found = []
    for i in _find_phrase(tokens, ("heading",)):
        num = _number(tokens, i)
        if num and num.isdigit() and 0 <= int(num) <= 360:
            found.append(int(num) % 360 or 360)
    return found


# --- taxi routes, approaches, callsigns -------------------------------------------------------------

# Words that sit between taxiway names without ending the route: "via B, holding point C",
# "via A then B". "hold short runway 25L" ends it anyway, because "runway" is not one of these.
ROUTE_FILLER = ("and", "then", "hold", "holding", "short", "point", "at", "to")


def taxi_routes(tokens: list[Token], expected: Any = None) -> list[tuple[str, ...]]:
    found = []
    for start in _find_phrase(tokens, ("via",)):
        names: list[str] = []
        i = start
        while i < len(tokens):
            token = tokens[i]
            letter = token.text if token.kind == "letter" or (token.kind == "word" and len(token.text) == 1) else None
            if letter is None:
                if token.kind == "word" and token.text in ROUTE_FILLER and names:
                    i += 1
                    continue
                break
            name = letter.upper()
            if i + 1 < len(tokens) and tokens[i + 1].kind == "number" and len(tokens[i + 1].text) <= 2:
                name += tokens[i + 1].text
                i += 1
            names.append(name)
            i += 1
        if names:
            found.append(tuple(names))
    return found


PROCEDURE_END = ("departure", "arrival", "transition")


def procedures(tokens: list[Token], expected: Any = None) -> list[str]:
    """A SID or STAR read back: "via the montn two departure" -> "MONTN2".

    Only counted when the naming word follows, so a taxi route ("taxi via bravo, charlie") is never
    mistaken for one.
    """
    found = []
    for start in _find_phrase(tokens, ("via",)) + _find_phrase(tokens, ("expect",)):
        i = start
        if i < len(tokens) and tokens[i].text == "the":
            i += 1
        name = ""
        while i < len(tokens) and tokens[i].text not in PROCEDURE_END:
            token = tokens[i]
            if token.kind not in ("word", "number", "letter") or not token.text.isalnum():
                break
            name += token.text.upper()
            i += 1
        if name and i < len(tokens) and tokens[i].text in PROCEDURE_END and name not in found:
            found.append(name)
    return found


def taxi_route_matches(heard: Any, expected: Any) -> bool:
    """Does the pilot's route agree with the one ATC gave?

    An exact comparison is wrong for one reason: a taxiway whose name is more than one letter is
    spoken letter by letter, so "taxi via delta golf, charlie hotel" comes back as D, G, C, H and
    has to be glued into DG, CH before it can be compared. The route is walked in order, taking as
    many letters as each assigned name needs.
    """
    said = [str(n).upper() for n in heard]
    want = [str(n).upper() for n in expected]
    if said == want:
        return True
    i = 0
    for name in want:
        size = next((k for k in range(1, len(name) + 1)  # one entry, or several letters spelling this name
                     if i + k <= len(said) and "".join(said[i : i + k]) == name), None)
        if size is None:
            return False
        i += size
    return i == len(said)


APPROACH_KINDS = {"ils": "ILS", "rnav": "RNAV", "gps": "RNAV", "visual": "VISUAL", "localizer": "LOC", "loc": "LOC"}


def approaches(tokens: list[Token], expected: Any = None) -> list[Approach]:
    words = _words(tokens)
    starts: list[tuple[int, str]] = []  # (index just after the approach type, kind)
    for i, word in enumerate(words):
        if word in APPROACH_KINDS:
            starts.append((i + 1, APPROACH_KINDS[word]))
        elif words[i : i + 3] == ["i", "l", "s"]:  # spelled out: "I L S" / "india lima sierra"
            starts.append((i + 3, "ILS"))
        elif words[i : i + 2] == ["r", "nav"]:
            starts.append((i + 2, "RNAV"))
    found = []
    for after, kind in starts:
        j = after
        while j < len(tokens) and tokens[j].text in ("approach", "runway", "to", "y", "z"):
            j += 1
        if (hit := _runway_at(tokens, j)) is not None:
            found.append(Approach(kind=kind, runway=hit[0]))
    return found


def _compact_segments(tokens: list[Token]) -> list[str]:
    """Runs of letters/digits (and short words) glued together: ``N 172 L T`` -> ``n172lt``."""
    segments, current = [], ""
    for token in tokens:
        if token.kind in ("letter", "number") or (
            token.kind == "word" and len(token.text) <= 3 and token.text not in NOT_CALLSIGN_WORDS
        ):
            current += token.text.replace(".", "")
        else:
            if current:
                segments.append(current)
            current = ""
    if current:
        segments.append(current)
    return segments


def callsigns(tokens: list[Token], expected: Callsign | None = None) -> list[Callsign]:
    if expected is None:
        return []
    words = _words(tokens)
    if expected.is_airline:
        telephony = expected.telephony.lower().split()
        n = len(telephony)
        for i in range(len(words) - n + 1):
            if words[i : i + n] == telephony and (num := _number(tokens, i + n)) and num.lstrip("0") == expected.flight_number.lstrip("0"):
                return [expected]
        return []
    ident = expected.ident.lower().replace("-", "")
    suffix = expected.suffix.lower()
    for segment in _compact_segments(tokens):
        if segment.endswith(ident) or segment.endswith(ident[1:]) or segment.endswith(suffix):
            return [expected]
    return []


def without_callsign(tokens: list[Token], callsign: Callsign | None) -> list[Token]:
    """The tokens with the callsign taken out, so its letters can't be read as taxiways
    ("taxi via alpha four, delta papa six niner" is route A4 from DP69, not A4, D, P69)."""
    if callsign is None or callsign.is_airline:
        return tokens
    ident = callsign.ident.lower().replace("-", "")
    forms = {ident, callsign.suffix.lower()} | ({ident[1:]} if len(ident) > 4 else set())
    best: tuple[int, int] | None = None  # the longest span that spells the callsign; the last one on a tie
    for first in range(len(tokens)):
        if tokens[first].kind not in ("letter", "number") and len(tokens[first].text) > 2:
            continue
        compact = ""
        for last in range(first, min(len(tokens), first + 10)):
            compact += tokens[last].text.replace(".", "")
            if len(compact) > len(ident):
                break
            if compact in forms and (best is None or last - first >= best[1] - best[0]):
                best = (first, last)
    return tokens if best is None else tokens[: best[0]] + tokens[best[1] + 1 :]


def phrase(*phrases: tuple[str, ...]) -> Extractor:
    def extract(tokens: list[Token], expected: Any = None) -> list[bool]:
        return [True] if _has_any(tokens, *phrases) else []

    return extract


def destination(tokens: list[Token], expected: Any = None) -> list[str]:
    if not isinstance(expected, str):
        return []
    words = set(_words(tokens))
    significant = [w for w in expected.lower().split() if w not in ("field", "airport", "international", "county")]
    return [expected] if significant and all(w in words for w in significant) else []


def fixes(tokens: list[Token], expected: Any = None) -> list[str]:
    """ "direct BLAKO": the words after "direct", compared loosely with the fix ATC gave."""
    words = _words(tokens)
    found = []
    for i in _find_phrase(tokens, ("direct",)):
        rest = [w for w in words[i : i + 4] if w not in ("to", "the")]
        if isinstance(expected, str):
            wanted = [w.lower() for w in expected.replace(" airport", "").split()]
            if wanted and all(w in rest or w in words for w in wanted):
                found.append(expected)
                continue
        if rest:
            found.append(" ".join(rest[:2]).upper())
    return found


ELEMENTS: dict[str, Extractor] = {
    "runway": runways,
    "hold_short": hold_short,
    "altitude": altitudes,
    "cruise": cruise_altitudes,
    "frequency": frequencies,
    "squawk": squawks,
    "heading": headings,
    "taxi_route": taxi_routes,
    "approach": approaches,
    "procedure": procedures,
    "destination": destination,
    "fix": fixes,
    "callsign": callsigns,
    "cleared_for_takeoff": phrase(("cleared", "for", "takeoff"), ("cleared", "takeoff"), ("cleared", "for", "take", "off"),
                                 ("clear", "for", "takeoff"), ("clear", "takeoff"), ("clear", "for", "take", "off")),
    "line_up_and_wait": phrase(("line", "up", "and", "wait"), ("line", "up", "wait"), ("lineup", "and", "wait"), ("position", "and", "hold")),
    "cleared_to_land": phrase(("cleared", "to", "land"), ("cleared", "land"), ("clear", "to", "land")),
    "hold_position": phrase(("hold", "position"), ("holding", "position")),
}


CORRECTIONS = (("correction",), ("i", "mean"), ("excuse", "me"), ("sorry",))


def candidates(element: str, tokens: list[Token], expected: Any) -> list[Any]:
    """What the pilot said for ``element``. After "correction" (or "I mean", "excuse me") only the
    corrected part counts: "cleared to land 08 left, correction 08" is runway 08."""
    found = ELEMENTS[element](tokens, expected)
    fixes = [(end - len(phrase), end) for phrase in CORRECTIONS for end in _find_phrase(tokens, phrase)]
    if not found or not fixes:
        return found
    start, end = max(fixes)
    if corrected := ELEMENTS[element](tokens[end:], expected):
        return corrected
    # A bare value after the correction replaces the one before it: "cleared to land 08 left, correction 08".
    last = next((i for i in range(start - 1, -1, -1) if tokens[i].kind == "number"), None)
    if last is not None and end < len(tokens) and tokens[end].kind == "number":
        if corrected := ELEMENTS[element](tokens[:last] + tokens[end:], expected):
            return corrected
    return found


def values_close(element: str, heard: Any, expected: Any) -> bool:
    """Not what ATC said, but probably it, misheard or misspoken: worth a "confirm", not a "negative"."""
    if isinstance(heard, Unclear):
        return True
    if element in ("runway", "hold_short"):
        heard, expected = normalize_runway(heard), normalize_runway(expected)
        return expected[-1:].isdigit() and heard != expected and heard.rstrip("LRC") == expected  # "08 left" for 08
    if element in ("altitude", "cruise"):
        return int(heard) % 100 != 0 and abs(int(heard) - int(expected)) < 50  # "1508" for 1,500
    return False


def values_equal(element: str, heard: Any, expected: Any) -> bool:
    if isinstance(heard, Unclear):
        return False
    if element == "frequency":
        return abs(float(heard) - float(expected)) < 0.0005
    if element in ("runway", "hold_short"):
        return normalize_runway(heard) == normalize_runway(expected)
    if element == "approach":
        return heard.runway == normalize_runway(expected.runway) and (heard.kind == expected.kind or heard.kind == "LOC")
    if element == "altitude" or element == "cruise":
        return int(heard) == int(expected)
    if element == "taxi_route":
        return taxi_route_matches(heard, expected)
    if element == "fix":
        return str(heard).lower() == str(expected).lower()
    return heard == expected
