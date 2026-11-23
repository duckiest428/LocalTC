"""ATC in the pilot's own language: how each of LocalTC's languages says what's on the radio, and reads it back.

English stays the radio's language unless the pilot calls in another (``[atc] language``): then the controller answers
in that one, as a French controller answers a French crew in French. Each language here has

- its radiotelephony words for the numbers, the runways, the levels, the frequencies and the rest of a clearance
  (``Tongue``: the digits one by one, as on the radio; numbers said whole, the minutes and the miles, are left in
  figures for that language's voice to read), what speech says for the spoken words, and
- its lexicon (``lexicon``): its aviation words put into English, so what a pilot says in it ("montons niveau un
  deux zéro") is read by the same grammar as English ("climb flight level 120") and every readback is checked the
  same way (``readback/translate.py``).

The templates' words in each language are ``templates/lang/<code>.toml``: the same ids, reworded.

``speaking(code)`` sets the language everything said in the block is worded in (``current()``: None for English).
"""

import importlib
import re
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

CODES = ("de", "fr", "es", "it", "pt", "nl", "pl", "sv", "ru", "zh")  # LocalTC's languages besides English
# The voice of a controller speaking it where the region's own isn't (French at JFK: a French voice).
LOCALES = {"de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT", "pt": "pt-PT", "nl": "nl-NL", "pl": "pl-PL",
           "sv": "sv-SE", "ru": "ru-RU", "zh": "zh-CN"}

ICAO_LETTERS = {
    "A": "alpha", "B": "bravo", "C": "charlie", "D": "delta", "E": "echo", "F": "foxtrot", "G": "golf",
    "H": "hotel", "I": "india", "J": "juliett", "K": "kilo", "L": "lima", "M": "mike", "N": "november",
    "O": "oscar", "P": "papa", "Q": "quebec", "R": "romeo", "S": "sierra", "T": "tango", "U": "uniform",
    "V": "victor", "W": "whiskey", "X": "x-ray", "Y": "yankee", "Z": "zulu",
}

# How the approaches are named (display): "ILS RWY 27L" is "ILS piste 27L" in French.
APPROACH_KINDS = {"ILS": "I L S", "RNAV": "R-NAV", "RNP": "R-NAV", "GPS": "G P S", "LOC": "LOC", "LOC/DME": "LOC D M E",
                  "LOC BC": "LOC B C", "LDA": "L D A", "SDF": "S D F", "VOR": "V O R", "VOR/DME": "V O R D M E",
                  "NDB": "N D B", "NDB/DME": "N D B D M E"}


@dataclass(frozen=True)
class Tongue:
    code: str
    digits: tuple[str, ...]  # 0-9 as said one by one on the radio
    point: str  # a frequency's decimal point
    sides: dict[str, str]  # L, R, C: a runway's side
    flight_level: str
    feet: str
    knots: str
    degrees: str
    calm: str  # the wind under three knots
    runway: str  # the word for a runway (in an approach's name)
    visual: str  # "VISUAL" in an approach's name ("ILS piste 27L", "à vue piste 27L")
    # English words the engine fills into a template (left, right, northbound, "the visual"...), in this language.
    words: dict[str, str] = field(default_factory=dict)
    # A station's last word ("Tower", "Ground") in this language ("Tour", "Sol"): "Paris Control" is "Paris Contrôle".
    stations: dict[str, str] = field(default_factory=dict)
    places: dict[str, str] = field(default_factory=dict)  # gate, stand, parking, ramp
    letters: dict[str, str] = field(default_factory=lambda: dict(ICAO_LETTERS))  # the spelling alphabet, as said
    wind: str = "{direction} {degrees}, {speed} {knots}"
    level_spoken: str = "{flight_level} {digits}"  # "niveau un deux zéro"
    feet_spoken: str = "{n} {feet}"  # "3000 pieds" (the voice reads the figures)
    sep: str = " "  # between words said one by one (Chinese: none)
    # Reading: the language's words put into English for the grammar (``translate.py``).
    lexicon: tuple[tuple[str, str], ...] = ()
    numbers: dict[str, int] = field(default_factory=dict)  # number words, and the pieces longer ones are made of
    connectors: tuple[str, ...] = ()  # "und", "et", "y": joining a number's parts ("vingt et un")
    articles: tuple[str, ...] = ()  # number words that are also "a" ("un", "ein"): numbers only beside another
    takes_numbers: tuple[str, ...] = ()  # words a number follows ("piste", "niveau"): "piste un" is runway 1
    pilot: dict[str, str] = field(default_factory=dict)  # the copilot's own calls on the radio (copilot.py)
    # A script of its own (Russian): what's left in it after the lexicon, back in Latin letters (a callsign speech-to-
    # text wrote in Cyrillic). (Its voice reads Latin names in its own letters: tts, ``languages.cyrillic``.)
    romanize: Callable[[str], str] | None = None
    approach_kinds: dict[str, str] = field(default_factory=lambda: dict(APPROACH_KINDS))  # "ILS" as it's said

    # --- speaking -----------------------------------------------------------------------------------------------

    def spell(self, text: str) -> str:
        """Each digit, and each letter by the spelling alphabet; "." the decimal point."""
        words = []
        for ch in str(text).upper():
            if ch.isdigit():
                words.append(self.digits[int(ch)])
            elif ch == ".":
                words.append(self.point)
            elif ch in self.letters:
                words.append(self.letters[ch])
        return self.sep.join(words)

    def word(self, english: str) -> str:
        """An English word the engine filled in ("left", "northbound"), in this language; as it is when unknown."""
        key = english.strip().lower()
        if key in self.words:
            return self.words[key]
        if key in ("left", "right", "center", "centre"):
            return self.sides[key[0].upper()]
        return english

    def runway_said(self, ident: str) -> str:
        num = "".join(c for c in ident if c.isdigit()).lstrip("0") or "0"
        side = ident[len(ident.rstrip("LRC")):]
        return self.sep.join([self.spell(num), *(self.sides[s] for s in side)])

    def runway_shown(self, ident: str) -> str:
        return ident

    def frequency_said(self, mhz: float) -> str:
        text = f"{mhz:.3f}".rstrip("0")
        return self.spell(text + "0" if text.endswith(".") else text)

    def altitude_said(self, feet: int, transition_ft: int) -> str:
        feet = int(round(feet / 100.0) * 100)
        if feet >= transition_ft:
            return self.level_spoken.format(flight_level=self.flight_level, digits=self.spell(str(feet // 100)))
        return self.feet_spoken.format(n=feet, feet=self.feet)

    def altitude_shown(self, feet: int, transition_ft: int) -> str:
        feet = int(round(feet / 100.0) * 100)
        if feet >= transition_ft:
            return f"FL{feet // 100:03d}"
        return f"{feet} {self.feet}"

    def wind_said(self, direction: int, speed: int) -> str:
        if speed < 3:
            return self.calm
        return self.wind.format(direction=self.spell(f"{direction % 360 or 360:03d}"), speed=self.spell(str(speed)),
                                degrees=self.degrees, knots=self.knots)

    def wind_shown(self, direction: int, speed: int) -> str:
        if speed < 3:
            return self.calm
        return self.wind.format(direction=f"{direction % 360 or 360:03d}", speed=speed, degrees=self.degrees,
                                knots=self.knots).replace(" ,", ",")

    def taxiway(self, name: str) -> str:
        name = name.upper().replace("TWY", "").strip()
        out = []
        for part in re.findall(r"[A-Z]{3,}|[A-Z]|\d", name):
            out.append(part.lower() if len(part) >= 3 else self.letters.get(part, part) if part.isalpha()
                       else self.digits[int(part)])
        return self.sep.join(out)

    def procedure(self, name: str) -> str:
        said, word = [], ""
        for char in name.upper():
            if char.isdigit():
                if word:
                    said.append(word.lower())
                    word = ""
                said.append(self.digits[int(char)])
            elif char.isalnum():
                if said and not word:
                    said.append(self.letters.get(char, char))
                else:
                    word += char
        if word:
            said.append(word.lower())
        return " ".join(said)

    def station(self, name: str) -> str:
        """ "Paris Control" -> "Paris Contrôle", "Heathrow Tower" -> "Heathrow Tour" (the station's own words kept)."""
        for english, own in sorted(self.stations.items(), key=lambda kv: -len(kv[0])):
            if name.lower().endswith(" " + english.lower()) or name.lower() == english.lower():
                head = name[: len(name) - len(english)].rstrip()
                return f"{head}{self.sep if head else ''}{own}".strip() if self.sep else f"{head}{own}"
        return name

    def place(self, display: str) -> tuple[str, str]:
        """ "Gate B25" -> ("porte B25", "porte bravo deux cinq")."""
        word, _, label = display.partition(" ")
        own = self.places.get(word.lower(), word)
        return f"{own} {label}".strip(), f"{own} {self.taxiway(label)}".strip()

    def approach_shown(self, kind: str, runway: str, suffix: str, charted: str) -> str:
        if charted:
            return f"{charted} {self.visual} {self.runway} {runway}"
        name = self.visual if kind == "VISUAL" else kind
        letter = f" {suffix}" if suffix else ""
        return f"{name}{letter} {self.runway} {runway}" if runway else f"{name}{letter or ' A'}"

    def approach_said(self, kind: str, runway: str, suffix: str, charted: str) -> str:
        if charted:
            return f"{charted.lower()} {self.visual} {self.runway} {self.runway_said(runway)}"
        name = self.visual if kind == "VISUAL" else self.approach_kinds.get(kind, kind)
        letter = f" {self.letters[suffix.upper()]}" if suffix and suffix.upper() in self.letters else ""
        if not runway:
            return f"{name}{letter or ' ' + self.letters['A']}"
        return f"{name}{letter} {self.runway} {self.runway_said(runway)}"

    def callsign_said(self, telephony: str, number: str) -> str:
        """An airline's callsign: its telephony as it is ("Air France"), the flight number digit by digit."""
        return f"{telephony} {self.spell(number)}".strip()


CURRENT: ContextVar[str] = ContextVar("localtc_radio_language", default="en")
_LOADED: dict[str, Tongue] = {}


def tongue(code: str) -> Tongue | None:
    """The language ``code`` ("fr", "fr-CA"); None for English or a language LocalTC doesn't speak on the radio."""
    code = (code or "").split("-")[0].lower()
    if code not in CODES:
        return None
    if code not in _LOADED:
        _LOADED[code] = importlib.import_module(f"localtc.atc_core.phraseology.tongues.{code}").TONGUE
    return _LOADED[code]


def current() -> Tongue | None:
    """The language what's said now is worded in (None: English)."""
    return tongue(CURRENT.get())


@contextmanager
def speaking(code: str) -> Iterator[str]:
    token = CURRENT.set(code if tongue(code) is not None else "en")
    try:
        yield CURRENT.get()
    finally:
        CURRENT.reset(token)


def plain(text: str) -> str:
    """Lower case, without accents ("Montée" -> "montee"); letters that aren't an accent added are kept (ł, ß)."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("œ", "oe").replace("æ", "ae").replace("ł", "l").replace("ß", "ss"))
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if not unicodedata.combining(c)))

