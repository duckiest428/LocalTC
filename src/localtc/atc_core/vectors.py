"""Radar vectors onto a final approach: the pattern an approach controller flies an arrival around.

Everything is worked out in the runway's own frame: ``out`` is how far the aircraft is out along the
extended centreline (positive on the approach side), ``side`` how far off it, and on which side.

- **Straight in**: out beyond the base turn and inside a cone around the final, the aircraft is pointed at
  a spot on the centreline it can join at 30 degrees.
- **Downwind**: anywhere else, it joins a downwind leg ``OFFSET_NM`` off the centreline on its own side
  (at 45 degrees from further out), then flies it outbound, parallel to the runway.
- **Base**: abeam a point ``BASE_NM`` out, it turns at right angles towards the centreline.
- **Intercept**: close to the centreline, a 30 degree cut to join the final, about ``GATE_NM`` out.

Too high to make it in from where it is, the aircraft is sent out on a downwind first (``extended``).

Each leg comes with the flying distance still to go, which sets the step-down altitudes: about 250 ft a
mile above the field, never below the altitude the approach starts from.
"""

import math
from dataclasses import dataclass

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport.geometry import RunwayEndGeometry

NM_M = 1852.0
FT_PER_NM = 250  # stepping down: 250 ft for every mile still to fly
GLIDESLOPE_FT_PER_NM = 318  # a three degree glideslope


@dataclass(frozen=True)
class Pattern:
    gate_nm: float  # the final is joined no closer than this
    base_nm: float  # the base leg is this far out
    offset_nm: float  # the downwind is this far from the centreline
    cone_deg: float = 35.0  # within this of the final (seen from the runway), an arrival goes straight in
    lead_nm: float = 2.0  # the base turn is given this much before the base point


JET = Pattern(gate_nm=9.0, base_nm=17.0, offset_nm=6.0)
SLOW = Pattern(gate_nm=5.0, base_nm=10.0, offset_nm=3.5, lead_nm=1.0)
SLOW_KT = 160.0  # flying slower than this, the tighter pattern


@dataclass(frozen=True)
class Vector:
    heading_true: float
    leg: str  # "straight_in", "join" (to the downwind), "downwind", "base", "intercept"
    track_nm: float  # flying distance left to the threshold, this way round
    side: float = 1.0  # which side of the final the pattern is on (+1 right of the landing direction)
    join_nm: float | None = None  # where it meets the final, miles out (straight in, intercept)


def _heading(east: float, north: float) -> float:
    return math.degrees(math.atan2(east, north)) % 360


def frame(geo: AirportGeometry, end: RunwayEndGeometry, lat: float, lon: float) -> tuple[float, float]:
    """(out, side) in nm: out along the extended centreline from the threshold, and off it (right of the
    landing direction positive)."""
    x, y = geo.xy(lat, lon)
    c = math.radians(end.heading_true)
    dx, dy = x - end.threshold[0], y - end.threshold[1]
    out = -(dx * math.sin(c) + dy * math.cos(c))
    side = dx * math.cos(c) - dy * math.sin(c)
    return out / NM_M, side / NM_M


def _towards(end: RunwayEndGeometry, out: float, side: float, to_out: float, to_side: float) -> float:
    """The true heading from (out, side) to (to_out, to_side), in the runway frame."""
    c = math.radians(end.heading_true)
    d_out, d_side = to_out - out, to_side - side
    # back to east/north: out is -inbound, side is +right of inbound
    east = -d_out * math.sin(c) + d_side * math.cos(c)
    north = -d_out * math.cos(c) - d_side * math.sin(c)
    return _heading(east, north)


ORDER = {"join": 0, "downwind": 1, "straight_in": 2, "base": 2, "intercept": 3}


def vector(geo: AirportGeometry, end: RunwayEndGeometry, lat: float, lon: float, *, slow: bool = False,
           after: str | None = None, side: float | None = None, heading_true: float | None = None) -> Vector:
    """The next leg onto the final. ``after`` is the leg the aircraft was last given and ``side`` the side of
    the final its pattern is on: the pattern only moves on (join, downwind, base, intercept), and stays on its
    side, so an aircraft drifting over a boundary, or across the centreline, isn't turned back."""
    p = SLOW if slow else JET
    out, lateral = frame(geo, end, lat, lon)
    s = side if side is not None and after in ("join", "downwind", "base") else (1.0 if lateral >= 0 else -1.0)
    off = lateral * s  # towards the pattern's side: negative once across the centreline
    if after not in ("join", "downwind", "base"):
        off = abs(lateral)
    inbound, outbound = end.heading_true % 360, (end.heading_true + 180) % 360
    toward_centreline = (inbound - 90 * s) % 360  # at right angles to the final, towards it
    patterned = after in ("join", "downwind", "base")

    def intercept() -> Vector:
        cut = 1.0 if lateral >= 0 else -1.0  # always from the side it is actually on
        join = out - abs(lateral) * math.sqrt(3)  # a 30 degree cut meets the final here
        return Vector((inbound - 30 * cut) % 360, "intercept", 2 * abs(lateral) + join, cut, join)

    outbound_now = heading_true is not None and abs(((heading_true - inbound + 540) % 360) - 180) > 100
    if after == "intercept" or (abs(lateral) <= 3.5 and out >= p.gate_nm - 2 and not outbound_now):
        return intercept()  # close to the centreline and far enough out: cut in at 30 degrees
    if abs(lateral) <= 3.5 and out >= p.gate_nm - 2:  # flying away from the runway: a base turn first
        cut = 1.0 if lateral >= 0 else -1.0
        return Vector((inbound - 90 * cut) % 360, "base", abs(lateral) + out, cut)
    if after == "base" or (patterned and out >= p.base_nm - p.lead_nm):  # turning takes a mile or two
        return Vector(toward_centreline, "base", abs(off) + max(out, p.gate_nm), s)
    if not patterned and out >= p.base_nm and off <= out * math.tan(math.radians(p.cone_deg)):
        # Out on the approach side, roughly in line: straight at a point it can join at 30 degrees.
        join = max(p.gate_nm, out - off * math.sqrt(3))
        return Vector(_towards(end, out, lateral, join, 0.0), "straight_in", math.hypot(out - join, off) + join, s, join)
    if out >= p.base_nm:
        return Vector(toward_centreline, "base", off + out, s)
    if after == "downwind" or abs(off - p.offset_nm) <= 2.0:
        return Vector(outbound, "downwind", (p.base_nm - out) + abs(off) + p.base_nm, s)
    # Join the downwind at 45 degrees, a little further out than now (from inside it: straight out to it).
    ahead = min(p.base_nm, out + max(2.0, abs(off - p.offset_nm)))
    heading = _towards(end, out, lateral, ahead, s * p.offset_nm)
    track = math.hypot(ahead - out, off - p.offset_nm) + (p.base_nm - ahead) + p.offset_nm + p.base_nm
    return Vector(heading, "join", track, s)


def too_high(leg: Vector, altitude_ft: float, elev_ft: float) -> bool:
    """Too high to go in this way: more than 1,500 ft above a three degree path from where it would join."""
    miles = leg.join_nm if leg.join_nm is not None else leg.track_nm
    return altitude_ft - elev_ft > miles * GLIDESLOPE_FT_PER_NM + 1500


def extended(geo: AirportGeometry, end: RunwayEndGeometry, lat: float, lon: float) -> Vector:
    """Too high to turn in: a downwind outbound on the side the aircraft is on, to make room to get down."""
    out, lateral = frame(geo, end, lat, lon)
    s = 1.0 if lateral >= 0 else -1.0
    return Vector((end.heading_true + 180) % 360, "downwind", out + abs(lateral), s)


def step_altitude(track_nm: float, elev_ft: float, floor_ft: int) -> int:
    """The altitude to be down to with ``track_nm`` still to fly: 250 ft a mile above the field, in whole
    thousands, never below ``floor_ft`` (where the approach itself starts)."""
    wanted = int((elev_ft + track_nm * FT_PER_NM) // 1000 * 1000)
    return max(floor_ft, wanted)
