"""ATC facilities (controllers) for a flight: who works which frequency, and what they're called on the radio."""

import re
from dataclasses import dataclass

from localtc.atc_core.phraseology import speech
from localtc.sim_api import Airport, Frequency

CONTROLLERS = ("clearance", "ground", "tower", "departure", "center", "approach")
SUFFIXES = {"clearance": "Clearance", "ground": "Ground", "tower": "Tower", "departure": "Departure",
            "approach": "Approach", "center": "Center"}
# Words in sim frequency names that describe the airport, not the radio callsign.
NOISE_WORDS = {
    "INTL", "INTERNATIONAL", "AIRPORT", "ARPT", "FLD", "FIELD", "CO", "COUNTY", "MUNI", "MUNICIPAL", "RGNL",
    "REGIONAL", "APP", "APPR", "APPROACH", "DEP", "DEPARTURE", "TWR", "TOWER", "GND", "GROUND", "CLNC", "CLR",
    "CLEARANCE", "DELIVERY", "DEL", "CTR", "CENTER", "CENTRE", "ATIS", "MEM", "MEMORIAL", "EXEC", "EXECUTIVE",
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
    return " ".join(w.capitalize() for w in words) + f" {suffix}"


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
    for airport in airports:
        facility = _facility(airport, "center", ("center",))
        if facility is not None:
            return Facility("center", facility.station, facility.mhz, None, facility.alternates)
    return Facility("center", f"{name} Center", mhz)
