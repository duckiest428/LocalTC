"""ATC facilities (controllers) for a flight: who works which frequency, and what they're called on the radio."""

import re
import zlib
from dataclasses import dataclass

from localtc.atc_core.phraseology import speech
from localtc.sim_api import Airport, Frequency

ABBREVIATION_STEM = 4  # letters that must agree for a frequency name to be the airport name shortened
CONTROLLERS = ("clearance", "ground", "tower", "departure", "center", "approach")
SUFFIXES = {"clearance": "Clearance", "ground": "Ground", "tower": "Tower", "departure": "Departure",
            "approach": "Approach", "center": "Center"}
# Words in sim frequency names that describe the airport, not the radio callsign.
NOISE_WORDS = {
    "INTL", "INTERNATIONAL", "AIRPORT", "ARPT", "FLD", "FIELD", "CO", "COUNTY", "MUNI", "MUNICIPAL", "RGNL",
    "REGIONAL", "APP", "APPR", "APPROACH", "DEP", "DEPARTURE", "TWR", "TOWER", "GND", "GROUND", "CLNC", "CLR",
    "CLEARANCE", "DELIVERY", "DEL", "CTR", "CENTER", "CENTRE", "ATIS", "MEM", "MEMORIAL", "EXEC", "EXECUTIVE",
    "MIL", "AB", "AFB", "BASE",
}


@dataclass(frozen=True)
class Facility:
    controller: str  # one of CONTROLLERS
    station: str  # radio callsign, e.g. "Seattle Approach"
    mhz: float  # primary frequency, used when ATC tells a pilot to contact this facility
    airport: str | None = None  # ICAO, None for center
    alternates: tuple[float, ...] = ()  # other frequencies the same controller works

    def matches(self, mhz: float) -> bool:
        return any(channel_khz(f) == channel_khz(mhz) for f in (self.mhz, *self.alternates))


def channel_khz(mhz: float) -> int:
    """The radio channel a frequency names, in kHz.

    25 kHz channels are written with two decimals: CYUL departure is named 120.42 in the sim's
    facility data but the radio tunes 120.425. Only .x20/.x70 are such names - 8.33 kHz channels
    skip those two slots - so expanding them is unambiguous.
    """
    khz = round(mhz * 1000)
    return khz + 5 if khz % 100 in (20, 70) else khz


def station_name(freq: Frequency, airport: Airport, controller: str) -> str:
    """ "SEATTLE-TACOMA INTL" -> "Seattle Approach", "PAINE" -> "Paine Tower", "" -> "<airport> Tower"."""
    suffix = SUFFIXES[controller]
    first = re.split(r"[-/(]", freq.name.upper())[0]
    words = [w for w in first.split() if w not in NOISE_WORDS and w != airport.icao.upper()]
    if not words:
        words = speech.airport_name(airport.name, airport.icao).split()
        words = [w for w in words if w.upper() not in NOISE_WORDS] or words
    return " ".join(w.capitalize() for w in _unabbreviate(words, airport)) + f" {suffix}"


def _unabbreviate(words: list[str], airport: Airport) -> list[str]:
    """Put back a name the sim's frequency data cut short.

    Fiumicino's tower is named FIUME in the sim while its approach is ROME, so a flight would be
    handed from "Rome Approach" to "Fiume Tower". A shortened name starts like the airport's own
    word but isn't always a prefix of it -- FIUME has an E where FIUMICINO has an I -- so they are
    matched on their first few letters. A name the sim did not shorten is the same length and stays.
    """
    full = re.split(r"[-/(]", airport.name.upper())[0].split()
    out = []
    for word in words:
        stem = word[:ABBREVIATION_STEM]
        match = next((f for f in full if len(word) >= ABBREVIATION_STEM and len(f) > len(word)
                      and f.startswith(stem)), None)
        out.append(match or word)
    return out


def _facility(airport: Airport, controller: str, kinds: tuple[str, ...]) -> Facility | None:
    for kind in kinds:  # first kind that exists wins; all its frequencies belong to the facility
        freqs = [f for f in airport.frequencies if f.kind == kind]
        if freqs:
            return Facility(
                controller=controller,
                station=station_name(freqs[0], airport, controller),
                mhz=freqs[0].mhz,
                airport=airport.icao,
                alternates=tuple(f.mhz for f in freqs[1:]),
            )
    return None


def airport_facilities(airport: Airport, *, role: str) -> list[Facility]:
    """Facilities at an origin ("departure" role) or destination ("arrival" role) airport."""
    specs = [
        ("clearance", ("clearance", "clearance_pretaxi", "remote_clearance")),
        ("ground", ("ground",)),
        ("tower", ("tower",)),
        ("departure", ("departure", "approach")) if role == "departure" else ("approach", ("approach", "departure")),
    ]
    facilities = [f for controller, kinds in specs if (f := _facility(airport, controller, kinds)) is not None]
    # No clearance delivery: ground issues clearances.
    if not any(f.controller == "clearance" for f in facilities):
        ground = next((f for f in facilities if f.controller == "ground"), None)
        if ground is not None:
            facilities.append(Facility("clearance", ground.station, ground.mhz, airport.icao, ground.alternates))
    return facilities


def center_facility(airports: list[Airport], name: str, mhz: float) -> Facility:
    """The centre the flight starts its cruise with.

    ``airports`` is the origin and destination only. Every airport the sim has streamed along the way
    would do just as well at having a centre frequency, and picking one of those hands a flight out of
    Seattle to a centre named after somewhere in British Columbia.
    """
    for airport in airports:
        facility = _facility(airport, "center", ("center",))
        if facility is not None:
            return Facility("center", facility.station, facility.mhz, None, facility.alternates)
    return Facility("center", f"{name} Center", mhz)


# An airport big enough for the enroute centre around it to be named after: a real field with real ATC,
# not a farm strip or a hospital helipad.
SECTOR_RUNWAY_M = 1800.0
SECTOR_MHZ = (132.0, 135.975)  # centres live up here; a made-up sector frequency is picked from this band


def sector_name(airport: Airport) -> str:
    """ "Kelowna" from an airport, for the centre working the airspace around it.

    The place, not the field: "Seattle-Tacoma Intl" is Seattle, "Wainwright(Field 21)" is Wainwright.
    """
    first = re.split(r"[-/(]", airport.name or airport.icao)[0]
    words = [w for w in first.split() if w.upper() not in NOISE_WORDS]
    return " ".join(w.capitalize() for w in words[:2]) if words else airport.icao.upper()


def is_sector_airport(airport: Airport) -> bool:
    if not any(f.kind in ("tower", "approach", "departure", "center") for f in airport.frequencies):
        return False
    return any(r.length_m >= SECTOR_RUNWAY_M for r in airport.runways)


def sector_center(airport: Airport, taken: tuple[float, ...] = ()) -> Facility:
    """The enroute centre around ``airport``: "Edmonton Center" on its own frequency.

    Real sector boundaries and frequencies aren't in the sim's data, so a centre is named after the
    nearest sizeable airport and given a frequency of its own, derived from that name so the same
    place is always the same frequency. ``taken`` keeps it off a frequency another controller works.
    """
    if (published := _facility(airport, "center", ("center",))) is not None:
        return Facility("center", published.station, published.mhz, None, published.alternates)
    name = sector_name(airport)
    low, high = (round(f * 1000) for f in SECTOR_MHZ)
    slots = [f / 1000 for f in range(low, high + 1, 25)]
    busy = {channel_khz(f) for f in taken}
    free = [f for f in slots if channel_khz(f) not in busy] or slots
    return Facility("center", f"{name} Center", free[zlib.crc32(name.encode()) % len(free)])
