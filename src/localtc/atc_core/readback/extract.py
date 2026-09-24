"""Readback element extractors: find values for each element anywhere in a normalized transmission.

Each extractor returns every candidate it finds. The parser then decides:
the expected value among the candidates means correct, other candidates mean
incorrect, and no candidates means missing.
"""

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from localtc.atc_core.readback.normalize import FILLERS, Token
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
    """Indices just after each occurrence of ``phrase`` (word tokens).

    Filler words are dropped from the phrase as well as from the tokens: normalize() never keeps
    "the", so a phrase written with it ("get the departure") would otherwise never match at all."""
    words = _words(tokens)
    phrase = tuple(w for w in phrase if w not in FILLERS)
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
    if found or expected is None:
        return found
    if _has_any(tokens, *CONTRARY_TO_HOLDING):
        # "Cleared for takeoff runway 27" answering "hold short runway 27" names the right runway and
        # the opposite instruction: the most dangerous readback there is, never a hold short.
        return found
    # "Hold short" is two quiet words that speech-to-text mangles into one ("holshore", "holtoire",
    # even "portrait"), and no list of spellings will cover them all. The runway is the part that
    # matters and it comes through clearly, so naming the one ATC said to hold short of is enough.
    wanted = normalize_runway(str(expected))
    bare = wanted.rstrip("LRC")
    for i, token in enumerate(tokens):
        if token.kind != "number":
            continue
        hit = _runway_at(tokens, i)
        if hit is None:
            continue
        heard = normalize_runway(hit[0])
        # A runway with no side takes the next letter it hears, and the next letter is often the ATIS
        # ("hold short runway 13 ... Romeo is current" reads as 13R), so compare without one.
        if heard == wanted or (wanted == bare and heard.rstrip("LRC") == bare):
            return [wanted]
    return found


# Words that say the aircraft is going onto the runway, which a hold-short readback can't contain.
CONTRARY_TO_HOLDING = (("takeoff",), ("take", "off"), ("line", "up"), ("lining", "up"), ("cross",), ("crossing",),
                       ("cleared", "to", "land"), ("clear", "to", "land"))


def normalize_runway(ident: str) -> str:
    digits = "".join(c for c in ident if c.isdigit())
    return f"{int(digits):02d}{ident[len(digits):].upper()}" if digits else ident.upper()


# --- numbers ---------------------------------------------------------------------------


MAX_ALTITUDE_FT = 60000


def _altitude_value(tokens: list[Token], i: int) -> int | None:
    # "flight level 350", or "FL350" the way it is written on the screen and typed back.
    spelled = i < len(tokens) and tokens[i].text == "flight" and i + 1 < len(tokens) and tokens[i + 1].text == "level"
    short = i < len(tokens) and tokens[i].text == "fl" and _number(tokens, i + 1) is not None
    if spelled or short:
        num = _number(tokens, i + (2 if spelled else 1))
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


TRANSITION_FT = 18000  # at or above this ATC talks in flight levels, so a bare "350" is one


def _bare_flight_level(value: int, expected: Any) -> int:
    """ "maintain 350" for FL350: above the transition altitude the words "flight level" get dropped.

    Only when ATC's own value is a flight level, so "maintain 500" for a helicopter at 500 feet keeps
    its meaning and a genuinely wrong number is still wrong.
    """
    try:
        wanted = int(expected)
    except (TypeError, ValueError):
        return value
    return wanted if 100 <= value <= 600 and wanted >= TRANSITION_FT and value * 100 == wanted else value


def altitudes(tokens: list[Token], expected: Any = None) -> list[int]:
    found = []
    starts = _find_phrase(tokens, ("maintain",)) + [  # "climb to 3,200", "descend 3,000"
        i for word in ("climb", "descend")  # not "climbing 5,000": that's a check-in report
        for i in _find_phrase(tokens, (word, "to")) + _find_phrase(tokens, (word,))
    ] + [  # VFR limits: "at or below 4,500" (FAA), "not above 3,000 feet" (ICAO)
        i for phrase in (("at", "or", "below"), ("not", "above")) for i in _find_phrase(tokens, phrase)
    ]

    for i in sorted(set(starts)):
        if (value := _altitude_value(tokens, i)) is not None:
            if (value := _bare_flight_level(value, expected)) not in found:
                found.append(value)
    for i, token in enumerate(tokens):  # "5000 feet"
        if token.kind == "number" and i + 1 < len(tokens) and tokens[i + 1].text in ("feet", "ft"):
            if (value := _altitude_value(tokens, i)) is not None and value not in found:
                found.append(value)
    if not found and expected is not None:
        # "Climb and maintain" came out of speech-to-text as "climateane", leaving only "flight level
        # three five zero" to go on. A flight level that is the one ATC gave is the readback, whatever
        # the words before it became. Only ever the expected value, so a wrong level stays wrong, and
        # only a stated flight level, so "up to five thousand" is still too vague to accept.
        levels = [i for i, token in enumerate(tokens) if token.text == "flight" and i + 1 < len(tokens)
                  and tokens[i + 1].text == "level" and (i == 0 or tokens[i - 1].text != "expect")]
        if any((value := _altitude_value(tokens, i)) is not None and _bare_flight_level(value, expected) == int(expected)
               for i in levels):
            found.append(int(expected))
    return found


def cruise_altitudes(tokens: list[Token], expected: Any = None) -> list[int]:
    found = []
    for i in _find_phrase(tokens, ("expect",)):
        if (value := _altitude_value(tokens, i)) is not None:
            if (value := _bare_flight_level(value, expected)) not in found:
                found.append(value)
    return found


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


SPEED_WORDS = (("speed",), ("knots",), ("kts",), ("slow",), ("maintain",), ("reduce",), ("increase",))


def speeds(tokens: list[Token], expected: Any = None) -> list[int]:
    """An assigned airspeed: "reduce speed to 210 knots", "210 knots", "maintain 180"."""
    found = []
    for i, token in enumerate(tokens):
        if token.kind != "number" or not token.text.isdigit():
            continue
        value = int(token.text)
        if not 60 <= value <= 400:
            continue
        near = [tok.text for tok in tokens[max(0, i - 3) : i + 3]]
        if any(word[0] in near for word in SPEED_WORDS) and value not in found:
            found.append(value)
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
    names = {str(n).lower() for n in expected} if expected else set()
    found = [route for start in _find_phrase(tokens, ("via",)) if (route := _route_at(tokens, start, names)[0])]
    if not found and expected is not None:
        # "Taxi runway 27, B, B6, C6, C, C1": read back without "via". With a route to compare against, every
        # taxiway name said is the route, in order, skipping the letter of a runway ("25 L") and a callsign
        # ("Delta 2543"); words in between are speech-to-text slips ("Foxtrop") that the comparison forgives.
        skip = _runway_letters(tokens)
        route: list[str] = []
        i = 0
        while i < len(tokens):
            if i not in skip and _is_route_token(tokens[i], names) and not _callsign_letter(tokens, i):
                taken, end = _route_at(tokens, i, names, filler=False)
                if taken:
                    route.extend(taken)
                    i = end
                    continue
            i += 1
        if len(route) >= 2:
            found.append(tuple(route))
    return found


def _is_route_token(token: Token, names: frozenset[str] | set[str] = frozenset()) -> bool:
    return token.kind == "letter" or (token.kind == "word" and (len(token.text) == 1 or token.text in names))


def _callsign_letter(tokens: list[Token], i: int) -> bool:
    """ "Delta 2543", "November 172": a letter then a long number is a callsign, not a taxiway."""
    return i + 1 < len(tokens) and tokens[i + 1].kind == "number" and len(tokens[i + 1].text) >= 3


def _runway_letters(tokens: list[Token]) -> set[int]:
    """Indices of the letters that are part of a runway ("runway 25 L"), not a taxiway."""
    skip = set()
    for i, token in enumerate(tokens[:-1]):
        if token.kind == "number" and token.text.isdigit() and 1 <= int(token.text) <= 36 \
                and tokens[i + 1].text in ("l", "r", "c") and (i > 0 and tokens[i - 1].text == "runway"):
            skip.add(i + 1)
    return skip


def _route_at(tokens: list[Token], start: int, names: set[str] | frozenset[str] = frozenset(), *,
              filler: bool = True) -> tuple[tuple[str, ...], int]:
    """Taxiway names from ``start``: letters, each maybe with a number ("B6"), joined by filler words; and the
    index just past them."""
    route: list[str] = []
    i = start
    while i < len(tokens):
        token = tokens[i]
        if not _is_route_token(token, names) or _callsign_letter(tokens, i):
            if filler and token.kind == "word" and token.text in ROUTE_FILLER and route:
                i += 1
                continue
            break
        name = token.text.upper()
        if i + 1 < len(tokens) and tokens[i + 1].kind == "number" and len(tokens[i + 1].text) <= 2:
            name += tokens[i + 1].text
            i += 1
        route.append(name)
        i += 1
        if not filler and i < len(tokens) and not _is_route_token(tokens[i], names):
            break
    return tuple(route), i


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
    # Leniency for speech-to-text on a long route: "Alpha November" heard as "off of November", a taxiway
    # swallowed. A letter dropped from a name costs half, a name missing one, a name that isn't in the route
    # one and a half (a wrong taxiway is never forgiven on its own). Short routes must be right.
    allowed = 0.5 if len(want) < 4 else len(want) // 3
    return route_cost(said, want) <= allowed


def route_cost(said: list[str], want: list[str]) -> float:
    """How far a heard route is from the assigned one (see ``taxi_route_matches``); 0 when it spells it."""
    inf = float("inf")
    cost = [[inf] * (len(want) + 1) for _ in range(len(said) + 1)]
    cost[0][0] = 0.0
    for i in range(len(said) + 1):
        for j in range(len(want) + 1):
            here = cost[i][j]
            if here == inf:
                continue
            if j < len(want):
                cost[i][j + 1] = min(cost[i][j + 1], here + 1.0)  # a name not read back
            if i < len(said):
                cost[i + 1][j] = min(cost[i + 1][j], here + 1.5)  # a name that isn't in the route
            if j < len(want):
                name = want[j]
                shorter = {name[:m] + name[m + 1:] for m in range(len(name))} if len(name) > 1 else set()
                for k in range(1, len(name) + 1):  # one entry, or several letters spelling this name
                    if i + k > len(said):
                        break
                    spelled = "".join(said[i : i + k])
                    if spelled == name:
                        cost[i + k][j + 1] = min(cost[i + k][j + 1], here)
                    elif spelled in shorter:
                        cost[i + k][j + 1] = min(cost[i + k][j + 1], here + 0.5)
    return cost[len(said)][len(want)]


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
    if callsign is None:
        return tokens
    if callsign.is_airline:
        # "Air Canada 216, flight level 350": the flight number is not an altitude, a heading or a speed.
        telephony = callsign.telephony.lower().split()
        number = callsign.flight_number.lstrip("0")
        words = _words(tokens)
        for i in range(len(words) - len(telephony)):
            if words[i : i + len(telephony)] == telephony:
                end = i + len(telephony)
                if end < len(tokens) and tokens[end].kind == "number" and tokens[end].text.lstrip("0") == number:
                    end += 1
                return tokens[:i] + tokens[end:]
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
    "speed": speeds,
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
    "descend_via": phrase(("descend", "via"), ("descending", "via"), ("descent", "via"), ("down", "via")),
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
    if element == "procedure":
        return procedure_matches(str(heard), str(expected))
    return heard == expected


def procedure_matches(heard: str, expected: str) -> bool:
    """A SID or STAR as speech-to-text spells it. The five-letter names are made to be said like words
    (HYDRR, "Hydra"; ZZOOO, "Zoo"; HOGGZ, "Hoggs"), so they never come back spelled as charted. The
    number must match; the name only has to sound like it: doubled letters count once and the letters
    left have to be mostly the same, in order."""

    def split(name: str) -> tuple[str, str]:
        letters = "".join(c for c in name.upper() if c.isalpha())
        squeezed = "".join(c for i, c in enumerate(letters) if i == 0 or c != letters[i - 1])
        return squeezed, "".join(c for c in name if c.isdigit())

    (heard_name, heard_number), (name, number) = split(heard), split(expected)
    if heard_number != number:
        return False
    return heard_name == name or difflib.SequenceMatcher(None, heard_name, name).ratio() >= PROCEDURE_LIKENESS


PROCEDURE_LIKENESS = 0.6  # how alike two spellings of a procedure's name must be to be the same one
