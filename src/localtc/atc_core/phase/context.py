"""Per-tick position context: where the aircraft is relative to known airports."""

from dataclasses import dataclass

from localtc.atc_core.airport import AirportGeometry, FinalApproach, HoldShort, RunwayEndGeometry, RunwayGeometry
from localtc.sim_api import Airport, OwnshipState

NEAR_AIRPORT_NM = 10.0
DESTINATION_ONLY_NM = 25.0  # this close to where you are going, "lined up" can only mean one of its runways


@dataclass(frozen=True)
class PositionContext:
    airport: AirportGeometry | None = None  # nearest known airport within NEAR_AIRPORT_NM
    airport_distance_nm: float | None = None
    runway: RunwayGeometry | None = None  # runway polygon the aircraft is on
    on_runway: bool = False  # polygon or the sim's ON ANY RUNWAY flag
    runway_end: RunwayEndGeometry | None = None  # end aligned with the aircraft heading, when on a runway
    hold_short: HoldShort | None = None
    hold_short_distance_m: float | None = None
    destination: AirportGeometry | None = None
    destination_distance_nm: float | None = None
    final: FinalApproach | None = None  # lined up to land (destination first, else the nearest airport)

    @property
    def aligned_on_runway(self) -> bool:
        # Without airport data, trust the sim's runway flag alone.
        return self.runway_end is not None or (self.on_runway and self.runway is None)


class ContextBuilder:
    def __init__(self, *, destination: str | None = None, runway_heading_tolerance: float = 20.0) -> None:
        self.airports: dict[str, AirportGeometry] = {}
        self.destination = destination.upper() if destination else None
        self._tolerance = runway_heading_tolerance

    def add_airport(self, airport: Airport) -> AirportGeometry:
        geometry = AirportGeometry(airport)
        self.airports[airport.icao.upper()] = geometry
        return geometry

    def build(self, own: OwnshipState) -> PositionContext:
        nearest, nearest_nm = None, None
        for geometry in self.airports.values():
            # Heliports and helipads (no runways) carry no usable geometry and would displace the real airport.
            if not geometry.runways and geometry.icao != self.destination:
                continue
            d = geometry.distance_nm(own.lat, own.lon)
            if nearest_nm is None or d < nearest_nm:
                nearest, nearest_nm = geometry, d
        if nearest_nm is not None and nearest_nm > NEAR_AIRPORT_NM:
            nearest = None

        runway = end = hold = None
        hold_m = None
        if nearest is not None:
            runway = nearest.runway_at(own.lat, own.lon)
            if runway is not None:
                end = runway.end_for_heading(own.hdg_true, self._tolerance)
            found = nearest.nearest_hold_short(own.lat, own.lon)
            if found is not None:
                hold, hold_m = found

        destination = self.airports.get(self.destination) if self.destination else None
        dest_nm = destination.distance_nm(own.lat, own.lon) if destination else None
        # Close to the destination, only its runways count. Airfields crowd together near a big airport, and
        # lining up with one of their runways on the way in is a coincidence, not the approach being flown.
        arriving = dest_nm is not None and dest_nm <= DESTINATION_ONLY_NM
        final = None
        for candidate in (destination, None if arriving else nearest):
            if candidate is not None and not own.on_ground:
                final = candidate.final_approach(own.lat, own.lon, own.hdg_true)
                if final is not None:
                    break

        return PositionContext(
            airport=nearest,
            airport_distance_nm=nearest_nm,
            runway=runway,
            on_runway=runway is not None or own.on_runway,
            runway_end=end,
            hold_short=hold,
            hold_short_distance_m=hold_m,
            destination=destination,
            destination_distance_nm=dest_nm,
            final=final,
        )
