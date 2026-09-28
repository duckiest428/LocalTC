"""Each airport's current ATIS, and when its letter advances.

A new ATIS (the next letter, A to Z and round again) is recorded (JO 7110.65 2-9-2):

- with every new official weather observation, hourly at 53 minutes past, whatever it says;
- when the weather changes enough to matter between them (a special): the wind, the visibility across 3 miles,
  the altimeter by 0.02, precipitation starting or stopping, a ceiling appearing;
- when the operations change: the runway in use, the approaches, the notices, the runway condition, low
  visibility procedures or wind shear.

Not more than once every ``MIN_UPDATE_S`` though, so a gusty wind doesn't spin the letters. The ATIS also fixes
each airport's runway in use, so the runway doesn't flip with every gust: it changes only when the tailwind on
it gets too strong (and never onto a closed one), and the broadcast says a runway change is in progress for a
while after.
"""

import math
import zlib

from localtc.atc_core.airport import AirportGeometry, select_runway
from localtc.atc_core.airport.approaches import Aircraft, choose_approach, options
from localtc.atc_core.airport.geometry import RunwayEndGeometry
from localtc.atc_core.atis.broadcast import AtisInfo
from localtc.atc_core.atis.observation import Weather
from localtc.atc_core.atis.operations import (
    SIMULTANEOUS_M,
    Notice,
    Operations,
    advisories,
    closed_ends,
    notices_for,
    outages,
    parallels,
)
from localtc.atc_core.region import Region, region_for

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_TAILWIND_KT = 5.0  # the runway in use changes once the tailwind on it is stronger than this
CROSSWIND_CAUTION_KT = 12.0
MIN_UPDATE_S = 600.0  # an ATIS changes at most this often (session time)
RUNWAY_CHANGE_S = 900.0  # "runway change in progress" this long after one
OBSERVED_AT_MIN = 53  # routine observations: hourly at 53 minutes past
TYPICAL = Aircraft(airline=True)  # what an ATIS advertises approaches for: the airliners that fly them


def components(end: RunwayEndGeometry, w: Weather) -> tuple[float, float]:
    """(headwind, crosswind) in knots on a runway end; a tailwind is a negative headwind."""
    angle = math.radians(w.wind_dir_true - end.heading_true)
    speed = w.gust_kt or w.wind.speed_kt
    return speed * math.cos(angle), abs(speed * math.sin(angle))


def remarks(geo: AirportGeometry, end: RunwayEndGeometry, w: Weather) -> tuple[str, ...]:
    """The "caution ..." notes a controller would add for this weather."""
    notes = []
    _, crosswind = components(end, w)
    if w.gust_kt:
        notes.append("caution gusty winds")
    if crosswind >= CROSSWIND_CAUTION_KT:
        notes.append("caution strong crosswind")
    if w.visibility_sm is not None and w.visibility_sm < 3:
        notes.append("caution low visibility")
    if w.precip == "rain":
        notes.append("caution wet runway")
    elif w.precip == "snow":
        notes.append("caution snow on the runway")
    if w.temperature_c is not None and geo.airport.elev_ft + 120 * max(0, w.temperature_c - (15 - 2 * geo.airport.elev_ft / 1000)) >= 5000:
        notes.append("caution high density altitude")
    return tuple(notes)


def observation_slot(zulu_s: float | None) -> int:
    """Which hourly observation is the latest at ``zulu_s`` (it changes at hh:53)."""
    return int(((zulu_s or 0.0) - OBSERVED_AT_MIN * 60) // 3600)


class AtisBoard:
    """The current ATIS for each airport; the letter advances with each new observation and each change."""

    def __init__(self, seed: int = 0, *, notams: bool = True, region: Region | None = None) -> None:
        self.seed = seed
        self.notams = notams
        self.region = region  # the phraseology setting, when it isn't "auto"
        self.current: dict[str, AtisInfo] = {}
        self._issued: dict[str, float] = {}
        self._runway_changed: dict[str, float] = {}
        self._notices: dict[str, tuple[Notice, ...]] = {}

    def notices(self, icao: str, geo: AirportGeometry) -> tuple[Notice, ...]:
        if not self.notams:
            return ()
        if icao not in self._notices:
            self._notices[icao] = notices_for(geo, self.seed)
        return self._notices[icao]

    def update(self, icao: str, geo: AirportGeometry, weather: Weather, zulu_s: float | None, name: str,
               t: float = 0.0, *, kind: str = "both") -> AtisInfo | None:
        """Refresh an airport's ATIS; returns it if it's new or its letter changed."""
        key = icao if kind == "both" else f"{icao}/{kind}"
        old = self.current.get(key)
        if old is not None and t - self._issued.get(key, -MIN_UPDATE_S) < MIN_UPDATE_S:
            return None
        notices = self.notices(icao, geo)
        closed = closed_ends(geo, notices)
        out = outages(notices)
        end = self._runway(geo, weather, old.runway if old else None, closed, out)
        if end is None:
            return None
        region = self.region or region_for(icao)
        if old is not None and old.runway != end.ident:
            self._runway_changed[key] = t
        landing = [end]
        simultaneous = False
        if end.has_ils and end.ident not in out.localizer:
            wide = [(o, d) for o, d in parallels(geo, end, closed) if o.has_ils and o.ident not in out.localizer
                    and d >= SIMULTANEOUS_M and o.runway.runway.length_m >= 0.6 * end.runway.runway.length_m]
            if wide:
                landing.append(min(wide, key=lambda od: od[1])[0])
                simultaneous = True
        approaches = {e.ident: choose_approach(
            geo.airport, e.ident, has_ils=e.has_ils, visibility_sm=weather.visibility_sm, ceiling_ft=weather.ceiling_ft,
            in_cloud=weather.in_cloud, aircraft_type=TYPICAL.type, airline=TYPICAL.airline,
            visual_first=not region.icao, precip=weather.precip, outages=out) for e in landing}
        extra = advisories(weather, end.ident)
        taxiways = frozenset(n.subject for n in notices if n.kind == "taxiway_closed")
        ops = Operations(
            landing=tuple(e.ident for e in landing), departing=(end.ident,), approaches=approaches,
            simultaneous=simultaneous and all(a.kind != "VISUAL" for a in approaches.values()), notices=notices,
            outages=out, closed_runways=closed, closed_taxiways=taxiways,
            runway_change=t - self._runway_changed.get(key, -math.inf) < RUNWAY_CHANGE_S, **extra)
        notes = remarks(geo, end, weather)
        slot = observation_slot(zulu_s)
        new_observation = old is not None and slot != old.observation
        if old is not None and not new_observation and not _changed(old, weather, end.ident, notes) \
                and old.operations is not None and old.operations.key() == ops.key():
            return None
        if old is None:
            which = "" if kind == "both" else kind  # separate arrival and departure broadcasts run their own letters
            letter = LETTERS[zlib.crc32(f"{icao}{which}{self.seed}{int((zulu_s or 0) // 3600)}".encode()) % 26]
        else:
            letter = LETTERS[(LETTERS.index(old.letter) + 1) % 26]
        now = zulu_s if zulu_s is not None else weather.t
        routine = old is None or new_observation
        stamp = _zulu(((slot * 3600) + OBSERVED_AT_MIN * 60) if routine and zulu_s is not None else now)
        primary = approaches[end.ident]
        info = AtisInfo(icao, name, letter, stamp, weather, end.ident, primary.kind, notes, ops, region, kind, slot)
        self.current[key] = info
        self._issued[key] = t
        return info

    @staticmethod
    def _runway(geo: AirportGeometry, weather: Weather, current: str | None, closed: frozenset[str],
                out=None) -> RunwayEndGeometry | None:
        open_ends = [e for e in geo.ends if e.ident not in closed]
        if not open_ends:
            return None
        if current is not None and current not in closed and (end := geo.end(current)) is not None:
            headwind, _ = components(end, weather)
            if headwind >= -MAX_TAILWIND_KT:
                return end  # still fine: keep the runway in use
        best = select_runway(geo, weather.wind_dir_true, weather.wind.speed_kt, exclude=closed)
        instrument = (weather.visibility_sm is not None and weather.visibility_sm < 3) or \
            (weather.ceiling_ft is not None and weather.ceiling_ft < 1000) or weather.in_cloud
        if best is not None and instrument and not options(geo.airport, best.ident, TYPICAL, out) and geo.airport.approaches:
            # Too poor for a visual, and no approach to the runway the wind favours: one that has one, if the
            # tailwind on it is acceptable (else the approach to another runway and a circle, choose_approach).
            fitting = [e for e in open_ends if options(geo.airport, e.ident, TYPICAL, out)
                       and components(e, weather)[0] >= -MAX_TAILWIND_KT]
            if fitting:
                return max(fitting, key=lambda e: components(e, weather)[0])
        return best


def _changed(old: AtisInfo, new: Weather, runway: str, notes: tuple[str, ...]) -> bool:
    before = old.weather
    if runway != old.runway or notes != old.remarks or new.precip != before.precip:
        return True
    if before.altimeter_inhg is not None and new.altimeter_inhg is not None and abs(before.altimeter_inhg - new.altimeter_inhg) >= 0.02:
        return True
    if abs(new.wind.speed_kt - before.wind.speed_kt) >= 7:
        return True
    turn = abs((new.wind.direction_mag - before.wind.direction_mag + 180) % 360 - 180)
    if turn >= 40 and max(new.wind.speed_kt, before.wind.speed_kt) >= 6:
        return True
    ceilings = [w.ceiling_ft for w in (before, new)]
    if (ceilings[0] is None) != (ceilings[1] is None) or (None not in ceilings and
                                                         (ceilings[0] < 1000) != (ceilings[1] < 1000)):
        return True
    vis = [w.visibility_sm for w in (before, new)]
    return None not in vis and (vis[0] < 3) != (vis[1] < 3)


def _zulu(zulu_s: float) -> str:
    minutes = int(zulu_s // 60) % 1440
    return f"{minutes // 60:02d}{minutes % 60:02d}"
