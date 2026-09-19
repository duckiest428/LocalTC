"""Value types that fill phraseology slots and that the readback parser compares against."""

import re
from dataclasses import dataclass, replace

# MSFS 2024 hands out untranslated localization tokens for ATC TYPE and ATC MODEL.
SIM_TOKEN = re.compile(r"ATCCOM\.(?:ATC_NAME|AC_MODEL)\s+(.+?)\.\d+\.(?:text|tts)", re.IGNORECASE)
# Three letters and a number (EXP69, ASA123): an airline-style callsign, said in full every time.
AIRLINE_STYLE = re.compile(r"[A-Z]{3}\d{1,4}[A-Z]{0,2}")


def clean_sim_name(value: str) -> str:
    """ "ATCCOM.ATC_NAME AIRBUS.0.text" -> "Airbus", "ATCCOM.AC_MODEL A330.0.text" -> "A330"."""
    value = value.strip()
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
        """Abbreviating only makes sense for a registration: "DP69" must not become "P69", and "EXP69"
        (an airline-style callsign) must not become "Cessna P69"."""
        ident = self.ident.replace("-", "")
        registration = len(ident) > 4 and (ident.startswith("N") and ident[1:2].isdigit() or not AIRLINE_STYLE.fullmatch(ident))
        return replace(self, abbreviated=registration)

    @property
    def suffix(self) -> str:
        """Last three characters, used for abbreviated registrations (N172LT -> 2LT)."""
        return self.ident.replace("-", "")[-3:]

    @classmethod
    def from_sim(cls, atc_id: str, airline: str = "", flight_number: str = "", atc_type: str = "") -> "Callsign":
        ident = atc_id.strip().upper()
        return cls(
            ident=ident,
            telephony=airline.strip(),
            flight_number=flight_number.strip(),
            type_name=clean_sim_name(atc_type),
        )


@dataclass(frozen=True)
class Approach:
    kind: str  # "ILS", "RNAV", "VISUAL"
    runway: str  # "14R"

    @property
    def display(self) -> str:
        return f"{self.kind} RWY {self.runway}"


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
