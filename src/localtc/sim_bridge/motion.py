"""LocalTC's own aircraft moving smoothly in the sim (its traffic, ``AiTrack``).

The traffic knows where each aircraft is a second or so at a time (live positions every few seconds, carried on in
between); the sim draws them every frame. So the bridge carries each one on from its last update by dead reckoning
(its speed, climb and turn), writes it to the sim as often as it can be seen to matter (many times a second close
by, less far away), and when an update lands somewhere else than it was being shown, eases the difference out over a
few seconds instead of jumping.
"""

import math
from dataclasses import dataclass, field

M_PER_NM = 1852.0
M_PER_DEG = 111_320.0
KT_TO_MPS = M_PER_NM / 3600.0
HORIZON_S = 20.0  # carried on this long past its last update at most, then held where it got to
# How often it's written to the sim, by its distance from the user: close by every frame or so, far away a few times
# a minute is plenty.
RATES = ((3.0, 1 / 30), (10.0, 1 / 10), (30.0, 1 / 2), (math.inf, 2.0))


def advance(lat: float, lon: float, hdg: float, gs_kt: float, turn_dps: float, dt: float) -> tuple[float, float, float]:
    """Where an aircraft at ``lat, lon`` heading ``hdg`` is ``dt`` seconds on, turning steadily."""
    steps = max(1, int(abs(dt)))
    step = dt / steps
    for _ in range(steps):
        mid = math.radians(hdg + turn_dps * step / 2)
        d = gs_kt * KT_TO_MPS * step
        lat += d * math.cos(mid) / M_PER_DEG
        lon += d * math.sin(mid) / (M_PER_DEG * max(0.01, math.cos(math.radians(lat))))
        hdg = (hdg + turn_dps * step) % 360
    return lat, lon, hdg


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


@dataclass
class Track:
    """One aircraft as the bridge is moving it."""

    object_id: int
    lat: float
    lon: float
    alt_ft: float
    hdg: float
    gs_kt: float
    vs_fpm: float
    turn_dps: float
    on_ground: bool
    pitch: float
    bank: float
    t0: float  # when this was where it is (monotonic)
    blend_s: float = 3.0
    # The difference from where it was shown when this update came, eased out over blend_s.
    off: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    ground_ft: float | None = None  # the ground under it plus its own height off it (from the sim)
    next_write: float = 0.0
    frozen: bool = False
    lights: tuple = field(default_factory=tuple)

    def raw(self, now: float) -> tuple[float, float, float, float]:
        """Lat, lon, altitude and heading on its own reckoning, without the easing."""
        dt = min(max(0.0, now - self.t0), HORIZON_S)
        lat, lon, hdg = advance(self.lat, self.lon, self.hdg, self.gs_kt, self.turn_dps, dt)
        alt = self.alt_ft + self.vs_fpm * dt / 60.0
        return lat, lon, alt, hdg

    def at(self, now: float) -> tuple[float, float, float, float, float, float]:
        """Lat, lon, altitude, heading, pitch and bank to show now."""
        lat, lon, alt, hdg = self.raw(now)
        k = max(0.0, 1.0 - (now - self.t0) / self.blend_s) if self.blend_s > 0 else 0.0
        k = k * k * (3 - 2 * k)  # eased: no sudden stop at the end
        lat, lon = lat + self.off[0] * k, lon + self.off[1] * k
        alt, hdg = alt + self.off[2] * k, (hdg + self.off[3] * k) % 360
        if self.on_ground and self.ground_ft is not None:
            alt = self.ground_ft
        return lat, lon, alt, hdg, self.pitch, self.bank

    def update(self, new: "Track", now: float) -> None:
        """A new state: the difference from what's being shown now is eased out from here."""
        shown = self.at(now)
        self.lat, self.lon, self.alt_ft, self.hdg = new.lat, new.lon, new.alt_ft, new.hdg
        self.gs_kt, self.vs_fpm, self.turn_dps = new.gs_kt, new.vs_fpm, new.turn_dps
        self.pitch, self.bank, self.blend_s, self.t0 = new.pitch, new.bank, new.blend_s, now
        if self.on_ground != new.on_ground:
            self.on_ground = new.on_ground
        lat, lon, alt, hdg = self.raw(now)
        alt_shown = shown[2] if not (new.on_ground and self.ground_ft is not None) else alt
        self.off = (shown[0] - lat, shown[1] - lon, alt_shown - alt, _wrap(shown[3] - hdg))

    def interval(self, user: tuple[float, float] | None, lat: float, lon: float) -> float:
        if user is None:
            return RATES[1][1]
        nm = math.hypot((lat - user[0]) * M_PER_DEG, (lon - user[1]) * M_PER_DEG * math.cos(math.radians(lat))) / M_PER_NM
        return next(every for within, every in RATES if nm <= within)
