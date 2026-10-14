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


# Where English is spoken as a first language (or is the language of the radio there), the voice's English: a
# controller in London sounds British, one in Sydney Australian. Longest prefix wins; elsewhere none in particular.
ACCENTS: dict[str, str] = {
    "K": "en-US", "PA": "en-US", "PH": "en-US", "PG": "en-US", "PF": "en-US", "PO": "en-US", "PP": "en-US",
    "TJ": "en-US", "C": "en-CA", "EG": "en-GB", "EI": "en-IE", "Y": "en-AU", "NZ": "en-NZ", "VA": "en-IN",
    "VE": "en-IN", "VI": "en-IN", "VO": "en-IN", "FA": "en-ZA", "WS": "en-SG", "VH": "en-HK", "RP": "en-PH",
    "HK": "en-KE", "HU": "en-KE", "DN": "en-NG", "DG": "en-NG", "HT": "en-TZ", "LM": "en-GB", "LX": "en-GB",
    "TX": "en-US", "MY": "en-US", "MK": "en-US", "TF": "fr-FR",
    # Where English is the second language: the region's own voice speaking it (the voices that can).
    "ED": "de-DE", "ET": "de-DE", "LO": "de-AT", "LS": "de-CH", "LF": "fr-FR", "EL": "fr-FR", "LN": "fr-FR",
    "LI": "it-IT", "LE": "es-ES", "GC": "es-ES", "GE": "es-ES", "LP": "pt-PT", "EH": "nl-NL", "EB": "nl-BE",
    "EK": "da-DK", "ES": "sv-SE", "EN": "nb-NO", "EF": "fi-FI", "BI": "is-IS", "EP": "pl-PL", "LK": "cs-CZ",
    "LZ": "sk-SK", "LH": "hu-HU", "LR": "ro-RO", "LU": "ro-RO", "LB": "bg-BG", "LG": "el-GR", "LC": "el-GR",
    "LT": "tr-TR", "LD": "hr-HR", "LJ": "sl-SI", "LY": "sr-RS", "LQ": "bs-BA", "LW": "mk-MK", "LA": "sq-AL",
    "EY": "lt-LT", "EV": "lv-LV", "EE": "et-EE", "UK": "uk-UA", "U": "ru-RU", "UG": "ka-GE", "UD": "hy-AM",
    "UB": "az-AZ", "UA": "kk-KZ", "UT": "uz-UZ", "LL": "he-IL", "OJ": "ar-JO", "OL": "ar-LB", "OS": "ar-SY",
    "OR": "ar-IQ", "OI": "fa-IR", "OK": "ar-KW", "OB": "ar-BH", "OT": "ar-QA", "OM": "ar-AE", "OO": "ar-OM",
    "OE": "ar-SA", "OY": "ar-YE", "HE": "ar-EG", "HL": "ar-LY", "DT": "ar-TN", "DA": "ar-DZ", "GM": "ar-MA",
    "OP": "ur-PK", "VG": "bn-BD", "VN": "ne-NP", "VC": "si-LK", "VT": "th-TH", "VV": "vi-VN", "VD": "km-KH",
    "WM": "ms-MY", "WB": "ms-MY", "WI": "id-ID", "WA": "id-ID", "WR": "id-ID", "RC": "zh-TW", "Z": "zh-CN",
    "VM": "zh-HK", "RJ": "ja-JP", "RO": "ja-JP", "RK": "ko-KR", "MM": "es-MX", "MG": "es-GT", "MH": "es-HN",
    "MN": "es-NI", "MR": "es-CR", "MP": "es-PA", "MS": "es-SV", "MU": "es-CU", "MD": "es-DO", "SK": "es-CO",
    "SV": "es-VE", "SE": "es-EC", "SP": "es-PE", "SL": "es-BO", "SC": "es-CL", "SA": "es-AR", "SU": "es-UY",
    "SG": "es-PY", "SB": "pt-BR", "SN": "pt-BR", "SD": "pt-BR", "SW": "pt-BR", "SS": "pt-BR", "GO": "fr-FR",
    "DI": "fr-FR", "DF": "fr-FR", "DB": "fr-FR", "DR": "fr-FR", "GA": "fr-FR", "FK": "fr-FR", "FO": "fr-FR",
    "FM": "fr-FR", "FI": "en-GB", "HA": "am-ET",
}


def accent(code: str | None) -> str:
    """The voice ATC speaks English with at an airport or FIR: its English ("en-GB" for EGLL), or where English
    isn't the first language the region's own language ("de-DE" for EDDF: a German voice speaking English); "" not
    known (a voice of no region in particular)."""
    code = (code or "").upper()
    for size in (2, 1):
        if code[:size] in ACCENTS:
            return ACCENTS[code[:size]]
    return ""


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
