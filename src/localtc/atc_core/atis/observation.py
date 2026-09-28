"""An airport's weather, observed from the sim: what an ATIS (and a METAR) is built from.

MSFS doesn't hand add-ons its METARs, only the weather where the aircraft is: the wind, the visibility, the
temperature, the pressure, precipitation (rain or snow, and how hard), whether the aircraft is in cloud or in
smoke. So an airport's surface weather is sampled while the aircraft is on or near it, and the rest is
worked out:

- **Gusts and a variable wind**: from the samples over the last two minutes (the METAR's mean) and ten.
- **Clouds**: from where the aircraft went into and came out of cloud while climbing or descending within
  ``OBSERVE_NM`` of the airport. Each 500 ft band's share of time in cloud gives its cover (few, scattered,
  broken, overcast). Climbing out of a clear airport says it's clear up to the height reached; cloud on the
  ground is an obscured sky with a vertical visibility. Nothing observed: the sky isn't known, and the ATIS
  leaves it out (the FAA's ATIS may, with the ceiling above 5,000 ft and more than 5 miles).
- **Present weather**: rain and snow by rate (light, moderate, heavy), freezing when it's below zero, a
  thunderstorm for a downpour with strong gusts, fog, mist, haze or smoke by the visibility.
- **Dew point**: the sim has no humidity. It's estimated from what the visibility and precipitation say
  about the air (fog is saturated, a clear day is dry), so the ATIS reads naturally; it's never used to decide
  anything.
- **RVR**: from the visibility, when it's low.
- **Pressure trend**: from the altimeter over the last hour ("pressure falling rapidly").
"""

import math
from collections import deque
from dataclasses import dataclass, field, replace

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.values import Wind
from localtc.sim_api import OwnshipState

M_PER_SM = 1609.34
FT_PER_M = 3.28084
SAMPLE_NM = 8.0  # sample an airport's surface weather within this distance...
SAMPLE_AGL_FT = 1500.0  # ...and below this height
REGION_NM = 150.0  # another airport's sample stands in for one this close
GUST_WINDOW_S = 120.0
VARIABLE_WINDOW_S = 600.0
OBSERVE_NM = 30.0  # clouds seen this close to an airport are its clouds
OBSERVE_TOP_FT = 12000  # ... up to this far above it
OBSERVE_KEEP_S = 5400.0  # and for this long
BAND_FT = 500
TREND_WINDOW_S = 3600.0
RAPID_INHG_PER_HR = 0.06  # "pressure falling rapidly": 0.06 inHg (2 hPa) in an hour or more


@dataclass(frozen=True)
class Layer:
    cover: str  # FEW, SCT, BKN, OVC, or VV (sky obscured: vertical visibility)
    base_ft: int  # above the airport

    @property
    def ceiling(self) -> bool:
        return self.cover in ("BKN", "OVC", "VV")


@dataclass(frozen=True)
class Phenomenon:
    """One present-weather group: intensity (-, "", +), descriptor (FZ, TS, SH), and the weather (RA, SN, FG, BR,
    HZ, FU)."""

    kind: str
    intensity: str = ""
    descriptor: str = ""

    @property
    def code(self) -> str:
        return f"{self.intensity}{self.descriptor}{self.kind}"


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
    # Since 0.4: what an ATIS needs beyond the basics (None: not known).
    variable_from: int | None = None  # a wind varying 60 degrees or more: between these (magnetic)
    variable_to: int | None = None
    precip_rate_mm: float | None = None  # mm/h
    in_cloud: bool = False  # the aircraft in cloud on the ground: the sky obscured
    smoke: bool = False
    sky: tuple[Layer, ...] | None = None  # None: not observed; (): clear as far as seen
    clear_below_ft: int | None = None  # no cloud seen up to this height
    dewpoint_c: int | None = None  # estimated (see the module's notes)
    pressure_trend: str = ""  # "", "rising rapidly", "falling rapidly"
    elevation_ft: float = 0.0

    @property
    def ceiling_ft(self) -> int | None:
        """The lowest broken or overcast layer (or the vertical visibility); None: none, or not known."""
        return next((layer.base_ft for layer in self.sky or () if layer.ceiling), None)

    @property
    def calm(self) -> bool:
        return self.wind.speed_kt < 3

    @property
    def rvr_ft(self) -> int | None:
        """Runway visual range, reported with a visibility of a mile or less: from the visibility, the runway lights
        seeing farther than the eye (about 1.5 times, by day), to the reporting steps, 6,000 ft at most."""
        if self.visibility_sm is None or self.visibility_sm > 1.0:
            return None
        feet = self.visibility_sm * M_PER_SM * FT_PER_M * 1.5
        step = 100 if feet < 800 else 200 if feet < 3000 else 500
        return int(min(6000, max(100, round(feet / step) * step)))

    @property
    def phenomena(self) -> tuple[Phenomenon, ...]:
        found: list[Phenomenon] = []
        freezing = self.temperature_c is not None and self.temperature_c <= 0
        rate = self.precip_rate_mm
        if self.precip:
            kind = "SN" if self.precip == "snow" else "RA"
            heavy, light = (7.6, 2.5) if kind == "RA" else (5.0, 1.0)
            intensity = "" if rate is None else "+" if rate > heavy else "-" if rate < light else ""
            if kind == "RA" and rate is not None and rate >= 16 and (self.gust_kt or 0) >= 20:
                found.append(Phenomenon(kind, "+" if rate > heavy else "", "TS"))  # a downpour with strong gusts
            else:
                found.append(Phenomenon(kind, intensity, "FZ" if kind == "RA" and freezing else ""))
        vis = self.visibility_sm
        if vis is not None and vis < 7:
            if self.smoke:
                found.append(Phenomenon("FU"))
            elif vis < 0.625:
                found.append(Phenomenon("FG", "", "FZ" if freezing else ""))
            elif not self.precip and (self.in_cloud or vis < 3 or (self.temperature_c is not None and self.temperature_c < 20)):
                found.append(Phenomenon("BR"))
            elif not self.precip:
                found.append(Phenomenon("HZ"))
        return tuple(found)


def estimated_dewpoint(temperature_c: float | None, visibility_sm: float | None, precip: str, in_cloud: bool) -> int | None:
    """A dew point that fits the weather: saturated in fog or cloud, close in rain or snow, dry on a clear day."""
    if temperature_c is None:
        return None
    if in_cloud or (visibility_sm is not None and visibility_sm < 0.625):
        spread = 0.0
    elif precip:
        spread = 1.0 if precip == "snow" else 2.0
    elif visibility_sm is None:
        spread = 8.0
    else:
        spread = min(15.0, max(1.0, (visibility_sm - 1) * 1.3))
    return int(round(temperature_c - spread))


def magnetic_wind(own: OwnshipState) -> Wind:
    magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180  # east positive; sim MAGVAR sign conventions vary
    return Wind(direction_mag=int(round((own.wind_dir_true - magvar) / 10) * 10) % 360 or 360,
                speed_kt=int(round(own.wind_kt)))


def _precip(bits: int) -> str:
    return "snow" if bits & 8 else ("rain" if bits & 4 else "")


@dataclass
class CloudProfile:
    """What the aircraft has seen of the clouds over one airport: per 500 ft band above it, samples in and out
    of cloud (time-stamped, the last ``OBSERVE_KEEP_S`` kept)."""

    samples: deque = field(default_factory=deque)  # (t, band, in_cloud)

    def add(self, t: float, height_ft: float, in_cloud: bool) -> None:
        if not 0 <= height_ft <= OBSERVE_TOP_FT:
            return
        self.samples.append((t, int(height_ft // BAND_FT), in_cloud))
        while self.samples and t - self.samples[0][0] > OBSERVE_KEEP_S:
            self.samples.popleft()

    def layers(self) -> tuple[tuple[Layer, ...] | None, int | None]:
        """(the layers seen, lowest first; None if too little was seen), and how high it's known to be clear."""
        seen: dict[int, list[bool]] = {}
        for _, band, cloud in self.samples:
            seen.setdefault(band, []).append(cloud)
        if not seen:
            return None, None
        bands = sorted(seen)
        if bands[0] * BAND_FT > 1500:
            return None, None  # never down near the airport: nothing known about the low clouds
        layers: list[Layer] = []
        clear_below: int | None = None
        run: list[float] = []
        run_base = bands[0]
        for expected, band in enumerate(bands, start=bands[0]):
            if band != expected:
                break  # a band never flown through: what's above it isn't known
            share = sum(seen[band]) / len(seen[band])
            if share >= 0.05:
                if not run:
                    run_base = band
                run.append(share)
                continue
            if run:
                layers.append(_layer(run, run_base))
                run = []
            if not layers:
                clear_below = (band + 1) * BAND_FT
        if run:
            layers.append(_layer(run, run_base))
        return tuple(_merge(layers)), clear_below


def _layer(shares: list[float], base_band: int) -> Layer:
    cover = max(shares)
    kind = "OVC" if cover >= 0.9 else "BKN" if cover >= 0.5 else "SCT" if cover >= 0.25 else "FEW"
    return Layer(kind, _round_height(base_band * BAND_FT))


def _round_height(feet: float) -> int:
    """Cloud heights as reported: to 100 ft up to 5,000, 500 up to 10,000, 1,000 above."""
    step = 100 if feet <= 5000 else 500 if feet <= 10000 else 1000
    return int(round(feet / step) * step)


def _merge(layers: list[Layer]) -> list[Layer]:
    """At most one layer per cover (lowest first): FEW 2500, BKN 4000, OVC 9000."""
    out: list[Layer] = []
    for layer in layers:
        if out and out[-1].cover == layer.cover:
            continue
        out.append(layer)
    return out[:4]


@dataclass
class WeatherTracker:
    """Surface samples per airport, clouds seen near each, and the latest pressure (good anywhere in the region)."""

    samples: dict[str, Weather] = field(default_factory=dict)
    altimeter_inhg: float | None = None  # the pressure where the aircraft is now
    _winds: dict[str, deque] = field(default_factory=dict)
    _measured: dict[str, float] = field(default_factory=dict)  # each airport's altimeter, taken there
    _estimated: dict[str, float] = field(default_factory=dict)  # ... or first guessed from afar, and kept
    _clouds: dict[str, CloudProfile] = field(default_factory=dict)
    _pressures: dict[str, deque] = field(default_factory=dict)  # (t, inHg) at the airport, for the trend

    def altimeter_at(self, icao: str | None) -> float | None:
        """An airport's altimeter: measured on or near it, else the pressure where the aircraft was when it was first
        asked for, kept until it's measured. The pressure where the aircraft happens to be drifts as it flies: taken
        afresh each time, Seattle's altimeter went 29.90, 29.95, 29.96, 30.00 on the way in."""
        if not icao:
            return self.altimeter_inhg
        if icao in self._measured:
            return self._measured[icao]
        if icao not in self._estimated and self.altimeter_inhg is not None:
            self._estimated[icao] = self.altimeter_inhg
        return self._estimated.get(icao)

    def update(self, own: OwnshipState, airports: dict[str, AirportGeometry]) -> None:
        if 25.0 < own.altimeter_setting_inhg < 33.0:
            self.altimeter_inhg = round(own.altimeter_setting_inhg, 2)
        for icao, geo in airports.items():
            if not geo.airport.runways:
                continue
            distance = geo.distance_nm(own.lat, own.lon)
            height = own.alt_msl_ft - geo.airport.elev_ft
            if distance <= OBSERVE_NM and not own.on_ground:
                self._clouds.setdefault(icao, CloudProfile()).add(own.t, height, own.in_cloud)
            near = distance <= SAMPLE_NM
            if not (near and (own.on_ground or own.alt_agl_ft <= SAMPLE_AGL_FT)):
                continue
            if self.altimeter_inhg is not None:
                self._measured[icao] = self.altimeter_inhg
                trend = self._pressures.setdefault(icao, deque())
                trend.append((own.t, self.altimeter_inhg))
                while trend and own.t - trend[0][0] > TREND_WINDOW_S:
                    trend.popleft()
            winds = self._winds.setdefault(icao, deque())
            winds.append((own.t, own.wind_kt, own.wind_dir_true))
            while winds and own.t - winds[0][0] > VARIABLE_WINDOW_S:
                winds.popleft()
            recent = [(kt, d) for t, kt, d in winds if own.t - t <= GUST_WINDOW_S]
            speeds = [kt for kt, _ in recent]
            mean = sum(speeds) / len(speeds)
            gust = max(speeds) if len(speeds) >= 10 and max(speeds) - mean >= 8 and max(speeds) >= 15 else None
            wind = magnetic_wind(own)
            variable = _variability([d for _, _, d in winds], own) if len(winds) >= 10 else None
            visibility = round(own.visibility_m / M_PER_SM, 1) if own.visibility_m is not None else None
            temperature = int(round(own.temperature_c)) if own.temperature_c is not None else None
            # Cloud on the ground: fog thick enough to hide the sky (the sim says "in cloud" in thin patches too).
            surface_cloud = own.in_cloud and (own.on_ground or own.alt_agl_ft < 200) and visibility is not None \
                and visibility < 1.0
            precip = _precip(own.precip)
            self.samples[icao] = Weather(
                wind=wind, gust_kt=int(round(gust)) if gust else None, visibility_sm=visibility, temperature_c=temperature,
                altimeter_inhg=self._measured.get(icao), precip=precip, t=own.t, wind_dir_true=own.wind_dir_true,
                variable_from=variable[0] if variable else None, variable_to=variable[1] if variable else None,
                precip_rate_mm=getattr(own, "precip_rate_mm", None), in_cloud=surface_cloud,
                smoke=bool(getattr(own, "in_smoke", False)),
                dewpoint_c=estimated_dewpoint(own.temperature_c, visibility, precip, surface_cloud),
                elevation_ft=geo.airport.elev_ft,
            )

    def surface(self, icao: str, airports: dict[str, AirportGeometry]) -> Weather | None:
        """The airport's own sample, or the freshest one from an airport in the same region; with the clouds seen
        near it, its altimeter and the pressure's trend."""
        own = self.samples.get(icao)
        geo = airports.get(icao)
        if own is None:
            nearby = [w for other, w in self.samples.items() if other != icao and geo is not None and other in airports
                      and airports[other].distance_nm(geo.airport.lat, geo.airport.lon) <= REGION_NM]
            own = max(nearby, key=lambda w: w.t, default=None)
        if own is None:
            return None
        sky, clear_below = self.clouds(icao)
        if own.in_cloud:
            vv = own.visibility_sm * M_PER_SM * FT_PER_M * 0.25 if own.visibility_sm is not None else 100
            sky = (Layer("VV", max(100, min(500, _round_height(vv)))),)  # cloud on the ground: sky obscured
        return replace(own, altimeter_inhg=self.altimeter_at(icao) or own.altimeter_inhg, sky=sky, clear_below_ft=clear_below,
                       pressure_trend=self.trend(icao), elevation_ft=geo.airport.elev_ft if geo is not None else own.elevation_ft)

    def clouds(self, icao: str) -> tuple[tuple[Layer, ...] | None, int | None]:
        profile = self._clouds.get(icao)
        return profile.layers() if profile is not None else (None, None)

    def trend(self, icao: str) -> str:
        samples = self._pressures.get(icao)
        if not samples or samples[-1][0] - samples[0][0] < 1800:
            return ""  # under half an hour of it: no trend to speak of
        hours = (samples[-1][0] - samples[0][0]) / 3600
        rate = (samples[-1][1] - samples[0][1]) / hours
        return "rising rapidly" if rate >= RAPID_INHG_PER_HR else "falling rapidly" if rate <= -RAPID_INHG_PER_HR else ""


def _variability(directions_true: list[float], own: OwnshipState) -> tuple[int, int] | None:
    """Directions spread over 60 degrees or more in the last ten minutes: (from, to), magnetic, clockwise."""
    if not directions_true:
        return None
    mean = math.degrees(math.atan2(sum(math.sin(math.radians(d)) for d in directions_true),
                                   sum(math.cos(math.radians(d)) for d in directions_true)))
    offsets = [((d - mean + 540) % 360) - 180 for d in directions_true]
    low, high = min(offsets), max(offsets)
    if high - low < 60:
        return None
    magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180

    def mag(offset: float) -> int:
        return int(round(((mean + offset - magvar) % 360) / 10) * 10) % 360 or 360

    return mag(low), mag(high)
