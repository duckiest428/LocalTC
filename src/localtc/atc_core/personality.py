"""A bit of personality: each controller greets and signs off in its own way.

The words ATC must say come from the templates; around them, real controllers differ. Some open with "good
afternoon" on the first call, some never do; some close a handoff with "good day", others with "have a good
one" or nothing at all. Each station gets its habits from its name, so Denver Center sounds the same every
time you fly through it, and different from Salt Lake Center next door.
"""

import zlib
from dataclasses import dataclass

SIGN_OFFS = ("good day", "have a good one", "so long", "have a good flight", "see ya", "good day")
ICAO_SIGN_OFFS = ("good day", "goodbye", "bye bye", "good day")


@dataclass(frozen=True)
class Personality:
    greets: float  # how often the first call to a flight opens with "good afternoon"
    signs_off: float  # how often a handoff ends with a sign-off
    sign_off: str  # which one


def personality(station: str, *, icao: bool = False) -> Personality:
    h = zlib.crc32(station.lower().encode())
    offs = ICAO_SIGN_OFFS if icao else SIGN_OFFS
    return Personality(greets=0.35 + (h % 50) / 100, signs_off=0.4 + ((h >> 8) % 50) / 100,
                       sign_off=offs[(h >> 16) % len(offs)])


def part_of_day(zulu_s: float | None, lon: float) -> str | None:
    """ "morning", "afternoon" or "evening" by the sun where the aircraft is (None without a time)."""
    if zulu_s is None:
        return None
    hour = (zulu_s / 3600 + lon / 15) % 24
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening"


def greet(text: str, callsign: str, station: str, words: str) -> str:
    """ "Delta 2543, Denver Departure, radar contact" -> "Delta 2543, Denver Departure, good afternoon, radar
    contact"; without the station named, the greeting follows the callsign."""
    head = f"{callsign}, "
    if not text.startswith(head):
        return text
    rest = text[len(head):]
    if rest.startswith(f"{station}, "):
        head, rest = f"{head}{station}, ", rest[len(station) + 2:]
    return f"{head}{words}, {rest}"


def sign_off(text: str, words: str) -> str:
    """ "..., contact Seattle Center 132.6." -> "..., contact Seattle Center 132.6, good day." (once)."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in ("good day", "good night", "have a good", "so long", "see ya", "bye")):
        return text
    return f"{text.rstrip('.')}, {words}."
