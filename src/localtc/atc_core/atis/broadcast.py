"""The ATIS as it's read: FAA (JO 7110.65 2-9-3, the AIM 4-1-13) or ICAO (Annex 11 4.3.6, Doc 4444).

**FAA order**: the airport and the letter, the time of the weather; the weather (wind, visibility and RVR,
present weather, sky condition, temperature and dew point, altimeter, remarks such as density altitude or a
rapid pressure change); the approaches and the landing runways, the departure runway when it's another;
notices (closed runways and taxiways, navaids and lights out, bird activity); runway condition codes and
braking action; wind shear; other local information; "read back all runway hold short instructions"; and
"advise on initial contact you have information Delta". The sky and visibility may be left out with the
ceiling above 5,000 ft and more than 5 miles; LocalTC leaves the sky out when it hasn't seen it.

**ICAO order**: the aerodrome and the designator, the time; the approach to expect and the runways in use;
the runway surface condition; the transition level; other essential operational information; the wind
(with its variations), visibility and RVR, present weather, clouds below 5,000 ft (CAVOK when there's nothing
to report), temperature, dew point, QNH; wind shear; and the instruction to acknowledge it.

Numbers are said the way ATC says them: "wind two seven zero at one zero, gusts one eight", "altimeter two
niner niner two", "ceiling four thousand five hundred broken"; ICAO: "wind two seven zero degrees one zero
knots", "QNH one zero one three", "visibility one zero kilometres or more".

The loop is read with small variations (``variants``): what's said is the same, the words around it change a
little each time round, as a person recording it would.
"""

import math
from dataclasses import dataclass, field

from localtc.atc_core.airport.approaches import Outages
from localtc.atc_core.atis.observation import M_PER_SM, Layer, Phenomenon, Weather
from localtc.atc_core.atis.operations import Operations
from localtc.atc_core.phraseology import speech
from localtc.atc_core.region import Region, speaking
from localtc.atc_core.values import Approach

VARIANTS = 3


@dataclass
class AtisInfo:
    airport: str
    name: str
    letter: str
    zulu: str  # "1753": the observation's time
    weather: Weather
    runway: str  # the runway end LocalTC's own flight lands and departs on (the first in use)
    approach: str  # its approach kind: "ILS", "RNAV", "VISUAL" ...
    remarks: tuple[str, ...] = ()  # the "caution ..." notes ATC adds
    operations: Operations | None = None
    region: Region | None = None
    kind: str = "both"  # both, arrival, departure (airports with separate ATIS broadcasts)
    observation: int = 0  # the hourly observation it's from (a new one is a new letter)

    @property
    def icao_style(self) -> bool:
        return bool(self.region and self.region.icao)

    @property
    def outages(self) -> Outages:
        return self.operations.outages if self.operations is not None else Outages()

    def approach_for(self, runway: str) -> Approach | None:
        if self.operations is None:
            return None
        return self.operations.approaches.get(runway)

    def closed(self, runway: str) -> bool:
        return self.operations is not None and runway in self.operations.closed_runways

    @property
    def text(self) -> str:
        return compose(self, spoken=False)

    @property
    def spoken(self) -> str:
        return compose(self, spoken=True)

    @property
    def variants(self) -> tuple[str, ...]:
        """The spoken broadcast, read a little differently each time round the loop."""
        return tuple(compose(self, spoken=True, variant=v) for v in range(VARIANTS))


@dataclass
class _Say:
    """Sentences with a display and a spoken form."""

    spoken: bool
    parts: list[str] = field(default_factory=list)

    def add(self, shown: str, said: str | None = None) -> None:
        text = said if self.spoken and said is not None else shown
        if text:
            self.parts.append(text if self.spoken else text[0].upper() + text[1:])

    def text(self) -> str:
        return " ".join(p if p.endswith(".") else p + "." for p in self.parts)


def compose(info: AtisInfo, *, spoken: bool, variant: int = 0) -> str:
    region = info.region or Region("faa", 18000)
    with speaking(region):
        return (_icao if region.icao else _faa)(info, _Say(spoken), variant)


# --- FAA ------------------------------------------------------------------------------------------------------------


def _faa(info: AtisInfo, say: _Say, v: int) -> str:
    w, ops = info.weather, info.operations
    letter = speech.letter(info.letter).capitalize()
    kind = {"arrival": " arrival", "departure": " departure"}.get(info.kind, "")
    say.add(f"{info.name}{kind} information {letter}. {info.zulu} Zulu",
            f"{info.name}{kind} information {letter}, {speech.digits(info.zulu)} zulu")
    say.add(*_faa_wind(w))
    ceiling = w.ceiling_ft
    omit = w.visibility_sm is not None and w.visibility_sm > 5 and (ceiling is None or ceiling > 5000) and w.sky is None
    weather = ", ".join(_phenomenon(p) for p in w.phenomena)
    if w.visibility_sm is not None and not (omit and v == 2 and not weather):
        vis = _sm(w.visibility_sm)
        say.add(f"Visibility {vis[0]}" + (f", {weather}" if weather else ""), f"visibility {vis[1]}" + (f", {weather}" if weather else ""))
    elif weather:
        say.add(weather)
    if w.rvr_ft is not None:
        say.add(f"Runway {info.runway} RVR {w.rvr_ft}", f"runway {speech.runway(info.runway)} R V R {speech.feet(w.rvr_ft)}")
    if (sky := _faa_sky(w)) is not None:
        say.add(*sky)
    if w.temperature_c is not None:
        dew = f", dew point {w.dewpoint_c}" if w.dewpoint_c is not None else ""
        dew_said = f", dew point {_temp(w.dewpoint_c)}" if w.dewpoint_c is not None else ""
        say.add(f"Temperature {w.temperature_c}{dew}", f"temperature {_temp(w.temperature_c)}{dew_said}")
    if w.altimeter_inhg is not None:
        say.add(f"Altimeter {w.altimeter_inhg:.2f}", f"altimeter {speech.altimeter(w.altimeter_inhg)}")
    if ops is not None and ops.density_altitude_ft:
        say.add(f"Density altitude {ops.density_altitude_ft:,}", f"density altitude {speech.feet(ops.density_altitude_ft)}")
    if w.pressure_trend:
        say.add(f"Pressure {w.pressure_trend}")
    if info.kind != "departure":
        say.add(*_faa_approaches(info))
    say.add(*_runways(info, icao=False))
    if ops is not None:
        notices = [n.text(False) for n in ops.notices]
        if notices:
            head = ("Notices to Air Missions", "NOTAMs", "Notices to Air Missions")[v % 3]
            say.add(f"{head}: " + ". ".join(n[0].upper() + n[1:] for n in notices),
                    f"{head.lower() if head == 'NOTAMs' else head}, " + ". ".join(_said_notice(n) for n in notices))
        if ops.condition is not None:
            c = ops.condition
            codes = ", ".join(str(x) for x in c.codes)
            say.add(f"Runway {c.runway} condition codes {codes} at {info.zulu} Zulu, {c.contaminant}",
                    f"runway {speech.runway(c.runway)} condition codes {', '.join(speech.digits(str(x)) for x in c.codes)}"
                    f" at {speech.digits(info.zulu)} zulu, {c.contaminant}")
            if min(c.codes) <= 3:
                say.add("Braking action advisories are in effect")
        if ops.low_visibility:
            say.add("Low visibility operations, surface movement guidance and control procedures in effect")
        if ops.wind_shear:
            say.add("Low level wind shear advisories in effect")
        if ops.deicing:
            say.add("Deicing in progress")
        if ops.runway_change:
            say.add("Runway change in progress")
    for remark in info.remarks:
        if remark not in ("caution gusty winds", "caution wet runway", "caution snow on the runway", "caution low visibility"):
            say.add(remark)
    if info.kind != "arrival":
        say.add(("Read back all runway hold short instructions", "Read back all runway hold short instructions",
                 "Readback all runway hold short instructions")[v % 3])
    closing = (f"Advise on initial contact you have information {letter}",
               f"Advise controller on initial contact you have {letter}",
               f"Advise on initial contact you have {letter}")[v % 3]
    say.add(closing)
    return say.text()


def _faa_wind(w: Weather) -> tuple[str, str]:
    if w.calm:
        return "Wind calm", "wind calm"
    speed = w.wind.speed_kt
    if w.variable_from is not None and speed <= 6:
        return f"Wind variable at {speed}", f"wind variable at {speech.digits(str(speed))}"
    shown = f"Wind {w.wind.direction_mag:03d} at {speed}" + (f" gusts {w.gust_kt}" if w.gust_kt else "")
    said = "wind " + speech.wind(w.wind) + (f", gusts {speech.digits(str(w.gust_kt))}" if w.gust_kt else "")
    if w.variable_from is not None and w.variable_to is not None:
        shown += f", variable between {w.variable_from:03d} and {w.variable_to:03d}"
        said += (f", variable between {speech.digits(f'{w.variable_from:03d}')} and"
                 f" {speech.digits(f'{w.variable_to:03d}')}")
    return shown, said


FRACTIONS = ((0.125, "1/8", "one eighth"), (0.25, "1/4", "one quarter"), (0.5, "1/2", "one half"),
             (0.75, "3/4", "three quarters"))


def _sm(sm: float) -> tuple[str, str]:
    """Statute miles as reported: fractions below 3 (1 1/4, 3/4), whole miles to 10."""
    if sm >= 10:
        return "10", "one zero"
    if sm >= 3:
        n = int(sm)
        return str(n), speech.digits(str(n))
    whole = int(sm)
    rest = sm - whole
    _, frac_text, frac_said = min(((abs(rest - f), t, s) for f, t, s in ((0.0, "", ""), *FRACTIONS, (1.0, "+", ""))),
                                      key=lambda x: x[0])
    if frac_text == "+":
        whole, frac_text = whole + 1, ""
    if whole and frac_text:
        return f"{whole} {frac_text}", f"{speech.digits(str(whole))} and {frac_said}"
    if frac_text:
        return frac_text, frac_said
    return str(max(whole, 0)), speech.digits(str(max(whole, 0)))


INTENSITY = {"-": "light ", "+": "heavy ", "": ""}
WEATHER = {"RA": "rain", "SN": "snow", "FG": "fog", "BR": "mist", "HZ": "haze", "FU": "smoke"}


def _phenomenon(p: Phenomenon) -> str:
    if p.descriptor == "TS":
        return f"thunderstorm, {INTENSITY[p.intensity]}{WEATHER[p.kind]}"
    descriptor = "freezing " if p.descriptor == "FZ" else ""
    return f"{INTENSITY[p.intensity]}{descriptor}{WEATHER[p.kind]}"


COVER = {"FEW": "few clouds at", "SCT": "scattered", "BKN": "broken", "OVC": "overcast"}


def _faa_sky(w: Weather) -> tuple[str, str] | None:
    if w.sky is None:
        return None
    if not w.sky:
        if (w.clear_below_ft or 0) >= 12000:
            return "Sky clear", "sky clear"
        return None  # clear as far as seen, which isn't far enough to say
    shown, said = [], []
    ceiling_said = False
    for layer in w.sky:
        if layer.cover == "VV":
            shown.append(f"Indefinite ceiling {layer.base_ft}, sky obscured")
            said.append(f"indefinite ceiling {speech.feet(layer.base_ft)}, sky obscured")
            ceiling_said = True
        elif layer.cover == "FEW":
            shown.append(f"few clouds at {layer.base_ft:,}")
            said.append(f"few clouds at {speech.feet(layer.base_ft)}")
        else:
            word = COVER[layer.cover]
            lead = "ceiling " if layer.ceiling and not ceiling_said else ""
            ceiling_said = ceiling_said or layer.ceiling
            shown.append(f"{lead}{layer.base_ft:,} {word}")
            said.append(f"{lead}{speech.feet(layer.base_ft)} {word}")
    return ", ".join(shown), ", ".join(said)


def _temp(c: int) -> str:
    return ("minus " if c < 0 else "") + speech.digits(str(abs(c)))


def _approach_words(a: Approach, spoken: bool) -> str:
    if spoken:
        text = speech.approach(a)
        if a.circle_to:
            text += ", " + speech.circling(a)[1]
        return text
    name = a.name if a.runway else a.display
    text = f"{name} runway {a.runway}" if a.runway else name
    if a.circle_to:
        text += ", " + speech.circling(a)[0]
    return text


def _faa_approaches(info: AtisInfo) -> tuple[str, str]:
    ops = info.operations
    approaches = [ops.approaches[r] for r in ops.landing if r in ops.approaches] if ops else []
    if not approaches:
        approach = Approach(info.approach, info.runway)
        approaches = [approach]
    if all(a.kind == "VISUAL" for a in approaches):
        # The instrument approaches the landing runways have, named with the visuals: "ILS Y runway 26R and visual
        # approaches in use" (a pilot found plain "visual approaches in use" nothing like the real broadcasts).
        backup = [ops.instrument[r] for r in ops.landing if r in ops.instrument] if ops is not None else []
        if not backup:
            return "Visual approaches in use", "visual approaches in use"
        shown = " and ".join(_approach_words(a, False) for a in backup)
        said = " and ".join(_approach_words(a, True) for a in backup)
        return f"{shown} and visual approaches in use", f"{said} and visual approaches in use"
    if ops is not None and ops.simultaneous and len({a.kind for a in approaches}) == 1 and len(approaches) > 1:
        runways = " and ".join(a.runway for a in approaches)
        said = " and ".join(speech.runway(a.runway) for a in approaches)
        kind = approaches[0].kind
        return (f"Simultaneous {kind} approaches in use, runways {runways}",
                f"simultaneous {speech.APPROACH_SPOKEN.get(kind, kind)} approaches in use, runways {said}")
    shown = " and ".join(_approach_words(a, False) for a in approaches)
    said = " and ".join(_approach_words(a, True) for a in approaches)
    plural = "es" if len(approaches) > 1 else ""
    return f"{shown} approach{plural} in use", f"{said} approach{plural} in use"


def _runways(info: AtisInfo, *, icao: bool) -> tuple[str, str]:
    ops = info.operations
    landing = ops.landing if ops is not None else (info.runway,)
    departing = ops.departing if ops is not None else (info.runway,)
    if info.kind == "arrival":
        departing = ()
    if info.kind == "departure":
        landing = ()

    def listed(runways: tuple[str, ...], said: bool) -> str:
        return (" and " if said else ", ").join(speech.runway(r) if said else r for r in runways)

    if landing and landing == departing:
        noun = "runways" if len(landing) > 1 else "runway"
        if icao:
            return f"Runway{'s' if len(landing) > 1 else ''} in use {listed(landing, False)}", \
                f"runway{'s' if len(landing) > 1 else ''} in use {listed(landing, True)}"
        return f"Landing and departing {noun} {listed(landing, False)}", f"landing and departing {noun} {listed(landing, True)}"
    shown, said = [], []
    if landing:
        noun = "runways" if len(landing) > 1 else "runway"
        shown.append(f"Landing {noun} {listed(landing, False)}")
        said.append(f"landing {noun} {listed(landing, True)}")
    if departing:
        noun = "runways" if len(departing) > 1 else "runway"
        word = "departure" if icao else "departing"
        shown.append(f"{word.capitalize()} {noun} {listed(departing, False)}")
        said.append(f"{word} {noun} {listed(departing, True)}")
    return ". ".join(shown), ". ".join(said)


def _said_notice(text: str) -> str:
    """A notice as said: runway and taxiway names spelled out."""
    words = text.split()
    out = []
    for i, word in enumerate(words):
        before = words[i - 1] if i else ""
        if before == "runway" and word[:1].isdigit():
            out.append(" slash ".join(speech.runway(p) for p in word.split("/")))
        elif before == "taxiway":
            out.append(speech.taxiway(word))
        else:
            out.append(word)
    return " ".join(out)


# --- ICAO -----------------------------------------------------------------------------------------------------------


def _icao(info: AtisInfo, say: _Say, v: int) -> str:
    w, ops = info.weather, info.operations
    letter = speech.letter(info.letter).capitalize()
    kind = {"arrival": " arrival", "departure": " departure"}.get(info.kind, "")
    say.add(f"This is {info.name}{kind} information {letter}, time {info.zulu}",
            f"this is {info.name}{kind} information {letter}, time {speech.digits(info.zulu)}")
    if info.kind != "departure":
        approaches = [ops.approaches[r] for r in ops.landing if r in ops.approaches] if ops else [Approach(info.approach, info.runway)]
        visual = all(a.kind == "VISUAL" for a in approaches)
        if visual:
            say.add("Expect visual approach", "expect visual approach")
        else:
            say.add("Expect " + " and ".join(_icao_approach(a, False) for a in approaches),
                    "expect " + " and ".join(_icao_approach(a, True) for a in approaches))
    say.add(*_runways(info, icao=True))
    if ops is not None and ops.condition is not None:
        c = ops.condition
        say.add(f"Runway {c.runway} condition code {', '.join(str(x) for x in c.codes)}, " + ", ".join([c.contaminant] * 3),
                f"runway {speech.runway(c.runway)} condition code {', '.join(speech.digits(str(x)) for x in c.codes)}, "
                + ", ".join([c.contaminant] * 3))
    if w.altimeter_inhg is not None and info.region is not None:
        level = transition_level(info.region.transition_ft, w.altimeter_inhg)
        say.add(f"Transition level {level}", f"transition level {speech.digits(str(level))}")
    if ops is not None:
        for notice in ops.notices:
            say.add(notice.text(True), _said_notice(notice.text(True)))
        if ops.low_visibility:
            say.add("Low visibility procedures in operation")
        if ops.deicing:
            say.add("De-icing in progress")
    say.add(*_icao_wind(w))
    if _cavok(w):
        say.add("CAVOK", "cavok")
    else:
        if w.visibility_sm is not None:
            say.add(*_icao_visibility(w.visibility_sm))
        if w.rvr_ft is not None:
            metres = int(round(w.rvr_ft / 3.28084 / 50) * 50)
            say.add(f"RVR runway {info.runway} {metres} metres", f"R V R runway {speech.runway(info.runway)} {_metres(metres)}")
        if w.phenomena:
            say.add(", ".join(_phenomenon(p) for p in w.phenomena))
        if (clouds := _icao_clouds(w)) is not None:
            say.add(*clouds)
    if w.temperature_c is not None:
        dew = f", dew point {w.dewpoint_c}" if w.dewpoint_c is not None else ""
        dew_said = f", dew point {_temp(w.dewpoint_c)}" if w.dewpoint_c is not None else ""
        say.add(f"Temperature {w.temperature_c}{dew}", f"temperature {_temp(w.temperature_c)}{dew_said}")
    if w.altimeter_inhg is not None:
        say.add(f"QNH {speech.hpa(w.altimeter_inhg)}", f"Q N H {speech.digits(str(speech.hpa(w.altimeter_inhg)))}")
    if ops is not None and ops.wind_shear:
        say.add("Wind shear reported")
    if w.pressure_trend:
        say.add(f"Pressure {w.pressure_trend}")
    closing = (f"Acknowledge receipt of information {letter} and advise aircraft type on first contact",
               f"On first contact, advise you have information {letter}",
               f"Acknowledge information {letter} on first contact")[v % 3]
    say.add(closing)
    return say.text()


def _icao_approach(a: Approach, spoken: bool) -> str:
    if spoken:
        return f"{speech.APPROACH_SPOKEN.get(a.kind, a.kind)} approach" + (
            f" runway {speech.runway(a.runway)}" if a.runway else "") + (f", {speech.circling(a)[1]}" if a.circle_to else "")
    return f"{a.name} approach" + (f" runway {a.runway}" if a.runway else "") + (f", {speech.circling(a)[0]}" if a.circle_to else "")


def _icao_wind(w: Weather) -> tuple[str, str]:
    if w.calm:
        return "Wind calm", "wind calm"
    speed = w.wind.speed_kt
    if w.variable_from is not None and speed <= 3:
        return f"Wind variable {speed} knots", f"wind variable {speech.digits(str(speed))} knots"
    shown = f"Wind {w.wind.direction_mag:03d} degrees {speed} knots" + (f", gusting {w.gust_kt} knots" if w.gust_kt else "")
    said = "wind " + speech.wind(w.wind) + (f", gusting {speech.digits(str(w.gust_kt))} knots" if w.gust_kt else "")
    if w.variable_from is not None and w.variable_to is not None:
        shown += f", varying between {w.variable_from:03d} and {w.variable_to:03d} degrees"
        said += (f", varying between {speech.digits(f'{w.variable_from:03d}')} and"
                 f" {speech.digits(f'{w.variable_to:03d}')} degrees")
    return shown, said


def _icao_visibility(sm: float) -> tuple[str, str]:
    metres = sm * M_PER_SM
    if metres >= 9999:
        return "Visibility 10 kilometres or more", "visibility one zero kilometres or more"
    if metres >= 5000:
        km = int(metres // 1000)
        return f"Visibility {km} kilometres", f"visibility {speech.digits(str(km))} kilometres"
    step = 50 if metres < 800 else 100
    m = int(round(metres / step) * step)
    return f"Visibility {m} metres", f"visibility {_metres(m)}"


def _metres(m: int) -> str:
    thousands, rest = divmod(m, 1000)
    parts = []
    if thousands:
        parts.append(f"{speech.digits(str(thousands))} thousand")
    hundreds, tens = divmod(rest, 100)
    if hundreds:
        parts.append(f"{speech.ONES[hundreds]} hundred")
    if tens:
        parts.append(speech.number_words(tens))
    return " ".join(parts) + " metres"


def _cavok(w: Weather) -> bool:
    """Visibility 10 km or more, no cloud below 5,000 ft, no weather to speak of: CAVOK (only when known)."""
    return (w.visibility_sm is not None and w.visibility_sm * M_PER_SM >= 9999 and not w.phenomena
            and w.sky is not None and all(layer.base_ft >= 5000 for layer in w.sky)
            and (w.sky or (w.clear_below_ft or 0) >= 5000))


def _icao_clouds(w: Weather) -> tuple[str, str] | None:
    if w.sky is None:
        return None
    low = [layer for layer in w.sky if layer.base_ft < 5000 or layer.cover == "VV"]
    if not low:
        return ("No significant cloud", "no significant cloud") if (w.clear_below_ft or 0) >= 5000 or w.sky else None
    shown, said = [], []
    for layer in low:
        if layer.cover == "VV":
            shown.append(f"vertical visibility {layer.base_ft} feet")
            said.append(f"vertical visibility {speech.feet(layer.base_ft)} feet")
            continue
        word = {"FEW": "few", "SCT": "scattered", "BKN": "broken", "OVC": "overcast"}[layer.cover]
        shown.append(f"{word} {layer.base_ft} feet")
        said.append(f"{word} {speech.feet(layer.base_ft)} feet")
    if len(low) == 1 and low[0].cover == "VV":
        return shown[0][0].upper() + shown[0][1:], said[0]
    return "Cloud " + ", ".join(shown), "cloud " + ", ".join(said)


def transition_level(transition_ft: int, altimeter_inhg: float) -> int:
    """The lowest flight level at least 1,000 ft above the transition altitude on today's QNH, in tens."""
    qnh = altimeter_inhg * 33.8639
    for level in range(10, 600, 5):
        if level * 100 + (qnh - 1013.25) * 27.3 >= transition_ft + 1000 - 30 and level % 10 == 0:
            return level
    return int(math.ceil((transition_ft + 1000) / 1000) * 10)


def layers_text(sky: tuple[Layer, ...]) -> str:
    """ "FEW025 BKN040", for the app."""
    return " ".join(f"{layer.cover}{layer.base_ft // 100:03d}" for layer in sky)


def wind_text(w: Weather) -> str:
    return _faa_wind(w)[0]

