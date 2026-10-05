"""What the copilot knows about the flight, and the questions it answers straight from it.

``facts`` is everything the copilot can tell from the sim and from ATC, as short labelled lines: the language model
answers from these and nothing else. ``answer`` handles the common questions without the model at all ("how much fuel
do we have", "how far to go", "what did ATC say"): fast, exact, and the same every time.
"""

import re
from dataclasses import dataclass
from typing import Any

from localtc.atc_core.phraseology import speech
from localtc.crew.actions import Cockpit
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
        own = self.cockpit.own
        if own is None or not own.fuel_lb or not own.fuel_flow_pph or own.fuel_flow_pph < 50:
            return None
        engines = (self.cockpit.systems.engines_running if self.cockpit.systems is not None else 0) or 1
        return own.fuel_lb / (own.fuel_flow_pph * engines) * 60


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
        if a.departure_runway and st.phase in ("PARKED", "PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF"):
            out["departure runway"] = a.departure_runway
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
        out["gear"] = "down" if own.gear_down else "up"
        out["flaps"] = c.profile.detent_name(own.flaps_index, c.flap_positions, on_ground=own.on_ground)
        if own.temperature_c is not None:
            out["outside temperature"] = f"{own.temperature_c:.0f} degrees"
        if own.wind_kt >= 3:
            out["wind here"] = f"{round(own.wind_dir_true - own.magvar) % 360 or 360:03d} at {own.wind_kt:.0f} knots"
        if own.zulu_s is not None:
            out["time"] = f"{int(own.zulu_s // 3600) % 24:02d}{int(own.zulu_s % 3600 // 60):02d} Zulu"
    if (to_go := p.to_go()) is not None:
        nm, minutes = to_go
        out["to go"] = f"{nm:,.0f} miles" + (f", about {_hm(minutes)}" if minutes is not None else "")
    if c.systems is not None:
        s = c.systems
        on = [name for name, lit in (("landing", s.light_landing), ("taxi", s.light_taxi), ("strobes", s.light_strobe),
                                     ("beacon", s.light_beacon), ("nav", s.light_nav), ("logo", s.light_logo)) if lit]
        out["lights on"] = ", ".join(on) or "none"
        out["spoilers"] = "armed" if s.spoilers_armed else "extended" if s.spoilers_pct > 40 else "stowed"
    if p.last_atc is not None:
        out["ATC last said"] = f'{p.last_atc.station}: "{p.last_atc.text}"'
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
    (r"(?:what|which) runway", "runway"),
    (r"(?:what|which) gate", "gate"),
)


def answer(text: str, p: Picture) -> str | None:
    """The answer to one of the common questions, from the facts; None when it isn't one of them."""
    lowered = text.lower()
    if not ("?" in text or re.match(r"^\s*(?:what|how|when|where|which|who|do|did|are|is|can you tell|tell me)\b", lowered)):
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
                return f"Runway {f[key]}."
        return "No runway assigned yet."
    if kind == "gate":
        return f"{f['gate']}." if "gate" in f else "No gate assigned yet."
    return None


def numbers_in(text: str) -> set[str]:
    """The numbers a reply mentions, as digits ("10,000" -> "10000"), for checking a model's reply against facts."""
    return {n.replace(",", "").rstrip(".").lstrip("0") or "0" for n in re.findall(r"\d[\d,.]*", text)}
