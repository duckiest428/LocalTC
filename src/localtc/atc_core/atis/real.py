"""A real airport's ATIS (the FAA's digital ATIS, as published), read for what LocalTC's own ATIS needs: its letter,
the time, the runways in use for landing and departing, the approach, and the weather in it.

    "LAS ATIS INFO W 1756Z. 00000KT 10SM SCT120 BKN180 29/03 A2987 (TWO NINER EIGHT SEVEN). VISUAL APPROACHES IN USE.
     LANDING RWYS 26L AND 19R. DEPG RWYS 26R, 19R AND 19L. ..."

LocalTC then speaks it in its own ATIS's words (``broadcast``), with the real letter and runways: the published text
is all abbreviations ("DEPG RWYS", "SIMUL CLSLY SPCD DPNDNT ILS RY 28R"), made for reading, not for a voice.

Outside the US it's a VATSIM controller's ATIS (``source`` "vatsim"), in the words of Europe's and Asia's ATIS: "RWY IN
USE FOR ARRIVALS 24 LEFT", "DEP AND ARR RWY 10", "ARRIVAL RUNWAY 23R", "RWY 27R 27L". The network's own notes ("submit
your TOBT at vats.im") aren't the airport's and aren't read out.
"""

import re
from dataclasses import dataclass

from localtc.atc_core.atis.observation import Layer, Weather
from localtc.atc_core.atis.operations import read_notices
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
    approach: str = ""  # ILS, RNAV, VISUAL ... when it names one (the first: ``approaches`` has them by runway)
    # The approach it gives each runway ("ILS RWY 27C APCH, VISUAL APCH RWY 27R", "EXPECT VISUAL APCH RWY 16L"), and
    # the ones it offers for the rest ("EXPC ILS, RNAV, OR VISUAL APCH ... RWY 7, RWY 17R"), in its order.
    approaches: dict[str, str] | None = None
    offered: tuple[str, ...] = ()
    weather: Weather | None = None  # from the observation in it (wind magnetic as ATIS gives it)
    text: str = ""
    # Everything else it says, sentence by sentence as published ("TWY T CLSD", "INCR BIRD STRIKE HAZ ALL RWYS", "READ
    # BACK ALL CONTROL INSTRUCTIONS"): LocalTC's ATIS reads them out in words, as the recording does.
    remarks: tuple[str, ...] = ()
    notices: tuple[tuple[str, str, str], ...] = ()  # what ATC works to in them (``operations.read_notices``)
    source: str = ""  # "": the FAA's digital ATIS; "vatsim": a VATSIM controller's


def parse(text: str, kind: str = "both", *, source: str = "", zulu: str = "") -> RealAtis | None:
    """``text`` as published; None when it has no letter. ``zulu``: its time when the text has none."""
    upper = " ".join(text.upper().split())
    # "24 LEFT" (a voice's words typed out): 24L
    upper = re.sub(r"\b(\d{1,2}) (LEFT|RIGHT|CENTER|CENTRE)\b", lambda m: m.group(1) + m.group(2)[0], upper)
    # "INFO K 0553Z", "ATIS INFORMATION J. 2150Z", "ATIS EDDV B"
    m = re.search(r"\b(?:INFO(?:RMATION)?|ATIS)\s+(?:[A-Z]{4}\s+)?([A-Z])\b(?:\.?\s+(\d{4})Z?)?", upper)
    if m is None:
        return None
    letter, zulu = m.group(1), m.group(2) or zulu
    # "LNDG/DEPG RWYS 4/8" (Honolulu), "LDG AND DEPG RWY 27", "DEP AND ARR RWY 10": the same runways both ways
    both_ways = _runways_after(upper, r"(?:LANDING|LDG|LNDG|ARR)\s*(?:/|AND|&)\s*(?:DEPARTING|DEPG|DEP)") \
        or _runways_after(upper, r"(?:DEPARTING|DEPG|DEP)\s*(?:/|AND|&)\s*(?:LANDING|LDG|LNDG|ARR)")
    landing = both_ways or _runways_after(upper, r"(?:LANDING|LDG|LNDG|ARRIVALS?|ARR)")
    departing = both_ways or _runways_after(upper, r"(?:DEPARTING|DEPG|DEPARTURES?|DEP|DEPS)(?:\s+(?:EXPC?|EXPECT))?")  # "DEPS EXP RWYS 22L 28R"
    approach = ""
    approaches = re.findall(r"\b(ILS|RNAV|GPS|LOC|VOR|VISUAL)\b(?:\s+(?:Y|Z)\b)?(?:\s+(?:APCHS?|APPROACHES|APPROACH|APP))?"
                            rf"(?:\s+(?:TO\s+)?{RUNWAY}\s*(\d{{1,2}}[LRC]?))?", upper)
    by_runway: dict[str, str] = {}
    offered: list[str] = []
    for kind_word, runway in approaches:
        kind_name = "RNAV" if kind_word == "GPS" else kind_word
        approach = approach or kind_name
        if runway:
            by_runway[_ident(runway)] = kind_name  # (a later "EXPECT VISUAL APCH RWY 16L" is the one for 16L)
        elif kind_name not in offered:
            offered.append(kind_name)
    named = not landing  # no "LANDING RWYS ...": the approaches' runways are the ones landed on
    in_use = _runways_after(upper, r"(?:IN USE|USING|ACTIVE)")
    if not landing and not departing:
        landing = departing = in_use or _first_runways(upper)
    if named:  # "EXPC ILS, RNAV, OR VISUAL APCH, SIMUL APCHS IN USE, RWY 7, RWY 17R, EXPECT VISUAL APCH RWY 16L"
        listed = _runways_after(upper, r"(?:APCHS?|APPROACH(?:ES)?)(?:\s*,?\s*SIMUL\s+APCHS\s+IN\s+USE)?") if offered else ()
        # (Only when it names them: Gatwick's "RWY IN USE 26L. ILS APPROACH TO BE EXPECTED" lands on 26L.)
        landing = tuple(dict.fromkeys((*listed, *by_runway))) or landing or in_use
    departing = departing or (in_use if kind != "arrival" else ())
    found = _remarks(upper)
    notices = tuple(dict.fromkeys(n for r in found for n in read_notices(r)))
    return RealAtis(letter=letter, zulu=zulu, kind=kind, landing=landing, departing=departing, approach=approach,
                    approaches=by_runway, offered=tuple(offered), weather=_weather(upper), text=" ".join(text.split()),
                    remarks=found, notices=notices, source=source)


def _first_runways(upper: str) -> tuple[str, ...]:
    """Where it only lists them, "ATIS EDDV B METAR 252150 RWY 27R 27L DEPENDENT PARALLEL APPROACHES": the first runways
    named (not in a notice: "RWY 16C CLSD")."""
    for m in re.finditer(rf"\b{RUNWAY}\s*\d{{1,2}}[LRC]?\b", upper):
        rest = upper[m.start():m.start() + 40]
        if not re.search(r"\b(?:CLSD|CLOSED|U/S|WIP|SHORTENED)\b", rest):
            return _runways_after(upper[m.start():], RUNWAY)
    return ()


# What starts a sentence LocalTC's ATIS already says its own way: the runways and approaches in use
OWN_WORDS = re.compile(r"^(?:SIMUL|EXP|EXPC|EXPECT|LNDG|LDG|LANDING|DEPG|DEPARTING|DEPS?|ARR(?:IVALS?)?|ILS|RNAV|GPS|"
                       r"VISUAL|VIS|APCHS?|RWYS? IN USE|RY|INST|"
                       # ... and the weather, the transition level and the runways in Europe's words (the observation
                       # spelled out, "WIND 330/08KT. VISIBILITY MORE THAN 10 KILOMETERS. QNH 1023")
                       r"(?:SURFACE )?WIND|VISIBILITY|CAVOK|CLOUDS?|TEMPERATURE|TEMP|DEW ?POINT|QNH|QFE|METAR|AUTO|TREND|"
                       r"NOSIG|TEMPO|BECMG|TRANSITION|TRL|TL|"
                       # (a runway's notice is the airport's news: "RWY 16C CLSD" is read out)
                       r"(?:RWY|RUNWAY)S?(?!.*\b(?:CLSD|CLOSED|U/S|WIP|SHORTENED|AVBL|AVAILABLE|WET|CONTAMINATED)\b)|ARRIVAL|DEPARTURE|SINGLE "
                       r"RUNWAY|DEPENDENT|INDEPENDENT|PARALLEL)\b")
# The network's notes on a VATSIM controller's ATIS: not the airport's ("submit their TOBT at vats.im/vdgs")
NETWORK = re.compile(r"VATS|DISCORD|HTTPS?\b|WWW\.|\.(?:COM|NET|ORG|IM)\b|\bTOBT\b|\bVDGS\b|\bCPDLC\b|\bHOPPIE\b|"
                     r"\bDATALINK\b|\bPDC\b|\bCHARTS?\b|\bCONTROLLER\b|\bONLINE\b")


def _remarks(upper: str) -> tuple[str, ...]:
    """The sentences after the observation, the runways and the approaches: notices, cautions, the readback line
    ("...ADVS YOU HAVE INFO K" left out: LocalTC closes with its own)."""
    out = []
    for sentence in re.split(r"(?<=[A-Z0-9)])\.\s+|\.\.\.+\s*", upper):
        s = sentence.strip(" .")
        if not s or re.fullmatch(r"\d{4}Z", s) or re.search(r"\b(?:ATIS|INFO(?:RMATION)?)\s+(?:[A-Z]{4}\s+)?[A-Z]\b", s) or re.search(r"\b\d{5}(?:G\d{2})?KT\b", s) \
                or re.search(r"\bA\d{4}\b|\bQ\d{4}\b|\bRMK\b", s) or OWN_WORDS.match(s) or NETWORK.search(s):
            continue
        out.append(s)
    return tuple(out)


def approach_for(real: RealAtis, runway: str, visual_weather: bool) -> str:
    """The approach the real ATIS gives ``runway``: the one it names for it, else of the ones it offers the visual in
    visual weather (what an ATIS's "ILS, RNAV, or visual" comes to in good weather), else the first; "" for none."""
    if real.approaches and runway in real.approaches:
        return real.approaches[runway]
    if not real.offered:
        return ""
    if visual_weather and "VISUAL" in real.offered:
        return "VISUAL"
    return next((k for k in real.offered if k != "VISUAL"), real.offered[0])


def age_minutes(zulu: str, now_zulu_s: float | None) -> int | None:
    """How old an ATIS stamped ``zulu`` ("2253") is at ``now_zulu_s`` (seconds into the UTC day), or None."""
    if not zulu.isdigit() or len(zulu) != 4 or now_zulu_s is None:
        return None
    return int((now_zulu_s // 60 - (int(zulu[:2]) * 60 + int(zulu[2:]))) % 1440)


def _runways_after(upper: str, lead: str) -> tuple[str, ...]:
    """The runways named after ``lead``: "LANDING RWYS 26L AND 19R" -> ("26L", "19R")."""
    found: list[str] = []
    # "26L", "RWY 16L", "RWY8"; and how it's used: "1R FULL LENGTH AND 1L AT BRAVO" (the second was lost)
    item = rf"(?:{RUNWAY}\s*)?\d{{1,2}}[LRC]?\b(?:\s+(?:FULL\s+LENGTH|(?:AT|FROM|INTXN|INT)\s+[A-Z][A-Z0-9]*))?"
    # (Two runways with only a space between them, "DEPS EXP RWYS 22L 28R": the next a full ident, so "28R FROM
    # NOVEMBER 5" isn't a runway 05.)
    sep = rf"(?:\s*(?:,|AND|OR|/|&)\s*|\s+(?=(?:{RUNWAY}\s*)?\d{{2}}[LRC]?\b(?!\s*(?:FT|THSD))))"
    for m in re.finditer(rf"\b{lead}\b[\s,]*(?:AND\s+)?((?:{item}(?:{sep})?)+)", upper):
        found += [_ident(r) for r in re.findall(r"\d{1,2}[LRC]?(?![0-9])", m.group(1))]
    return tuple(dict.fromkeys(r for r in found if 1 <= int(r[:2]) <= 36))


def _ident(runway: str) -> str:
    """"1L" -> "01L", "26" -> "26"."""
    m = re.fullmatch(r"(\d{1,2})([LRC]?)", runway)
    return f"{int(m.group(1)):02d}{m.group(2)}" if m else runway


def _weather(upper: str) -> Weather | None:
    """The observation in it: "09005G15KT 10SM FEW006 BKN020 18/13 A2981" (or Q1013). Its remarks aren't the weather
    here: Phoenix's "RMK AO2 SLP111 SHRA DSNT N" (showers in the distance) was read as rain on the field."""
    upper = re.split(r"\bRMK\b", upper)[0]
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
