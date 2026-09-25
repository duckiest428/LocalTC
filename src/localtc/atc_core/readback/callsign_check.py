"""Whose call was that? The callsign heard in a transmission, judged against the flight's own.

A controller listens for the callsign before anything else. Speech-to-text mangles it all the time ("Air
Canada's a 779", "Canada 779", the airline left off a readback), and none of that is worth a word: the
controller knows who they're talking to. What they do pick up on:

- **another aircraft**: another airline's name with a flight number ("Westjet 452"), or the right airline
  with a number that isn't this flight's ("Air Canada 452"). It isn't this flight's call, so it isn't
  answered as one: "station calling Montreal Centre, say again your callsign".
- **close but not quite**: one digit off or two swapped ("Air Canada 797" for 779). The controller asks
  before acting on an initial call; a readback, which they are waiting for from this flight, they take.
"""

from typing import Literal

from localtc.atc_core.airlines import TELEPHONY
from localtc.atc_core.readback.normalize import Token
from localtc.atc_core.values import Callsign

Verdict = Literal["ours", "absent", "close", "other"]

# Airline names as heard, longest first so "Air Canada" wins over a stray "Air": the words of each.
_AIRLINES: list[tuple[str, ...]] = sorted({tuple(name.lower().split()) for name in TELEPHONY.values()}, key=len, reverse=True)


# Airline names that are also spelling-alphabet words: the normaliser has made them letters ("Delta" -> D).
_SPELLED = {"d": "delta"}


def _words(tokens: list[Token]) -> list[str]:
    return [t.text.lower() if t.kind == "word" else _SPELLED.get(t.text, t.text) if t.kind == "letter" else t.text
            for t in tokens]


def _flight_number(tokens: list[Token], i: int) -> str | None:
    """The flight number starting at token ``i``: a number of 1-4 digits (not a frequency or an altitude)."""
    if i >= len(tokens) or tokens[i].kind != "number":
        return None
    text = tokens[i].text.replace(",", "")
    return text if text.isdigit() and 1 <= len(text) <= 4 else None


def _distance(a: str, b: str) -> int:
    """Edits between two flight numbers, a swap of neighbours counting as one (Damerau-Levenshtein)."""
    d = [[max(i, j) if i == 0 or j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _dropped(heard: str, ours: str) -> bool:
    """Digits of ours with some left out ("Frontier 24" for 2084, "Air Canada 79" for 779): speech-to-text
    dropping them, not another flight."""
    it = iter(ours)
    return len(heard) < len(ours) and all(d in it for d in heard)


def judge(tokens: list[Token], expected: Callsign | None) -> Verdict:
    """``ours`` (heard, near enough), ``absent`` (no callsign said), ``close`` (one slip away from ours), or
    ``other`` (somebody else's)."""
    if expected is None or not expected.is_airline:
        return _judge_registration(tokens, expected)
    ours = tuple(expected.telephony.lower().split())
    number = expected.flight_number.lstrip("0") or "0"
    words = _words(tokens)
    verdict: Verdict = "absent"
    for i in range(len(words)):
        for name in _AIRLINES:
            if tuple(words[i : i + len(name)]) != name:
                continue
            heard = _flight_number(tokens, i + len(name))
            if heard is None or (tokens[i].kind == "letter" and len(heard) < 3):
                break  # no number after it, or "Delta 5" that's a taxiway: not a callsign that says anything
            heard = heard.lstrip("0") or "0"
            ours_name = name == ours or (len(ours) > 1 and name == ours[-1:])
            if heard == number or (ours_name and _dropped(heard, number)):
                return "ours"  # the right number (or ours with digits lost): whatever speech-to-text made of the rest
            if ours_name and _distance(heard, number) <= 1 and verdict == "absent":
                verdict = "close"
            elif not ours_name or _distance(heard, number) > 1:
                verdict = "other" if verdict != "close" else verdict
            break
    if verdict == "absent" and ours[-1] in words:
        # The airline's last word with our number after it ("Canada 779", "Air Canada's a 779") is us.
        at = words.index(ours[-1])
        for j in range(at + 1, min(at + 4, len(tokens))):
            if (heard := _flight_number(tokens, j)) is not None:
                heard = heard.lstrip("0") or "0"
                if heard == number or _dropped(heard, number):
                    return "ours"
                return "close" if _distance(heard, number) <= 1 else "other"
    return verdict


def _judge_registration(tokens: list[Token], expected: Callsign | None) -> Verdict:
    """A registration (N172LT): only another airline's callsign is recognisably somebody else."""
    words = _words(tokens)
    for i in range(len(words)):
        for name in _AIRLINES:
            heard = _flight_number(tokens, i + len(name))
            if tuple(words[i : i + len(name)]) == name and heard is not None and (tokens[i].kind != "letter" or len(heard) >= 3):
                return "other"
    return "absent"


__all__ = ["Verdict", "judge"]
