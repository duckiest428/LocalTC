"""What the copilot knows about the flight, and the questions it answers straight from it.

``facts`` is everything the copilot can tell from the sim and from ATC, as short labelled lines: the language model
answers from these and nothing else. ``answer`` handles the common questions without the model at all ("how much fuel
do we have", "how far to go", "what did ATC say"): fast, exact, and the same every time.
"""

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from localtc.atc_core.phraseology import speech
from localtc.crew.actions import Cockpit
from localtc.crew.places import where
from localtc.sim_api import AtcTransmission
from localtc.sim_api.geo import haversine_nm

LB_PER_KG = 2.20462


@dataclass
class Picture:
    """The cockpit plus what the copilot has heard and been told."""

    cockpit: Cockpit
    engine: Any = None  # the ATC engine, read only
    last_atc: AtcTransmission | None = None
    metric: bool = False  # fuel in kilograms (outside North America)
    burn_pph: float | None = None  # the cruise burn measured from the fuel used (the monitor's), pounds an hour
    fuel_doubted: bool = False  # the pilot said the fuel projections are off: none until told otherwise

    @property
    def destination(self) -> str | None:
        return self.engine.state.flight.destination if self.engine is not None else None

    def to_go(self) -> tuple[float, float | None] | None:
        """(nm to the destination, minutes at this ground speed) or None when its position isn't known."""
        own, dest = self.cockpit.own, self.destination
        geo = self.engine.geometry(dest) if self.engine is not None and dest else None
        if own is None or geo is None:
            return None
        nm = haversine_nm(own.lat, own.lon, geo.airport.lat, geo.airport.lon)
        minutes = nm / own.gs_kt * 60 if own.gs_kt > 60 else None
        return nm, minutes

    def fuel(self) -> str | None:
        own = self.cockpit.own
        if own is None or own.fuel_lb is None:
            return None
        if self.metric:
            return f"{round(own.fuel_lb / LB_PER_KG / 100) * 100:,} kilos"
        return f"{round(own.fuel_lb / 100) * 100:,} pounds"

    def endurance_min(self) -> float | None:
        """Fuel over the burn: the one measured in the cruise where there is one (an add-on's fuel flow variable can
        be anything), else the sim's fuel flow."""
        own = self.cockpit.own
        if own is None or not own.fuel_lb:
            return None
        if self.burn_pph:
            return own.fuel_lb / self.burn_pph * 60
        if not own.fuel_flow_pph or own.fuel_flow_pph < 50:
            return None
        engines = (self.cockpit.systems.engines_running if self.cockpit.systems is not None else 0) or 1
        return own.fuel_lb / (own.fuel_flow_pph * engines) * 60


def _components(engine: Any, icao: str, runway: str | None, w: Any) -> tuple[str, str] | None:
    """(runway, "23 knots headwind, 1 knot crosswind from the left") from the ATIS wind; None when it can't be told."""
    from localtc.atc_core.weather import components

    geo = engine.geometry(icao) if engine is not None and runway else None
    end = geo.end(runway) if geo is not None else None
    if end is None or w is None or not getattr(w.wind, "speed_kt", 0):
        return None
    head, cross = components(end, w)
    side = "right" if ((w.wind_dir_true - end.heading_true + 360) % 360) < 180 else "left"
    knots = lambda n: f"{n} knot{'' if n == 1 else 's'}"  # noqa: E731
    along = f"{knots(abs(round(head)))} {'headwind' if head >= 0 else 'tailwind'}"
    return end.ident, f"{along}, {knots(round(cross))} crosswind from the {side}"


def _hm(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h} hour{'s' if h != 1 else ''} {m} minute{'s' if m != 1 else ''}" if h else f"{m} minute{'s' if m != 1 else ''}"


def facts(p: Picture) -> dict[str, str]:
    """Everything the copilot can say about the flight right now."""
    c, own, out = p.cockpit, p.cockpit.own, {}
    st = p.engine.state if p.engine is not None else None
    if st is not None:
        f = st.flight
        if f.callsign:
            out["callsign"] = speech.callsign_display(f.callsign)
        if f.origin or f.destination:
            out["route"] = f"{f.origin or '?'} to {f.destination or '?'}"
        if st.phase:
            out["phase"] = st.phase.lower().replace("_", " ")
        a = st.assignments
        if a.altitude_ft:
            out["cleared altitude"] = speech.altitude_display(a.altitude_ft)
        if a.squawk:
            out["squawk"] = a.squawk
        departing = st.phase in (None, "PARKED", "PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF")
        if a.departure_runway and departing:
            out["departure runway"] = a.departure_runway
        elif departing:
            info = p.engine.current_atis(f.origin) if f.origin else None
            planned = getattr(getattr(p.engine, "cfg", None), "dep_runway", None)
            out["departure runway"] = "not assigned yet" + (f"; the ATIS has runway {info.runway} in use" if info else "") \
                + (f"; the flight plan says {planned}" if planned else "")
        if a.arrival_runway:
            out["landing runway"] = a.arrival_runway
        if a.approach:
            out["expected approach"] = a.approach
        if a.gate:
            out["gate"] = a.gate
        tuned = st.comms.tuned
        if tuned is not None:
            out["talking to"] = f"{tuned.station} on {speech.frequency_display(tuned.mhz)}"
        dest = f.destination
        if dest and (info := p.engine.current_atis(dest)) is not None:
            w = info.weather
            weather = f"information {info.letter}, wind {speech.wind_display(w.wind)}"
            if w.altimeter_inhg:
                weather += f", altimeter {speech.altimeter_display(w.altimeter_inhg)}"
            if w.temperature_c is not None:
                weather += f", temperature {w.temperature_c}"
            out["destination weather"] = weather + f", runway {info.runway} in use"
            if (comp := _components(p.engine, dest, a.arrival_runway or info.runway, w)) is not None:
                out[f"wind on runway {comp[0]}"] = comp[1]
    if own is not None:
        out["altitude"] = f"{round(own.alt_indicated_ft / 10) * 10:,.0f} feet"
        out["speed"] = f"{own.ias_kt:.0f} knots indicated, {own.gs_kt:.0f} over the ground"
        out["heading"] = f"{round(own.hdg_mag) % 360 or 360:03d}"
        if abs(own.vs_fpm) >= 300:
            out["vertical speed"] = f"{'climbing' if own.vs_fpm > 0 else 'descending'} {abs(round(own.vs_fpm, -2)):,.0f} feet a minute"
        if (fuel := p.fuel()) is not None:
            out["fuel"] = fuel
        if (endurance := p.endurance_min()) is not None:
            out["endurance"] = _hm(endurance)
        out["position"] = where(own.lat, own.lon) or f"{abs(own.lat):.1f}{'N' if own.lat >= 0 else 'S'} " \
            f"{abs(own.lon):.1f}{'E' if own.lon >= 0 else 'W'}, no town within 60 miles"
        out["gear"] = "down" if own.gear_down else "up"
        if c.flaps_index is not None:
            out["flaps"] = c.profile.detent_name(c.flaps_index, c.flap_positions, on_ground=own.on_ground)
        else:
            out["flaps"] = "can't be read in this aircraft"
        if own.temperature_c is not None:
            out["outside temperature"] = f"{own.temperature_c:.0f} degrees"
        if own.wind_kt >= 3:
            out["wind here"] = f"{round(own.wind_dir_true - own.magvar) % 360 or 360:03d} at {own.wind_kt:.0f} knots"
        if own.zulu_s is not None:
            out["time"] = f"{int(own.zulu_s // 3600) % 24:02d}{int(own.zulu_s % 3600 // 60):02d} Zulu"
    if st is not None and own is not None and not any(k.startswith("wind on runway") for k in out) \
            and st.assignments.arrival_runway and own.wind_kt >= 1 and (p.to_go() or (999.0,))[0] <= 60:
        # No ATIS for it: the wind where the aircraft is, on the runway it's landing on.
        here = SimpleNamespace(wind=SimpleNamespace(speed_kt=own.wind_kt), gust_kt=None, wind_dir_true=own.wind_dir_true)
        if (comp := _components(p.engine, st.flight.destination, st.assignments.arrival_runway, here)) is not None:
            out[f"wind on runway {comp[0]}"] = comp[1] + " (the wind here)"
    if (to_go := p.to_go()) is not None:
        nm, minutes = to_go
        out["to go"] = f"{nm:,.0f} miles" + (f", about {_hm(minutes)}" if minutes is not None else "")
    if c.systems is not None:
        s = c.systems
        on = [name for name, lit in (("landing", s.light_landing), ("taxi", s.light_taxi), ("strobes", s.light_strobe),
                                     ("beacon", s.light_beacon), ("nav", s.light_nav), ("logo", s.light_logo)) if lit]
        out["lights on"] = ", ".join(on) or "none"
        out["spoilers"] = "armed" if s.spoilers_armed else "extended" if s.spoilers_pct > 40 else "stowed"
        if c.autopilot_known:
            modes = [m for m, on in (("heading", s.ap_heading), ("nav", s.ap_nav), ("approach", s.ap_approach),
                                     ("altitude hold", s.ap_altitude), ("vertical speed", s.ap_vs), ("level change", s.ap_flc)) if on]
            out["autopilot"] = ("on" + (f", {', '.join(modes)}" if modes else "")) if s.ap_master else "off"
        else:
            out["autopilot"] = "can't be read in this aircraft"
        if own is not None and not own.on_ground:
            out["localizer"] = ("captured" if abs(s.loc_deviation) < 20 else "alive") if s.loc_received else "not received"
            out["glideslope"] = (("on it" if abs(s.gs_deviation) < 20 else "above us" if s.gs_deviation > 0 else "below us")
                                 if s.gs_received else "not received")
        if (brake := c.profile.autobrake_name(s.autobrake)):
            out["autobrake"] = brake
    if p.last_atc is not None:
        out["ATC last said"] = f'{p.last_atc.station}: "{p.last_atc.text}"'
    out["what I can't see"] = "the weather radar, the view outside, the autoland status, anything not listed here"
    return out


# The common questions, answered from the facts with no model: (words that ask it, how to answer).
QUESTIONS: tuple[tuple[str, str], ...] = (
    (r"\bfuel\b|\bgas\b", "fuel"),
    (r"how (?:far|long)|\bto go\b|\bdistance\b|\beta\b|when (?:do|will) we (?:get|arrive|land)|time to destination", "to_go"),
    (r"what did (?:atc|they|he|she|the controller) (?:say|want)|say again what|last (?:call|clearance|transmission)", "atc"),
    (r"(?:what|which) (?:altitude|level)|how high|our altitude", "altitude"),
    (r"(?:what|which) frequency|who (?:are|am) (?:we|i) (?:talking|on)|which (?:controller|station)", "frequency"),
    (r"\bsquawk\b|transponder code", "squawk"),
    (r"\bweather\b|\bwinds?\b at|\bmetar\b|\batis\b", "weather"),
    (r"what time|\btime is it\b|\bzulu\b", "time"),
    (r"(?:what|which) runway|runway (?:are we|do we|will we)", "runway"),
    (r"where are we|what (?:city|town|state|country)|which (?:city|town|state)|are we over|our (?:position|location)", "where"),
    (r"(?:what|which) gate", "gate"),
    (r"cross ?wind|head ?wind|tail ?wind", "components"),
    (r"\bgear\b", "gear"),
    (r"\bflaps?\b", "flaps"),
    (r"auto ?pilot", "autopilot"),
)


def answer(text: str, p: Picture) -> str | None:
    """The answer to one of the common questions, from the facts; None when it isn't one of them."""
    lowered = text.lower()
    if not ("?" in text or re.match(r"^\s*(?:[a-z']+[,\s]+){0,2}(?:what|how|when|where|which|who|do|did|are|is|can you tell|tell me)\b",
                                    lowered)):
        return None
    kind = next((k for pattern, k in QUESTIONS if re.search(pattern, lowered)), None)
    if kind is None:
        return None
    f = facts(p)
    if kind == "fuel":
        if "fuel" not in f:
            return "The fuel quantity isn't showing here."
        return f"We've got {f['fuel']}" + (f", about {f['endurance']} at this burn." if "endurance" in f else ".")
    if kind == "to_go":
        return f"{f['to go'].capitalize()} to {p.destination}." if "to go" in f else "I don't have the destination's position yet."
    if kind == "atc":
        return f"{p.last_atc.station} said: {p.last_atc.text}" if p.last_atc is not None else "Nothing from ATC yet."
    if kind == "altitude":
        cleared = f", cleared to {f['cleared altitude']}" if "cleared altitude" in f else ""
        return f"We're at {f['altitude']}{cleared}." if "altitude" in f else None
    if kind == "frequency":
        return f"We're with {f['talking to']}." if "talking to" in f else "We're not on an ATC frequency."
    if kind == "squawk":
        return f"Squawk {f['squawk']}." if "squawk" in f else "No code assigned yet."
    if kind == "weather":
        return f"At {p.destination}: {f['destination weather']}." if "destination weather" in f else \
            "No ATIS for the destination yet."
    if kind == "time":
        return f"It's {f['time']}." if "time" in f else None
    if kind == "runway":
        for key in ("departure runway", "landing runway"):
            if key in f:
                value = f[key]
                return f"Runway {value}." if not value.startswith("not") else f"Not assigned yet{value[len('not assigned yet'):]}."
        return "No runway assigned yet."
    if kind == "where":
        own = p.cockpit.own
        if own is None:
            return None
        place = where(own.lat, own.lon)
        return f"We're {place}." if place else "Nowhere near a town; " + f["position"].split(",")[0] + "."
    if kind == "gate":
        return f"{f['gate']}." if "gate" in f else "No gate assigned yet."
    if kind == "components":
        wind = [(k, v) for k, v in f.items() if k.startswith("wind on runway")]
        return f"Runway {wind[0][0].split()[-1]}: {wind[0][1]}." if wind else "I don't have the runway's wind yet."
    if kind == "gear":
        return f"Gear's {f['gear']}." if "gear" in f else None
    if kind == "flaps":
        return None if f.get("flaps", "").startswith("can't") else f"Flaps {f['flaps']}." if "flaps" in f else None
    if kind == "autopilot":
        value = f.get("autopilot", "")
        return ("I can't read the autopilot on this aircraft." if value.startswith("can't") else
                f"Autopilot's {value}." if value else None)
    return None


def numbers_in(text: str) -> set[str]:
    """The numbers a reply mentions, as digits ("10,000" -> "10000"), for checking a model's reply against facts."""
    return {n.replace(",", "").rstrip(".").lstrip("0") or "0" for n in re.findall(r"\d[\d,.]*", text)}
