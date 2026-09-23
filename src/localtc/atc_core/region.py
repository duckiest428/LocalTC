"""Which phraseology a controller uses, from where it is: FAA in the US, FAA-style in Canada, ICAO elsewhere.

A ``Region`` is decided from an ICAO location indicator: an airport ("LIRF") or a FIR ("LIRR", "EGGX"), both
of which start with the ICAO region and country letters. It also carries the local transition altitude,
which decides where "flight level" starts and where the altimeter (or QNH) is no longer given.

The transition altitudes are typical values for each country; individual airports publish their own, and
some vary with the day's pressure. They're close enough for how ATC words things.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

Style = Literal["faa", "icao"]


@dataclass(frozen=True)
class Region:
    style: Style
    transition_ft: int

    @property
    def icao(self) -> bool:
        return self.style == "icao"


FAA = Region("faa", 18000)
CANADA = Region("faa", 18000)  # NAV CANADA's wording is close to the FAA's: altimeter in inches, "hold short"
ICAO_DEFAULT = Region("icao", 6000)

# Location-indicator prefixes -> transition altitude (ICAO regions). Longest prefix wins.
TRANSITION_FT: dict[str, int] = {
    "EG": 6000, "EI": 5000, "ED": 5000, "ET": 5000, "LF": 5000, "EH": 3000, "EB": 4500, "EL": 4000,
    "LS": 7000, "LO": 10000, "LI": 6000, "LE": 6000, "LP": 4000, "EK": 5000, "EN": 7000, "ES": 5000,
    "EF": 5000, "EP": 6500, "LK": 5000, "LZ": 10000, "LH": 10000, "LJ": 10000, "LD": 10000, "LG": 10000,
    "LT": 10000, "LL": 15000, "OM": 13000, "OE": 13000, "OT": 13000, "OB": 13000, "OK": 13000,
    "Y": 10000, "NZ": 13000, "RJ": 14000, "RK": 14000, "RC": 11000, "VH": 9000, "WS": 11000, "VT": 11000,
    "Z": 9800, "V": 4000, "FA": 18000, "SB": 7000, "SC": 10000, "SA": 3000, "SK": 18000, "MM": 18500,
    "MP": 18000, "TJ": 18000, "BI": 7000, "BG": 6000,
}


def region_for(code: str | None) -> Region:
    """The region of an airport or FIR identifier. Unknown or empty: the FAA (LocalTC's home default)."""
    code = (code or "").upper()
    if not code:
        return FAA
    if code[0] == "K" or code[:2] in ("PA", "PH", "PG", "PF", "PO", "PP", "TJ"):  # the US, Alaska, Hawaii, Guam, PR
        return FAA
    if code[0] == "C":
        return CANADA
    for size in (2, 1):
        if code[:size] in TRANSITION_FT:
            return Region("icao", TRANSITION_FT[code[:size]])
    return ICAO_DEFAULT


def forced(style: str, transition_ft: int = 0) -> Region | None:
    """A region from the setting ``[atc] phraseology``: None for "auto"."""
    if style == "faa":
        return Region("faa", transition_ft or 18000)
    if style == "icao":
        return Region("icao", transition_ft or ICAO_DEFAULT.transition_ft)
    return None


# The region the phraseology is being spoken in, for the speech helpers (phraseology/speech.py). The engine
# sets it around each step; outside one (tests, tools) it's the FAA's.
CURRENT: ContextVar[Region] = ContextVar("localtc_region", default=FAA)


@contextmanager
def speaking(region: Region):
    token = CURRENT.set(region)
    try:
        yield region
    finally:
        CURRENT.reset(token)
