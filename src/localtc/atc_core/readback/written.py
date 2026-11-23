"""The pilot's words as the radio log shows them once ATC has taken the frequency in them: "Tower on 118, Dec. 7, 7, 7,
5" is "Tower on 118.775", "Departure is on 9 decimal 75" is "Departure is on 119.75".

Whisper writes a frequency said digit by digit every way but the right one, and the log showed it that way, though ATC
had heard the right one. Only the log's line changes: the recording keeps what Whisper heard, and nothing here decides
whether the readback was right (a frequency ATC turned down is never rewritten).
"""

import re

_NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "niner": "9", "tree": "3", "fife": "5"}
_POINT = r"(?:decimal|dec\.?|point|\.)"
_PIECE = rf"(?:\d+(?:[.,]\d+)*|{'|'.join(_NUMBER_WORDS)}|{_POINT})"
# A run of the frequency's pieces: "118, Dec. 7, 7, 7, 5", "9 decimal 75", "1, 2, 1340, 75", "13205"
_RUN = re.compile(rf"(?<![\w.]){_PIECE}(?:[ ,]*{_PIECE})*(?![\w])", re.IGNORECASE)
# What may stand before it, kept or replaced: "on", the station ("Tower 118.7"), "departure is on"
_ANCHOR = re.compile(r"\b(?:on|frequency|tower|ground|departure|approach|cent(?:er|re)|control|radio|clearance|delivery|"
                     r"director|contact)\b[ ,]*$", re.IGNORECASE)
_FILLER = re.compile(r"((?:\b[a-z]+\b[ ,]*){1,2})$", re.IGNORECASE)  # up to two words Whisper made of "one one"


def _digits(run: str) -> tuple[str, bool]:
    """The run's digits, and whether it says where the decimal point is."""
    out, point = [], False
    for piece in re.findall(rf"\d+|[a-z]+|\.", run.lower()):
        if piece.isdigit():
            out.append(piece)
        elif piece in _NUMBER_WORDS:
            out.append(_NUMBER_WORDS[piece])
        elif piece in ("decimal", "dec", "point", "."):
            point = True
    return "".join(out), point


def written(mhz: float) -> str:
    """118.775, 119.75, 121.9, 132.05: as ATC writes it."""
    text = f"{mhz:.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _is_it(digits: str, mhz: float) -> bool:
    want = written(mhz).replace(".", "")
    whole = written(mhz).split(".")[0]
    if not digits or len(digits) < 3:
        return False
    if digits == want or digits.rstrip("0") == want.rstrip("0"):
        return True
    if digits == whole + "0" + written(mhz).split(".")[1]:  # "13205": the point heard as a zero
        return True
    # A digit heard twice ("118, Dec. 7, 7, 7, 5" for 118.775)
    if any(digits[:i] + digits[i + 1:] == want and digits[i] == digits[i - 1] for i in range(1, len(digits))):
        return True
    # Something before it ("1, 2, 1340, 75" for 134.075), or the start lost ("9 decimal 75" for 119.75)
    return (digits.endswith(want) and len(want) >= 4) or (want.endswith(digits) and len(digits) >= len(want) - 2)


def with_frequency(text: str, mhz: float) -> str:
    """``text`` with the run of digits that's ``mhz`` written as ATC writes it, or ``text`` as it was if no run is."""
    best = None
    for m in _RUN.finditer(text):
        digits, _ = _digits(m.group(0))
        if _is_it(digits, mhz):
            best = m
    if best is None:
        return text
    start = best.start()
    before = text[:start]
    if not _ANCHOR.search(before) and (filler := _FILLER.search(before)) and _ANCHOR.search(before[:filler.start()]):
        start = filler.start()  # "on normal normal 9 decimal 75": the words "one one" became
    run = best.group(0)
    end = best.start() + len(run.rstrip(" ,."))
    return text[:start] + written(mhz) + text[end:]


_EXPECT = re.compile(r"\bexpect(?:ing)?\b(?P<said>.{1,60}?)\b(?:after\s+)?departure\b", re.IGNORECASE)


def with_expected(text: str, level: str, minutes: int | None) -> str:
    """``text`` with its "expect ... departure" written as ATC gave it ("Expect 5L, 3300, 23M, departure" is "Expect
    FL330 23 minutes after departure"), once the cruise in it was taken; ``text`` as it was without one."""
    m = _EXPECT.search(text)
    if m is None or not re.search(r"\d", m.group("said")):
        return text
    word = m.group(0).split()[0]
    after = f" {minutes} minutes after departure" if minutes else " after departure"
    return text[:m.start()] + f"{word} {level}{after}" + text[m.end():]
