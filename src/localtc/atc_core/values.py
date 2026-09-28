"""Value types that fill phraseology slots and that the readback parser compares against."""

import re
from dataclasses import dataclass, replace

from localtc.atc_core import airlines

# MSFS 2024 hands out untranslated localization tokens for ATC TYPE and ATC MODEL.
# "ATCCOM.AC_MODEL A330.0.text", and "ATCCOM.AC_MODEL_A20N.0.text" (the A320neo V2 writes it with an underscore)
SIM_TOKEN = re.compile(r"ATCCOM\.(?:ATC_NAME|AC_MODEL)[\s_]+(.+?)\.\d+\.(?:text|tts)", re.IGNORECASE)
# Three letters and a number (EXP69, ASA123): an airline-style callsign, said in full every time.
AIRLINE_STYLE = re.compile(r"[A-Z]{3}\d{1,4}[A-Z]{0,2}")


def clean_sim_name(value: str) -> str:
    """ "ATCCOM.ATC_NAME AIRBUS.0.text" -> "Airbus", "ATCCOM.AC_MODEL A330.0.text" -> "A330"."""
    value = value.strip()
    value = value.removeprefix("$$:")  # "$$:E170": how some add-on liveries carry the type
    if (match := SIM_TOKEN.search(value)) is not None:
        word = match.group(1).strip()
        return word.title() if word.isalpha() else word.upper()
    return "" if ".text" in value or ".tts" in value or value.upper().startswith("ATCCOM") else value


@dataclass(frozen=True)
class Callsign:
    """``ident`` is the ATC ID (``N172LT``). Airlines also carry ``telephony`` ("Alaska") + ``flight_number``."""

    ident: str
    telephony: str = ""
    flight_number: str = ""
    type_name: str = ""  # e.g. "Skyhawk", used for abbreviated GA callsigns
    abbreviated: bool = False

    @property
    def is_airline(self) -> bool:
        return bool(self.telephony and self.flight_number)

    @property
    def short(self) -> "Callsign":
        """Abbreviating only makes sense for a registration: an N-number (N172LT), an all-letter one (CFABC,
        GABCD) or a hyphenated one (C-FABC). "DP69" must not become "P69", "EXP69" (airline-style) must not
        become "Cessna P69", and "NZXT42" (letters then digits: a flight callsign) must not become "T42"."""
        ident = self.ident.replace("-", "")
        n_number = ident.startswith("N") and ident[1:2].isdigit()
        lettered = ident.isalpha() or ("-" in self.ident and not AIRLINE_STYLE.fullmatch(ident))
        return replace(self, abbreviated=len(ident) > 4 and (n_number or lettered))

    @property
    def suffix(self) -> str:
        """Last three characters, used for abbreviated registrations (N172LT -> 2LT)."""
        return self.ident.replace("-", "")[-3:]

    @classmethod
    def named(cls, ident: str, type_name: str = "") -> "Callsign":
        """A callsign from a flight plan or the command line: "ACA216" is Air Canada 216, "N172LT" is not."""
        ident = ident.strip().upper()
        parts = airlines.split(ident)
        if parts is None:
            return cls(ident=ident, type_name=type_name)
        _, telephony, number = parts
        return cls(ident=ident, telephony=telephony, flight_number=number, type_name=type_name)

    @classmethod
    def from_sim(cls, atc_id: str, airline: str = "", flight_number: str = "", atc_type: str = "") -> "Callsign":
        ident, airline, flight_number = atc_id.strip().upper(), airline.strip(), flight_number.strip()
        if not ident and airline and flight_number:
            # Some aircraft carry the airline and flight number but no ATC ID. Its code makes the ident.
            code = airlines.code_for(airline)
            ident = f"{code}{flight_number}" if code else flight_number
        telephony = airline
        if not telephony and (parts := airlines.split(ident)) is not None:
            _, telephony, flight_number = parts  # an ident that is itself a flight number
        return cls(
            ident=ident,
            telephony=telephony,
            flight_number=flight_number,
            type_name=clean_sim_name(atc_type),
        )


@dataclass(frozen=True)
class Approach:
    """An approach as ATC names it: "ILS Z RWY 34R", "LOC BC RWY 26", "RNAV (RNP) Y RWY 19", "VOR-A".

    ``kind`` is one of APPROACH_KINDS. ``runway`` is the runway the procedure is built for; "" for one with
    circling minima only (named with a letter: VOR-A). ``circle_to`` is the runway to land on when that's
    another one (circle-to-land), with ``circle_side`` ("west") and ``circle_pattern`` ("left") when ATC
    restricts where to circle. ``minima`` is the RNAV minima line flown (LPV, LNAV/VNAV, LNAV, LP), for show:
    ATC never says it."""

    kind: str
    runway: str
    suffix: str = ""
    circle_to: str = ""
    circle_side: str = ""
    circle_pattern: str = ""
    minima: str = ""

    @property
    def name(self) -> str:
        """The chart's name without the runway: "ILS Z", "RNAV (RNP) Y", "VOR-A"."""
        kind = {"RNP": "RNAV (RNP)"}.get(self.kind, self.kind)
        if not self.runway:
            return f"{kind}-{self.suffix or 'A'}"
        return f"{kind} {self.suffix}" if self.suffix else kind

    @property
    def display(self) -> str:
        return self.name if not self.runway else f"{self.name} RWY {self.runway}"

    @property
    def landing_runway(self) -> str:
        """Where it lands: the runway circled to, or the one the approach is to."""
        return self.circle_to or self.runway

    @property
    def circling(self) -> bool:
        return bool(self.circle_to) or not self.runway

    @classmethod
    def parse(cls, display: str) -> "Approach | None":
        """The approach from its ``display`` ("ILS Z RWY 34R", "VOR-A"); None if it isn't one."""
        text = (display or "").strip()
        if not text:
            return None
        head, _, runway = text.partition(" RWY ")
        head = head.replace("RNAV (RNP)", "RNP")
        if not runway:
            kind, _, letter = head.rpartition("-")
            return cls(kind, "", letter) if kind in APPROACH_KINDS and len(letter) == 1 else None
        words = head.split()
        suffix = words[-1] if len(words) > 1 and len(words[-1]) == 1 and words[-1].isalpha() else ""
        kind = " ".join(words[:-1] if suffix else words)
        return cls(kind, runway.strip(), suffix) if kind in APPROACH_KINDS else None


# Every approach LocalTC knows, precision first. "RNP" is an RNAV (RNP) approach (RNP AR, authorization required);
# "RNAV" an RNAV (GPS) approach, whose minima line (LPV ... LNAV) depends on the aircraft.
APPROACH_KINDS = ("ILS", "RNP", "RNAV", "GPS", "LOC", "LOC/DME", "LDA", "SDF", "LOC BC", "VOR/DME", "VOR", "NDB/DME",
                  "NDB", "VISUAL")


@dataclass(frozen=True)
class Phrase:
    """Pre-rendered text with separate display and spoken forms (e.g. a correction)."""

    display: str
    spoken: str

    def __add__(self, other: "Phrase") -> "Phrase":
        return Phrase(f"{self.display}, {other.display}", f"{self.spoken}, {other.spoken}")


@dataclass(frozen=True)
class Wind:
    direction_mag: int
    speed_kt: int
