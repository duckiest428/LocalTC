"""ATC facilities (controllers) for a flight: who works which frequency."""

from dataclasses import dataclass

from localtc.atc_core.phraseology import speech
from localtc.sim_api import Airport

CONTROLLERS = ("clearance", "ground", "tower", "departure", "center", "approach")


@dataclass(frozen=True)
class Facility:
    controller: str  # one of CONTROLLERS
    station: str  # spoken/display station name, e.g. "Paine Tower"
    mhz: float
    airport: str | None = None  # ICAO, None for center

    def matches(self, mhz: float) -> bool:
        return abs(self.mhz - mhz) < 0.004


def _station(airport: Airport, kinds: tuple[str, ...], fallback_suffix: str, rename: tuple[str, str] | None = None) -> tuple[str, float] | None:
    freq = next((f for kind in kinds for f in airport.frequencies if f.kind == kind), None)
    if freq is None:
        return None
    name = speech.station_name(freq.name) if freq.name else ""
    if rename and name.endswith(rename[0]):
        name = name[: -len(rename[0])] + rename[1]
    if not name:
        name = f"{speech.airport_name(airport.name, airport.icao).replace(' Field', '')} {fallback_suffix}"
    return name, freq.mhz


def airport_facilities(airport: Airport, *, role: str) -> list[Facility]:
    """Facilities at an origin ("departure" role) or destination ("arrival" role) airport."""
    specs = [
        ("clearance", ("clearance", "clearance_pretaxi", "remote_clearance"), "Clearance", None),
        ("ground", ("ground",), "Ground", None),
        ("tower", ("tower",), "Tower", None),
    ]
    if role == "departure":
        specs.append(("departure", ("departure", "approach"), "Departure", ("Approach", "Departure")))
    else:
        specs.append(("approach", ("approach", "departure"), "Approach", ("Departure", "Approach")))
    facilities = []
    for controller, kinds, suffix, rename in specs:
        found = _station(airport, kinds, suffix, rename)
        if found is not None:
            facilities.append(Facility(controller=controller, station=found[0], mhz=found[1], airport=airport.icao))
    # No clearance delivery: ground issues clearances.
    if not any(f.controller == "clearance" for f in facilities):
        ground = next((f for f in facilities if f.controller == "ground"), None)
        if ground is not None:
            facilities.append(Facility("clearance", ground.station, ground.mhz, airport.icao))
    return facilities


def center_facility(airports: list[Airport], name: str, mhz: float) -> Facility:
    for airport in airports:
        found = _station(airport, ("center",), "Center")
        if found is not None:
            return Facility("center", found[0], found[1])
    return Facility("center", f"{name} Center", mhz)
