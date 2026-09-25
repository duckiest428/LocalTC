"""How far a controller's radio reaches: an airport's frequencies work near the airport, not across a continent.

VHF goes in straight lines: it reaches the radio horizon and no further. With both ends above a smooth earth
(4/3 its radius, for the way radio bends round it) the horizon is about 1.23 x (sqrt h1 + sqrt h2) nautical
miles, heights in feet (FAA Order 6050.32B, Appendix 2: d = sqrt(2h) statute miles for each end). An aircraft
at 3,000 ft and a tower antenna at 60 ft see each other out to about 77 nm; on the ground at another airport
20 miles away, only to about 13.

Within the horizon, a facility's frequency is engineered to work inside its "frequency protected service
volume" (6050.32B, Appendix 2, figure 5, typical terminal FPSVs):

    ground control, clearance delivery   3 nm        100 ft
    local control (tower)               10/15/30 nm  2,500/5,000/10,000 ft  (small / typical / large)
    approach and departure (arrival)    30/45/55 nm  7,500/10,000/24,000 ft
    ATIS                                up to 60 nm  25,000 ft

A protected volume is where the frequency is guaranteed free of interference, not where the signal stops: a
radio carries on, weaker, past its edge. Here the usable range is ``USABLE`` times the protected radius,
never past the horizon. The centres aren't limited: each works its airspace through a network of remote
sites (75 nm low, 150 nm high each), and the centre the pilot is given is the one whose airspace the aircraft
is in.
"""

import math
from dataclasses import dataclass

NM_PER_SQRT_FT = 1.23  # the 4/3-earth radio horizon, nm per sqrt(feet)
COCKPIT_FT = 10.0  # an aircraft on the ground: its antenna is about this high
USABLE = 2.0  # a frequency is heard out to this many times its protected radius, horizon permitting

# Protected radius (nm) by airport size: small, typical, large (FAA 6050.32B, App. 2, fig. 5).
SERVICE_NM: dict[str, tuple[float, float, float]] = {
    "clearance": (3.0, 3.0, 3.0),
    "ground": (3.0, 3.0, 3.0),
    "tower": (10.0, 15.0, 30.0),
    "approach": (30.0, 45.0, 55.0),
    "departure": (30.0, 45.0, 55.0),
    "atis": (30.0, 45.0, 60.0),
}
# How high the antenna is (ft): on the tower cab or a mast for the terminal radar's remote sites.
ANTENNA_FT: dict[str, float] = {"clearance": 40.0, "ground": 40.0, "tower": 80.0, "approach": 150.0,
                                "departure": 150.0, "atis": 80.0}


@dataclass(frozen=True)
class Reach:
    distance_nm: float
    range_nm: float  # how far it reaches from here (the horizon at this altitude, or the usable radius)
    service_nm: float  # the protected radius: inside it, loud and clear

    @property
    def in_range(self) -> bool:
        return self.distance_nm <= self.range_nm

    @property
    def readability(self) -> float:
        """1 inside the protected volume, fading to 0.3 at the edge of the range, 0 beyond it."""
        if not self.in_range:
            return 0.0
        if self.distance_nm <= self.service_nm or self.range_nm <= self.service_nm:
            return 1.0
        return 1.0 - 0.7 * (self.distance_nm - self.service_nm) / (self.range_nm - self.service_nm)


def horizon_nm(aircraft_agl_ft: float, antenna_ft: float) -> float:
    """The radio horizon between an aircraft and a ground antenna (4/3 earth)."""
    return NM_PER_SQRT_FT * (math.sqrt(max(aircraft_agl_ft, COCKPIT_FT)) + math.sqrt(antenna_ft))


def airport_size(runways: int, longest_m: float) -> int:
    """0 small, 1 typical, 2 large: for the protected volumes."""
    if runways >= 3 or longest_m >= 3500:
        return 2
    if longest_m < 1800:
        return 0
    return 1


def reach(controller: str, distance_nm: float, aircraft_agl_ft: float, size: int = 1) -> Reach | None:
    """How a facility's radio reaches an aircraft ``distance_nm`` from its airport; None for a controller whose
    radio isn't limited here (a centre)."""
    if controller not in SERVICE_NM:
        return None
    service = SERVICE_NM[controller][max(0, min(2, size))]
    usable = min(horizon_nm(aircraft_agl_ft, ANTENNA_FT[controller]), service * USABLE)
    return Reach(distance_nm=distance_nm, range_nm=usable, service_nm=min(service, usable))


__all__ = ["Reach", "airport_size", "horizon_nm", "reach"]
