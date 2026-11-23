"""Typed template slots: each slot name maps to a type that renders display text and spoken words."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from localtc.atc_core.phraseology import speech, tongues
from localtc.atc_core.values import Approach, Callsign, Phrase, Wind


@dataclass(frozen=True)
class SlotType:
    name: str
    value_type: type | tuple[type, ...]
    display: Callable[[Any], str]
    spoken: Callable[[Any], str]


def _text(v: str) -> str:
    return str(v)


def _word(v: str) -> str:
    """An English word the engine fills in ("left", "northbound"), in the language being spoken."""
    tg = tongues.current()
    return tg.word(str(v)) if tg is not None else str(v)


def _station(v: str) -> str:
    """A station's name in the language being spoken ("Paris Control" is "Paris Contrôle" in French)."""
    tg = tongues.current()
    return tg.station(str(v)) if tg is not None else str(v)


def _approach_shown(v: Approach) -> str:
    tg = tongues.current()
    return v.display if tg is None else tg.approach_shown(v.kind, v.runway, v.suffix, v.charted)


def _gate_shown(v: str) -> str:
    tg = tongues.current()
    return str(v) if tg is None else tg.place(str(v))[0]


def _length_shown(v: int) -> str:
    rounded = int(round(v / 100.0) * 100)
    tg = tongues.current()
    return f"{rounded:,}" if tg is None else f"{rounded} {tg.feet}"


CALLSIGN = SlotType("callsign", Callsign, speech.callsign_display, speech.callsign)
TEXT = SlotType("text", str, _text, _text)
WORD = SlotType("word", str, _word, _word)  # left, right, northbound: said in the language spoken
STATION = SlotType("station", str, _station, _station)
PHRASE = SlotType("phrase", Phrase, lambda v: v.display, lambda v: v.spoken)
RUNWAY = SlotType("runway", str, _text, speech.runway)
FREQUENCY = SlotType("frequency", (float, int), speech.frequency_display, speech.frequency)
SQUAWK = SlotType("squawk", str, _text, speech.squawk)
ALTITUDE = SlotType("altitude", int, speech.altitude_display, speech.altitude)
SPEED = SlotType("speed", int, lambda v: str(int(v)), speech.speed)
HEADING = SlotType("heading", int, lambda v: f"{int(v) % 360 or 360:03d}", speech.heading)
TAXI_ROUTE = SlotType("taxi_route", tuple, lambda v: ", ".join(v), speech.taxi_route)
TAXI_POINT = SlotType("taxi_point", str, _text, lambda v: speech.taxi_route((v,)))  # one taxiway: "Delta", "C7"
APPROACH = SlotType("approach", Approach, _approach_shown, speech.approach)
PROCEDURE = SlotType("procedure", str, _text, speech.procedure)
ATIS = SlotType("atis", str, lambda v: speech.letter(v).capitalize(), speech.letter)
WIND = SlotType("wind", Wind, speech.wind_display, speech.wind)
ALTIMETER = SlotType("altimeter", float, speech.altimeter_display, speech.altimeter)


def _minutes(v: int) -> str:
    """Minutes as ATC says them: "five" under ten, digit by digit above ("one seven"). ICAO says them as a
    number ("seventeen"): right after "flight level eight zero", digits would run into the level."""
    from localtc.atc_core.region import CURRENT

    return speech.number_words(int(v)) if int(v) < 10 or CURRENT.get().icao else speech.digits(str(int(v)))


MINUTES = SlotType("minutes", int, _minutes, _minutes)
# A time of day, UTC ("1432"): a hold's expected further clearance; said digit by digit.
CLOCK = SlotType("clock", str, _text, speech.digits)
GATE = SlotType("gate", str, _gate_shown, speech.gate)
MILES = SlotType("miles", int, lambda v: str(int(v)), speech.miles)
LENGTH = SlotType("length", int, _length_shown, speech.feet)

# Slot name used in templates -> its type. Names double as readback element names.
SLOTS: dict[str, SlotType] = {
    "callsign": CALLSIGN,
    "station": STATION,  # spoken station name, e.g. "Seattle Departure"
    "destination": TEXT,  # spoken airport name, e.g. "Boeing Field"
    "city": TEXT,  # the place an airport serves, as its stations are named: "welcome to Dubai"
    "fix": TEXT,  # a waypoint or airport the pilot asked to go direct to
    "missing": PHRASE,  # pre-rendered fragments ("read back {missing}")
    "correction": PHRASE,
    "message": PHRASE,  # free wording: an answer or a declined request
    "runway_note": PHRASE,  # a clearance's ", expect runway 27C" ("" when the runway isn't known yet)
    "runway": RUNWAY,
    "hold_short": RUNWAY,
    "frequency": FREQUENCY,
    "squawk": SQUAWK,
    "altitude": ALTITUDE,
    "cruise": ALTITUDE,
    "heading": HEADING,
    "speed": SPEED,  # an assigned airspeed in knots
    "taxi_route": TAXI_ROUTE,
    "hold_point": TAXI_POINT,  # where a taxi clearance meets the runway
    "turn": WORD,  # "left" or "right"
    "direction": WORD,  # a VFR departure's way out: "northbound", "straight-out"
    "approach": APPROACH,
    "procedure": PROCEDURE,  # a SID or STAR ident from the flight plan
    "atis": ATIS,
    "wind": WIND,
    "altimeter": ALTIMETER,
    "minutes": MINUTES,
    "gate": GATE,  # "Gate B25", "Parking 3"  # "expect FL350 one seven minutes after departure"
    "distance": MILES,  # nautical miles to somewhere: "Yuma, 38 miles"
    "length": LENGTH,  # a runway's length in feet
    "bearing": WORD,  # where somewhere is from the aircraft: "southeast"
    "side": WORD,  # which way off the runway: "left" or "right"
    "exit": TAXI_POINT,  # the taxiway it leaves by
    "efc": CLOCK,  # a hold's expected further clearance time
}

# What a slot says when nothing better is known. "Expect FL350 one zero minutes after departure" is what a
# real clearance says without a flight plan's climb to go on; the engine always works out its own.
SLOT_DEFAULTS: dict[str, Any] = {"minutes": 10, "runway_note": Phrase("", "")}
