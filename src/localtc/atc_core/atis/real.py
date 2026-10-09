"""A real airport's ATIS (the FAA's digital ATIS, as published), read for what LocalTC's own ATIS needs: its letter,
the time, the runways in use for landing and departing, the approach, and the weather in it.

    "LAS ATIS INFO W 1756Z. 00000KT 10SM SCT120 BKN180 29/03 A2987 (TWO NINER EIGHT SEVEN). VISUAL APPROACHES IN USE.
     LANDING RWYS 26L AND 19R. DEPG RWYS 26R, 19R AND 19L. ..."

LocalTC then speaks it in its own ATIS's words (``broadcast``), with the real letter and runways: the published text
is all abbreviations ("DEPG RWYS", "SIMUL CLSLY SPCD DPNDNT ILS RY 28R"), made for reading, not for a voice.
"""

import re
from dataclasses import dataclass

from localtc.atc_core.atis.observation import Layer, Weather
from localtc.atc_core.values import Wind

COVERS = ("FEW", "SCT", "BKN", "OVC", "VV")
RUNWAY = r"(?:RWYS?|RYS?|RUNWAYS?)"


@dataclass(frozen=True)
class RealAtis:
    letter: str
    zulu: str  # "1756"
    kind: str  # both, arrival, departure
    landing: tuple[str, ...] = ()  # "26L", "19R"
    departing: tuple[str, ...] = ()
    approach: str = ""  # ILS, RNAV, VISUAL ... when it names one
    weather: Weather | None = None  # from the observation in it (wind magnetic as ATIS gives it)
    text: str = ""


def parse(text: str, kind: str = "both") -> RealAtis | None:
    """``text`` as published; None when it has no letter."""
    upper = " ".join(text.upper().split())
    m = re.search(r"\b(?:INFO(?:RMATION)?|ATIS)\s+([A-Z])\b(?:\s+(\d{4})Z?)?", upper)
    if m is None:
        return None
    letter, zulu = m.group(1), m.group(2) or ""
    landing = _runways_after(upper, r"(?:LANDING|LDG|LNDG|ARRIVALS?|ARR)")
    departing = _runways_after(upper, r"(?:DEPARTING|DEPG|DEPARTURES?|DEP|DEPS)")
    approach = ""
    approaches = re.findall(r"\b(ILS|RNAV|GPS|LOC|VOR|VISUAL)\b(?:\s+(?:APCHS?|APPROACHES|APPROACH))?(?:\s+(?:Y|Z)\b)?"
                            rf"(?:\s+{RUNWAY}\s+(\d{{1,2}}[LRC]?))?", upper)
    for kind_word, runway in approaches:
        approach = approach or ("RNAV" if kind_word == "GPS" else kind_word)
        if runway and not landing:
            landing = tuple(dict.fromkeys(_ident(r) for _, r in approaches if r))
    if not landing and not departing:
        both = _runways_after(upper, r"(?:IN USE|USING|ACTIVE)")
        landing = departing = both
    return RealAtis(letter=letter, zulu=zulu, kind=kind, landing=landing, departing=departing, approach=approach,
                    weather=_weather(upper), text=" ".join(text.split()))


def _runways_after(upper: str, lead: str) -> tuple[str, ...]:
    """The runways named after ``lead``: "LANDING RWYS 26L AND 19R" -> ("26L", "19R")."""
    found: list[str] = []
    item = rf"(?:{RUNWAY}\s*)?\d{{1,2}}[LRC]?\b"  # "26L", "RWY 16L", "RWY8"
    for m in re.finditer(rf"\b{lead}\b[\s,]*(?:AND\s+)?((?:{item}(?:\s*(?:,|AND|OR|/|&)\s*)?)+)", upper):
        found += [_ident(r) for r in re.findall(r"\d{1,2}[LRC]?(?![0-9])", m.group(1))]
    return tuple(dict.fromkeys(r for r in found if 1 <= int(r[:2]) <= 36))


def _ident(runway: str) -> str:
    """"1L" -> "01L", "26" -> "26"."""
    m = re.fullmatch(r"(\d{1,2})([LRC]?)", runway)
    return f"{int(m.group(1)):02d}{m.group(2)}" if m else runway


def _weather(upper: str) -> Weather | None:
    """The observation in it: "09005G15KT 10SM FEW006 BKN020 18/13 A2981" (or Q1013)."""
    wind = re.search(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b", upper)
    if wind is None:
        return None
    speed = int(wind.group(2))
    direction = int(wind.group(1)) if wind.group(1) != "VRB" else 0
    gust = int(wind.group(3)) if wind.group(3) else None
    vis = re.search(r"\b(?:(\d+)\s+)?(\d+)(?:/(\d+))?SM\b", upper)
    visibility = None
    if vis is not None:
        whole = int(vis.group(1) or 0)
        visibility = whole + (int(vis.group(2)) / int(vis.group(3)) if vis.group(3) else int(vis.group(2)))
    temps = re.search(r"\b(M?\d{2})/(M?\d{2})?\b", upper)
    temperature = _celsius(temps.group(1)) if temps else None
    dewpoint = _celsius(temps.group(2)) if temps and temps.group(2) else None
    altimeter = None
    if (a := re.search(r"\bA(\d{4})\b", upper)) is not None:
        altimeter = int(a.group(1)) / 100
    elif (q := re.search(r"\bQ(\d{3,4})\b", upper)) is not None:
        altimeter = round(int(q.group(1)) / 33.8639, 2)
    sky = tuple(Layer(c, int(h) * 100) for c, h in re.findall(r"\b(FEW|SCT|BKN|OVC|VV)(\d{3})\b", upper))
    clear = bool(re.search(r"\b(?:CLR|SKC|CAVOK)\b", upper))
    precip = "snow" if re.search(r"\b[-+]?(?:SN|SHSN)\b", upper) else "rain" if re.search(r"\b[-+]?(?:RA|SHRA|TSRA|DZ)\b", upper) else ""
    # The observation in a digital ATIS is the METAR's: its wind is true. The engine turns it magnetic by the airport's
    # variation (``with_magvar``).
    return Weather(wind=Wind(direction_mag=direction if speed else 0, speed_kt=speed), gust_kt=gust,
                   visibility_sm=visibility, temperature_c=temperature, altimeter_inhg=altimeter, precip=precip,
                   wind_dir_true=float(direction), dewpoint_c=dewpoint, sky=sky if sky or clear else None)


def _celsius(text: str) -> int:
    return -int(text[1:]) if text.startswith("M") else int(text)


def with_magvar(weather: Weather, magvar: float) -> Weather:
    """``weather`` (its wind true, from the observation) with the magnetic wind an ATIS gives. ``magvar`` east +."""
    from dataclasses import replace

    if not weather.wind.speed_kt:
        return weather
    mag = int(round((weather.wind_dir_true - magvar) / 10) * 10) % 360 or 360
    return replace(weather, wind=Wind(direction_mag=mag, speed_kt=weather.wind.speed_kt))
