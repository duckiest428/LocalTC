"""The words every voice is given, whichever synthesizer speaks them: what's left of digits and codes in the text
turned into spoken aviation English, in one place.

ATC's own words arrive already spoken (``AtcTransmission.spoken``: atc_core's phraseology says "runway three four
left", "flight level three five zero"). What still has digits or codes in it (the copilot's lines, a language model's
wording, a display text) is finished here, the same way for Piper, Kokoro and Azure, so no synthesizer reads a number
its own way: "119.2" is never "one hundred nineteen point two", "FL350" never "F L three hundred fifty", "2LT" never
"two L T". Only words leave this module (no digits), so a voice can't change what a number means.
"""

import re

DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner")
PHONETIC = {
    "A": "alpha", "B": "bravo", "C": "charlie", "D": "delta", "E": "echo", "F": "foxtrot", "G": "golf",
    "H": "hotel", "I": "india", "J": "juliett", "K": "kilo", "L": "lima", "M": "mike", "N": "november",
    "O": "oscar", "P": "papa", "Q": "quebec", "R": "romeo", "S": "sierra", "T": "tango", "U": "uniform",
    "V": "victor", "W": "whiskey", "X": "x-ray", "Y": "yankee", "Z": "zulu",
}
SIDES = {"L": "left", "R": "right", "C": "center"}

THOUSANDS = re.compile(r"(\d),(\d{3})\b")
FLIGHT_LEVEL = re.compile(r"\bFL\s?(\d{2,3})\b")
RUNWAY = re.compile(r"\b(\d{1,2})([LRC])\b")
# A code of digits and capitals together ("2LT", "N172LT", "A3"): each character on its own.
CODE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,8}\b")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# A single capital on its own, as a taxiway or an ATIS letter is written ("via A, B", "information C."): not "I", and
# not "A" starting a sentence's next word ("A Cessna").
LETTER = re.compile(r"(?<![\w-])([B-HJ-Z]|A(?=\s*(?:[,.;]|$|\s+(?:and|then)\b|\s+[A-Z](?:\W|$))))(?![\w-])")


def spell(code: str) -> str:
    """Each character on its own: digits (niner), letters in the phonetic alphabet, "." as "point"."""
    out = []
    for ch in code.upper():
        if ch.isdigit():
            out.append(DIGITS[int(ch)])
        elif ch == ".":
            out.append("point")
        elif ch in PHONETIC:
            out.append(PHONETIC[ch])
    return " ".join(out)


def speakable(text: str) -> str:
    """``text`` with nothing left for a synthesizer to read its own way: numbers digit by digit ("one five zero
    zero", "two niner point niner two"), runways ("three four left"), flight levels, codes and lone letters
    phonetically. Words are left as they are."""
    text = THOUSANDS.sub(r"\1\2", text)
    text = FLIGHT_LEVEL.sub(lambda m: "flight level " + spell(m.group(1)), text)
    text = RUNWAY.sub(lambda m: spell(m.group(1)) + " " + SIDES[m.group(2)], text)
    text = CODE.sub(lambda m: spell(m.group()), text)
    text = NUMBER.sub(lambda m: spell(m.group()), text)
    return LETTER.sub(lambda m: PHONETIC[m.group(1)], text)


def digits_in(text: str) -> list[str]:
    """The digits a text says, in order, whether written ("29.92") or spoken ("two niner point niner two"): what
    the tests hold every voice's input to (the meaning of the numbers kept)."""
    words = {w: str(i) for i, w in enumerate(DIGITS)} | {"nine": "9"}
    out = []
    for token in re.findall(r"[A-Za-z]+|\d", THOUSANDS.sub(r"\1\2", text)):
        if token.isdigit():
            out.append(token)
        elif token.lower() in words:
            out.append(words[token.lower()])
    return out
