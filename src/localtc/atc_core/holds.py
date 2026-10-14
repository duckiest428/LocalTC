"""Holds and the arrival sequence: a hold given as structured state, its entry, its racetrack, the expected further
clearance time and where the flight sits in the sequence, and why.

A hold is given when there's a reason a controller would give one:

- **arrival demand**: more aircraft ahead of this one on their way down to the destination than the runway takes in
  the time it needs to get there (``ARRIVAL_RATE_PER_H`` an hour, a minute and a half to two minutes apart);
- **weather**: the destination below the minimums of every approach it has;
- **the runways closed** (its NOTAMs);
- **asked for** ("request holding at BLAKO").

The hold is over the arrival's last fix of the route (the STAR's, else the route's, 15 to 60 miles out), else
none is given (the controller vectors for spacing instead). It's flown inbound towards the airport, right turns,
one-minute legs at or below 14,000 ft and a minute and a half above (ICAO), at the altitude the flight is
cleared to. Its entry (direct, parallel or teardrop) follows from the heading the aircraft has to the fix.

ATC says why it's holding, where it is in the sequence and the expected further clearance time, and says again
when either changes; it clears the flight out of the hold when its turn comes ("cleared direct BLAKO, descend ...")
or the reason is gone. ``Hold.to_json`` carries the racetrack for the maps.
"""

import math
from dataclasses import dataclass, field

HOLD_LEG_LOW_MIN = 1.0
HOLD_LEG_HIGH_MIN = 1.5
HIGH_HOLD_FT = 14000
ARRIVAL_RATE_PER_H = 30.0  # the runway's landings an hour: one every two minutes
DEMAND_NM = 60.0  # arrivals within this of the destination count for the demand
HOLD_FROM_NM = 15.0  # a hold fix this far ...
HOLD_TO_NM = 60.0  # ... to this far from the destination
HOLD_DEMAND = 5  # ahead of this flight this many or more, still well out: it holds
TURN_RATE_DEG_S = 3.0  # standard rate
EFC_SLACK_MIN = 2
STATES = ("cleared", "entering", "holding", "leaving", "done", "cancelled")


@dataclass
class Hold:
    fix: str
    lat: float
    lon: float
    inbound_true: float
    inbound_mag: int
    turn: str  # "right" or "left"
    leg_min: float
    altitude_ft: int
    reason: str  # "arrival traffic", "weather", "runway closed", "requested"
    issued_t: float
    efc_t: float
    efc_clock: str  # "1432" (Z), or "" when the sim's time isn't known
    position: int = 0  # in the sequence (1: next)
    delay_min: int = 0
    entry: str = ""
    state: str = "cleared"
    laps: int = 0
    entered_t: float | None = None
    last_said_position: int = 0
    tas_kt: float = 230.0
    history: list[tuple[float, str]] = field(default_factory=list)

    def racetrack(self, points: int = 24) -> list[tuple[float, float]]:
        """The racetrack's outline (lat, lon), for the maps."""
        return racetrack(self.lat, self.lon, self.inbound_true, self.turn, self.leg_nm(), self.radius_nm(), points)

    def leg_nm(self) -> float:
        return self.tas_kt * self.leg_min / 60.0

    def radius_nm(self) -> float:
        # Standard rate: a 180 degree turn in a minute, at the hold speed.
        return self.tas_kt / (math.pi * 60.0) * (180.0 / TURN_RATE_DEG_S / 60.0)

    def to_json(self) -> dict:
        return {"fix": self.fix, "lat": self.lat, "lon": self.lon, "inbound": self.inbound_mag, "turn": self.turn,
                "leg_min": self.leg_min, "altitude_ft": self.altitude_ft, "reason": self.reason,
                "issued_t": self.issued_t, "efc_t": self.efc_t, "efc": self.efc_clock, "position": self.position,
                "delay_min": self.delay_min, "entry": self.entry, "state": self.state, "laps": self.laps,
                "racetrack": [list(p) for p in self.racetrack()]}


def entry_for(heading_to_fix: float, inbound: float, turn: str) -> str:
    """The entry for a hold by the aircraft's heading to the fix (degrees, the same reference as ``inbound``): the
    three sectors either side of the 70 degree line (ICAO Doc 8168): direct 180 degrees wide, parallel 110, teardrop
    70."""
    rel = (heading_to_fix - inbound) % 360 if turn == "right" else (inbound - heading_to_fix) % 360
    if 110 <= rel < 180:
        return "teardrop"
    if 180 <= rel < 290:
        return "parallel"
    return "direct"


def racetrack(lat: float, lon: float, inbound_true: float, turn: str, leg_nm: float, radius_nm: float,
              points: int = 24) -> list[tuple[float, float]]:
    """The hold's outline: the fix at the end of the inbound leg, the holding side to the ``turn`` side of it."""
    side = 1.0 if turn == "right" else -1.0
    ux, uy = math.sin(math.radians(inbound_true)), math.cos(math.radians(inbound_true))  # along the inbound course
    px, py = uy * side, -ux * side  # towards the holding side
    # In nm east/north of the fix: the inbound leg from (start) to (0, 0), the turn, the outbound leg, the turn.
    out: list[tuple[float, float]] = []
    half = max(4, points // 4)
    cx, cy = px * radius_nm, py * radius_nm  # the turn at the fix's end: its centre
    for i in range(half + 1):  # turning from the inbound course to the outbound, over the fix's end
        a = math.pi * i / half
        # Start pointing back across to the inbound leg (-p), sweep through +u to +p.
        vx = -px * math.cos(a) + ux * math.sin(a)
        vy = -py * math.cos(a) + uy * math.sin(a)
        out.append((cx + vx * radius_nm, cy + vy * radius_nm))
    bx, by = cx - ux * leg_nm, cy - uy * leg_nm  # the far turn's centre
    for i in range(half + 1):
        a = math.pi * i / half
        vx = px * math.cos(a) - ux * math.sin(a)
        vy = py * math.cos(a) - uy * math.sin(a)
        out.append((bx + vx * radius_nm, by + vy * radius_nm))
    out.append(out[0])
    k = 1.0 / 60.0
    return [(round(lat + y * k, 5), round(lon + x * k / max(0.01, math.cos(math.radians(lat))), 5)) for x, y in out]


def leg_minutes(altitude_ft: float) -> float:
    return HOLD_LEG_HIGH_MIN if altitude_ft > HIGH_HOLD_FT else HOLD_LEG_LOW_MIN


def delay_minutes(position: int, rate_per_h: float = ARRIVAL_RATE_PER_H) -> int:
    """The delay for ``position`` in the sequence (1: next): the ones ahead each take the runway's spacing."""
    spacing = 60.0 / max(1.0, rate_per_h)
    return max(0, int(math.ceil((position - 1) * spacing)))


def clock(zulu_s: float | None, in_s: float) -> str:
    """The time of day ``in_s`` from now as ATC says it ("1432"), from the sim's time; "" when unknown. Rounded up
    to the minute."""
    if zulu_s is None:
        return ""
    when = (zulu_s + in_s) % 86400
    minutes = int(math.ceil(when / 60.0)) % 1440
    return f"{minutes // 60:02d}{minutes % 60:02d}"


def nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 60.0
    dx = (lon2 - lon1) * 60.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = lat2 - lat1
    dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.degrees(math.atan2(dx, dy)) % 360
