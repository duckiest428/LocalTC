"""Surface weather at the flight's airports, and the ATIS built from it.

MSFS doesn't hand add-ons its ATIS or METARs, only the weather where the aircraft is. So an
airport's surface weather is sampled while the aircraft is on or near it (on the ground, or low
and close). Until the destination has been sampled, its ATIS uses the freshest surface sample
from another airport; the altimeter (sea level pressure) is always current. When the weather at
an airport changes enough, its ATIS letter advances, as a real one would.

The ATIS also fixes each airport's runway in use, so the runway doesn't flip with every gust:
it changes only when the tailwind on it gets too strong.
"""

import math
import zlib
from collections import deque
from dataclasses import dataclass, field

from localtc.atc_core.airport import AirportGeometry, select_runway
from localtc.atc_core.airport.geometry import RunwayEndGeometry
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Wind
from localtc.sim_api import OwnshipState

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
M_PER_SM = 1609.34
SAMPLE_NM = 8.0  # sample an airport's surface weather within this distance...
SAMPLE_AGL_FT = 1500.0  # ...and below this height
REGION_NM = 150.0  # another airport's sample stands in for one this close
GUST_WINDOW_S = 120.0
MAX_TAILWIND_KT = 5.0  # the runway in use changes once the tailwind on it is stronger than this
CROSSWIND_CAUTION_KT = 12.0
MIN_UPDATE_S = 600.0  # an ATIS changes at most this often (session time)


@dataclass(frozen=True)
class Weather:
    wind: Wind
    gust_kt: int | None = None
    visibility_sm: float | None = None
    temperature_c: int | None = None
    altimeter_inhg: float | None = None
    precip: str = ""  # "", "rain", "snow"
    t: float = 0.0  # when sampled (session time)
    wind_dir_true: float = 0.0


@dataclass
class AtisInfo:
    airport: str
    name: str
    letter: str
    zulu: str  # "1753"
    weather: Weather
    runway: str  # the runway end in use for landing and departing
    approach: str  # "ILS", "RNAV", "VISUAL"
    remarks: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        w = self.weather
        parts = [f"{self.name} information {speech.letter(self.letter).capitalize()}, {self.zulu} Zulu", wind_text(w)]
        if w.visibility_sm is not None:
            parts.append(f"Visibility {_visibility(w.visibility_sm)}" + (f", {w.precip}" if w.precip else ""))
        if w.temperature_c is not None:
            parts.append(f"Temperature {w.temperature_c}")
        if w.altimeter_inhg is not None:
            parts.append(f"Altimeter {w.altimeter_inhg:.2f}")
        parts.append(f"{self.approach} runway {self.runway} approach in use, landing and departing runway {self.runway}"
                     if self.approach != "VISUAL" else f"Visual approaches in use, landing and departing runway {self.runway}")
        if self.remarks:
            parts.append("Notice to airmen: " + ", ".join(self.remarks))
        parts.append(f"Advise on initial contact you have information {speech.letter(self.letter).capitalize()}")
        return ". ".join(parts) + "."

    @property
    def spoken(self) -> str:
        w = self.weather
        letter = speech.letter(self.letter)
        parts = [f"{self.name} information {letter}, {speech.digits(self.zulu)} zulu", "wind " + speech.wind(w.wind)
                 + (f", gusts {speech.digits(str(w.gust_kt))}" if w.gust_kt else "")]
        if w.visibility_sm is not None:
            parts.append(f"visibility {_visibility_spoken(w.visibility_sm)}" + (f", {w.precip}" if w.precip else ""))
        if w.temperature_c is not None:
            parts.append(f"temperature {'minus ' if w.temperature_c < 0 else ''}{speech.digits(str(abs(w.temperature_c)))}")
        if w.altimeter_inhg is not None:
            parts.append(f"altimeter {speech.altimeter(w.altimeter_inhg)}")
        rwy = speech.runway(self.runway)
        approach = {"ILS": "I L S", "RNAV": "R-NAV"}.get(self.approach, self.approach.lower())
        parts.append(f"{approach} runway {rwy} approach in use, landing and departing runway {rwy}"
                     if self.approach != "VISUAL" else f"visual approaches in use, landing and departing runway {rwy}")
        if self.remarks:
            parts.append("notice to airmen, " + ", ".join(self.remarks))
        parts.append(f"advise on initial contact you have information {letter}")
        return ". ".join(parts) + "."


def wind_text(w: Weather) -> str:
    if w.wind.speed_kt < 3:
        return "Wind calm"
    return f"Wind {w.wind.direction_mag:03d} at {w.wind.speed_kt}" + (f" gusts {w.gust_kt}" if w.gust_kt else "")


def _visibility(sm: float) -> str:
    return "10" if sm >= 10 else (f"{sm:.0f}" if sm >= 3 else f"{sm:.1f}".rstrip("0").rstrip("."))


def _visibility_spoken(sm: float) -> str:
    text = _visibility(sm)
    return speech.digits(text) if "." not in text else text.replace(".", " point ")


def magnetic_wind(own: OwnshipState) -> Wind:
    magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180  # east positive; sim MAGVAR sign conventions vary
    return Wind(direction_mag=int(round((own.wind_dir_true - magvar) / 10) * 10) % 360 or 360,
                speed_kt=int(round(own.wind_kt)))


def _precip(bits: int) -> str:
    return "snow" if bits & 8 else ("rain" if bits & 4 else "")


@dataclass
class WeatherTracker:
    """Surface samples per airport, and the latest pressure (good anywhere in the region)."""

    samples: dict[str, Weather] = field(default_factory=dict)
    altimeter_inhg: float | None = None
    _winds: dict[str, deque] = field(default_factory=dict)

    def update(self, own: OwnshipState, airports: dict[str, AirportGeometry]) -> None:
        if 25.0 < own.altimeter_setting_inhg < 33.0:
            self.altimeter_inhg = round(own.altimeter_setting_inhg, 2)
        for icao, geo in airports.items():
            if not geo.airport.runways:
                continue
            near = geo.distance_nm(own.lat, own.lon) <= SAMPLE_NM
            if not (near and (own.on_ground or own.alt_agl_ft <= SAMPLE_AGL_FT)):
                continue
            winds = self._winds.setdefault(icao, deque())
            winds.append((own.t, own.wind_kt))
            while winds and own.t - winds[0][0] > GUST_WINDOW_S:
                winds.popleft()
            speeds = [kt for _, kt in winds]
            mean = sum(speeds) / len(speeds)
            gust = max(speeds) if len(speeds) >= 10 and max(speeds) - mean >= 8 and max(speeds) >= 15 else None
            self.samples[icao] = Weather(
                wind=magnetic_wind(own), gust_kt=int(round(gust)) if gust else None,
                visibility_sm=round(own.visibility_m / M_PER_SM, 1) if own.visibility_m is not None else None,
                temperature_c=int(round(own.temperature_c)) if own.temperature_c is not None else None,
                altimeter_inhg=self.altimeter_inhg, precip=_precip(own.precip), t=own.t, wind_dir_true=own.wind_dir_true,
            )

    def surface(self, icao: str, airports: dict[str, AirportGeometry]) -> Weather | None:
        """The airport's own sample, or the freshest one from an airport in the same region."""
        own = self.samples.get(icao)
        if own is None:
            geo = airports.get(icao)
            nearby = [w for other, w in self.samples.items() if other != icao and geo is not None and other in airports
                      and airports[other].distance_nm(geo.airport.lat, geo.airport.lon) <= REGION_NM]
            own = max(nearby, key=lambda w: w.t, default=None)
        if own is None:
            return None
        return Weather(own.wind, own.gust_kt, own.visibility_sm, own.temperature_c, self.altimeter_inhg or own.altimeter_inhg,
                       own.precip, own.t, own.wind_dir_true)


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


class AtisBoard:
    """The current ATIS for each airport; the letter advances when the weather changes."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed
        self.current: dict[str, AtisInfo] = {}
        self._issued: dict[str, float] = {}

    def update(self, icao: str, geo: AirportGeometry, weather: Weather, zulu_s: float | None, name: str,
               t: float = 0.0) -> AtisInfo | None:
        """Refresh an airport's ATIS; returns it if it's new or its letter changed."""
        old = self.current.get(icao)
        if old is not None and t - self._issued.get(icao, -MIN_UPDATE_S) < MIN_UPDATE_S:
            return None
        end = self._runway(geo, weather, old.runway if old else None)
        if end is None:
            return None
        approach = "ILS" if end.has_ils else "RNAV"
        notes = remarks(geo, end, weather)
        if old is not None and not _changed(old, weather, end.ident, notes):
            return None
        if old is None:
            letter = LETTERS[zlib.crc32(f"{icao}{self.seed}{int((zulu_s or 0) // 3600)}".encode()) % 26]
        else:
            letter = LETTERS[(LETTERS.index(old.letter) + 1) % 26]
        info = AtisInfo(icao, name, letter, _zulu(zulu_s if zulu_s is not None else weather.t), weather, end.ident, approach,
                        notes)
        self.current[icao] = info
        self._issued[icao] = t
        return info

    @staticmethod
    def _runway(geo: AirportGeometry, weather: Weather, current: str | None) -> RunwayEndGeometry | None:
        if current is not None and (end := geo.end(current)) is not None:
            headwind, _ = components(end, weather)
            if headwind >= -MAX_TAILWIND_KT:
                return end  # still fine: keep the runway in use
        return select_runway(geo, weather.wind_dir_true, weather.wind.speed_kt)


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
    vis = [w.visibility_sm for w in (before, new)]
    return None not in vis and (vis[0] < 3) != (vis[1] < 3)


def _zulu(zulu_s: float) -> str:
    minutes = int(zulu_s // 60) % 1440
    return f"{minutes // 60:02d}{minutes % 60:02d}"
