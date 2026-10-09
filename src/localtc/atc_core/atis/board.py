"""Each airport's current ATIS, and when its letter advances.

A new ATIS (the next letter, A to Z and round again) is recorded (JO 7110.65 2-9-2):

- with every new official weather observation, hourly at 53 minutes past, whatever it says;
- when the weather changes enough to matter between them (a special): the wind by 10 kt or swinging 60 degrees,
  the visibility across 3 miles, the altimeter by 0.03, precipitation starting or stopping, a ceiling appearing;
- when the operations change: the runway in use, the approaches, the notices, the runway condition, low
  visibility procedures or wind shear.

Not more than once every ``MIN_UPDATE_S`` between the hourly ones though (a change of runway: ``RUNWAY_UPDATE_S``),
so the sim's ever-shifting weather doesn't spin the letters: pilots found a new letter every ten minutes nothing
like the real thing. The ATIS also fixes
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
from localtc.sim_api.geo import angle_diff

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
FLOW_DEG = 45.0  # a runway end this close to the traffic's heading is going the same way
MAX_TAILWIND_KT = 5.0  # the runway in use changes once the tailwind on it is stronger than this
CROSSWIND_CAUTION_KT = 12.0
MIN_UPDATE_S = 1800.0  # a special between the hourly observations at most this often (session time) ...
RUNWAY_UPDATE_S = 300.0  # ... or this often when the runway in use changes
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
        self.flows: dict[str, float] = {}  # airport -> the true heading its own traffic is using (the engine sets it)
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
        since = t - self._issued.get(key, -math.inf)
        if old is not None and since < RUNWAY_UPDATE_S:
            return None
        notices = self.notices(icao, geo)
        closed = closed_ends(geo, notices)
        out = outages(notices)
        end = self._runway(geo, weather, old.runway if old else None, closed, out, flow=self.flows.get(icao))
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
        instrument = {}
        for e in landing:
            if approaches[e.ident].kind == "VISUAL":
                published = choose_approach(
                    geo.airport, e.ident, has_ils=e.has_ils, visibility_sm=weather.visibility_sm,
                    ceiling_ft=weather.ceiling_ft, in_cloud=True, aircraft_type=TYPICAL.type, airline=TYPICAL.airline,
                    visual_first=False, precip=weather.precip, outages=out)  # what it has for when it isn't visual
                if published.kind != "VISUAL" and not published.circle_to:
                    instrument[e.ident] = published
        extra = advisories(weather, end.ident)
        taxiways = frozenset(n.subject for n in notices if n.kind == "taxiway_closed")
        ops = Operations(
            landing=tuple(e.ident for e in landing), departing=(end.ident,), approaches=approaches, instrument=instrument,
            simultaneous=simultaneous and all(a.kind != "VISUAL" for a in approaches.values()), notices=notices,
            outages=out, closed_runways=closed, closed_taxiways=taxiways,
            runway_change=t - self._runway_changed.get(key, -math.inf) < RUNWAY_CHANGE_S, **extra)
        notes = remarks(geo, end, weather)
        slot = observation_slot(zulu_s)
        new_observation = old is not None and slot != old.observation
        if old is not None and not new_observation and since < MIN_UPDATE_S and end.ident == old.runway:
            return None  # a special so soon after the last one: it waits (the runway changing doesn't)
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

    def _runway(self, geo: AirportGeometry, weather: Weather, current: str | None, closed: frozenset[str],
                out=None, *, flow: float | None = None) -> RunwayEndGeometry | None:
        """The runway in use. With ``flow`` (the way the sim's own traffic is taking off and landing, a true heading):
        a runway that way, as long as its tailwind is acceptable, so this flight isn't sent head-on into the stream
        of everybody else's arrivals and departures."""
        if flow is not None:
            against = frozenset(e.ident for e in geo.ends if angle_diff(e.heading_true, flow) > FLOW_DEG)
            with_flow = [e for e in geo.ends if e.ident not in closed | against
                         and components(e, weather)[0] >= -MAX_TAILWIND_KT]
            if with_flow:
                closed = closed | against  # the rest of the choice as below, among the runways that way
        open_ends = [e for e in geo.ends if e.ident not in closed]
        if not open_ends:
            return None
        best = select_runway(geo, weather.wind_dir_true, weather.wind.speed_kt, exclude=closed)
        if current is not None and current not in closed and (end := geo.end(current)) is not None:
            headwind, crosswind = components(end, weather)
            if headwind >= -MAX_TAILWIND_KT and not _better(best, end, weather):
                return end  # still fine: keep the runway in use
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


def _better(best: RunwayEndGeometry | None, current: RunwayEndGeometry, w: Weather) -> bool:
    """The wind has swung so the runway in use has a strong crosswind (``CROSSWIND_CAUTION_KT``) and another one
    is nearly into it: the runway changes. The wind went from 070 to 110 at 16 with runway 05 kept (14 kt across)
    and 13 a few degrees off the wind; a tailwind alone moved it."""
    if best is None or best.ident == current.ident:
        return False
    _, crosswind = components(current, w)
    best_head, best_cross = components(best, w)
    return crosswind >= CROSSWIND_CAUTION_KT and best_head > 0 and best_cross <= crosswind / 2


def _changed(old: AtisInfo, new: Weather, runway: str, notes: tuple[str, ...]) -> bool:
    before = old.weather
    if runway != old.runway or notes != old.remarks or new.precip != before.precip:
        return True
    if before.altimeter_inhg is not None and new.altimeter_inhg is not None and abs(before.altimeter_inhg - new.altimeter_inhg) >= 0.03:
        return True
    if abs(new.wind.speed_kt - before.wind.speed_kt) >= 10:
        return True
    turn = abs((new.wind.direction_mag - before.wind.direction_mag + 180) % 360 - 180)
    if turn >= 60 and max(new.wind.speed_kt, before.wind.speed_kt) >= 10:
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
