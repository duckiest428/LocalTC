"""Typed template slots: each slot name maps to a type that renders display text and spoken words."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Approach, Callsign, Phrase, Wind


@dataclass(frozen=True)
class SlotType:
    name: str
    value_type: type | tuple[type, ...]
    display: Callable[[Any], str]
    spoken: Callable[[Any], str]


def _text(v: str) -> str:
    return str(v)


CALLSIGN = SlotType("callsign", Callsign, speech.callsign_display, speech.callsign)
TEXT = SlotType("text", str, _text, _text)
PHRASE = SlotType("phrase", Phrase, lambda v: v.display, lambda v: v.spoken)
RUNWAY = SlotType("runway", str, _text, speech.runway)
FREQUENCY = SlotType("frequency", (float, int), speech.frequency_display, speech.frequency)
SQUAWK = SlotType("squawk", str, _text, speech.squawk)
ALTITUDE = SlotType("altitude", int, speech.altitude_display, speech.altitude)
SPEED = SlotType("speed", int, lambda v: str(int(v)), speech.speed)
HEADING = SlotType("heading", int, lambda v: f"{int(v) % 360 or 360:03d}", speech.heading)
TAXI_ROUTE = SlotType("taxi_route", tuple, lambda v: ", ".join(v), speech.taxi_route)
TAXI_POINT = SlotType("taxi_point", str, _text, lambda v: speech.taxi_route((v,)))  # one taxiway: "Delta", "C7"
APPROACH = SlotType("approach", Approach, lambda v: v.display, speech.approach)
PROCEDURE = SlotType("procedure", str, _text, speech.procedure)
ATIS = SlotType("atis", str, lambda v: speech.letter(v).capitalize(), speech.letter)
WIND = SlotType("wind", Wind, speech.wind_display, speech.wind)
ALTIMETER = SlotType("altimeter", float, lambda v: f"{v:.2f}", speech.altimeter)


def _minutes(v: int) -> str:
    """Minutes as ATC says them: "five" under ten, digit by digit above ("one seven")."""
    return speech.number_words(int(v)) if int(v) < 10 else speech.digits(str(int(v)))


MINUTES = SlotType("minutes", int, _minutes, _minutes)

# Slot name used in templates -> its type. Names double as readback element names.
SLOTS: dict[str, SlotType] = {
    "callsign": CALLSIGN,
    "station": TEXT,  # spoken station name, e.g. "Seattle Departure"
    "destination": TEXT,  # spoken airport name, e.g. "Boeing Field"
    "fix": TEXT,  # a waypoint or airport the pilot asked to go direct to
    "missing": PHRASE,  # pre-rendered fragments ("read back {missing}")
    "correction": PHRASE,
    "message": PHRASE,  # free wording: an answer or a declined request
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
    "turn": TEXT,  # "left" or "right"
    "approach": APPROACH,
    "procedure": PROCEDURE,  # a SID or STAR ident from the flight plan
    "atis": ATIS,
    "wind": WIND,
    "altimeter": ALTIMETER,
    "minutes": MINUTES,  # "expect FL350 one seven minutes after departure"
}

# What a slot says when nothing better is known. "Expect FL350 one zero minutes after departure" is what a
# real clearance says without a flight plan's climb to go on; the engine always works out its own.
SLOT_DEFAULTS: dict[str, Any] = {"minutes": 10}
