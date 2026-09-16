"""Value types that fill phraseology slots and that the readback parser compares against."""

from dataclasses import dataclass, replace


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
        return replace(self, abbreviated=True)

    @property
    def suffix(self) -> str:
        """Last three characters, used for abbreviated registrations (N172LT -> 2LT)."""
        return self.ident.replace("-", "")[-3:]

    @classmethod
    def from_sim(cls, atc_id: str, airline: str = "", flight_number: str = "", atc_type: str = "") -> "Callsign":
        ident = atc_id.strip().upper()
        return cls(ident=ident, telephony=airline.strip(), flight_number=flight_number.strip(), type_name=atc_type.strip())


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
