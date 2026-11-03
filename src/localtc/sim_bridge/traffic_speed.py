"""How fast LocalTC's own aircraft are really going, for everything that reads the sim's traffic.

The bridge places them itself (``motion.py``), frozen against the sim's physics, so the sim reports each one at 0 knots,
and those it isn't rolling as off the ground: one lining up was taken for "Boeing 737 landing" while nobody was, and a
taxiing one for parked. Here each one's speed comes from how far it went since the last look.
"""

import msgspec

from localtc.sim_api import TrafficTarget
from localtc.sim_api.geo import haversine_nm

FLYING_KT = 40.0  # nothing below this is flying (a helicopter's report has its own speed in it)
MIN_GAP_S = 0.5


class TrafficSpeeds:
    def __init__(self) -> None:
        self._last: dict[int, tuple[float, float, float, float]] = {}  # object id: lat, lon, when, speed

    def apply(self, targets: tuple[TrafficTarget, ...], t: float) -> tuple[TrafficTarget, ...]:
        out, last = [], {}
        for target in targets:
            before = self._last.get(target.object_id)
            if target.gs_kt >= 1.0 or before is None:  # the sim's own (an aircraft it flies), or first seen
                last[target.object_id] = (target.lat, target.lon, t, target.gs_kt)
                out.append(target)
                continue
            gap = t - before[2]
            if gap < MIN_GAP_S:
                last[target.object_id] = before
                gs = before[3]
            else:
                gs = haversine_nm(before[0], before[1], target.lat, target.lon) / gap * 3600.0
                last[target.object_id] = (target.lat, target.lon, t, gs)
            out.append(msgspec.structs.replace(target, gs_kt=round(gs, 1), on_ground=target.on_ground or gs < FLYING_KT))
        self._last = last
        return tuple(out)
