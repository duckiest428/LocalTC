"""The rest of the frequency: other aircraft being cleared, checking in and reading back.

A frequency with only one aircraft on it is a strange, private place. Real ones carry a steady murmur of
other flights: "Westjet 452, runway 06L, cleared to land, wind 060 at 6" and "cleared to land 06L, Westjet
452". This makes that murmur, and nothing more: there is no traffic behind it, nothing is tracked, and the
engine never waits for, or answers, any of it (``RadioChatter`` events, not transmissions to the pilot).

The words are the same templates ATC uses with the pilot, filled in with the airport's own runway in use,
wind and taxiways. The aircraft are the sim's own: the AI traffic around, by the callsigns it carries, each
told what fits what it's doing (the one at a gate is pushed back, the one low on final is cleared to land).
With none of it around, the frequency is quiet: nobody is made up. The engine decides when (a quiet
frequency, a gap of a minute or two that depends on how busy that kind of controller is) and who is around;
this module decides what is said.
"""

import random
import zlib
from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core.phraseology import TemplateLibrary, speech
from localtc.atc_core.values import Callsign, Wind

# How often each kind of controller is heard talking to somebody else: (shortest, longest) gap, seconds.
GAP_S: dict[str, tuple[float, float]] = {
    "clearance": (120.0, 300.0), "ground": (45.0, 110.0), "tower": (35.0, 90.0), "departure": (45.0, 100.0),
    "approach": (40.0, 95.0), "center": (70.0, 170.0),
}


@dataclass(frozen=True)
class Line:
    speaker: str  # "atc" or "pilot"
    callsign: str  # as displayed
    text: str
    spoken: str


# What another aircraft is doing, as the engine sees it in the sim's traffic.
DOINGS = ("at_gate", "taxiing_out", "taxiing_in", "holding", "landing", "climbing", "descending", "level")


@dataclass(frozen=True)
class Other:
    """A real aircraft in the sim that this controller could be talking to."""

    object_id: int
    callsign: Callsign
    doing: str  # one of DOINGS
    level_ft: int = 0  # its altitude, to the nearest thousand (airborne)


@dataclass
class Scene:
    """What the other flights can be told here: the airport's runway in use and taxiways, the wind, altitudes."""

    controller: str
    station: str
    runway: str | None = None
    wind: Wind | None = None
    routes: list[tuple[str, ...]] = field(default_factory=list)  # real taxi routes from gates to the runway in use
    handoff: tuple[str, float] | None = None  # (station, MHz) the next controller, for "contact ..."
    icao_region: bool = False
    level_ft: int = 35000  # around where the pilot's own flight is: centres work flights near its level
    exclude: str = ""  # the pilot's own callsign: never borrowed
    others: list[Other] = field(default_factory=list)  # the sim's aircraft this controller is working


class Chatter:
    def __init__(self, library: TemplateLibrary, seed: int = 0) -> None:
        self.library = library
        self.rng = random.Random(seed or 1)
        self.last: Other | None = None  # who the last exchange was with

    def gap(self, controller: str) -> float:
        low, high = GAP_S.get(controller, (60.0, 150.0))
        return self.rng.uniform(low, high)

    def exchange(self, scene: Scene) -> list[Line]:
        """One exchange between the controller and somebody else: ATC's call and the readback (or the other way
        round, for a check-in). Empty if there's nothing sensible to say here."""
        choices = [(other, option) for other in scene.others if other.callsign.ident != scene.exclude
                   for option in self._options(scene, other)]
        if not choices:
            self.last = None
            return []
        other, (kind, iid, slots) = self.rng.choice(choices)
        self.last = other
        cs = other.callsign
        slots = {**slots, "callsign": cs}
        shown = cs.telephony + " " + cs.flight_number if cs.is_airline else cs.ident
        atc = self.library.render(iid, slots, rng=self.rng)
        lines = []
        if kind == "checkin":  # the aircraft calls first: "Montreal Centre, Westjet 452, flight level 350"
            level = slots["level"] // 100
            text = f"{scene.station}, {shown}, flight level {level}"
            spoken = f"{scene.station}, {speech.callsign(cs)}, flight level {_digit_words(level)}"
            lines.append(Line("pilot", shown, text, spoken))
            lines.append(Line("atc", shown, atc.text, atc.spoken))
            return lines
        lines.append(Line("atc", shown, atc.text, atc.spoken))
        readback = self.library.get(iid).pilot_readback
        if iid == "ground.pushback":  # the template has no readback: crews say it anyway
            spoken = f"Push and start, tail {slots['turn']}, {speech.callsign(cs)}"
            lines.append(Line("pilot", shown, f"Push and start, tail {slots['turn']}, {shown}", spoken))
        elif readback:
            spoken = self.library.pilot_readback(iid, slots)
            lines.append(Line("pilot", shown, _sentence(spoken), spoken))
        return lines

    def _options(self, scene: Scene, other: Other) -> list[tuple[str, str, dict[str, Any]]]:
        """What this controller would say to ``other``, given what it's doing."""
        rng = self.rng
        c, doing = scene.controller, other.doing
        out: list[tuple[str, str, dict[str, Any]]] = []
        runway, wind = scene.runway, scene.wind
        route = rng.choice(scene.routes) if scene.routes else ()
        if c == "ground":
            if doing == "at_gate":
                out.append(("atc", "ground.pushback", {"turn": rng.choice(("left", "right"))}))
            elif doing == "taxiing_out" and runway and route:
                out.append(("atc", "ground.taxi_out", {"runway": runway, "taxi_route": route}))
            elif doing == "taxiing_in" and route:
                out.append(("atc", "ground.taxi_in", {"taxi_route": tuple(reversed(route))}))  # the way back in
        elif c == "tower" and runway:
            if doing == "holding":
                out.append(("atc", "tower.takeoff", {"runway": runway}))
                out.append(("atc", "tower.luaw", {"runway": runway}))
            elif doing == "landing" and wind is not None:
                out.append(("atc", "tower.land", {"runway": runway, "wind": wind}))
        elif c in ("departure", "approach") and doing in ("climbing", "descending", "level"):
            if doing == "climbing":
                out.append(("atc", "common.climb", {"altitude": min(17000, other.level_ft + rng.choice((2000, 4000, 6000)))}))
            elif doing == "descending":
                out.append(("atc", "common.descend", {"altitude": max(2000, other.level_ft - rng.choice((2000, 3000, 4000)))}))
            if scene.handoff is not None and (doing != "climbing" or other.level_ft >= 10000):
                station, mhz = scene.handoff
                out.append(("atc", "common.contact", {"station": station, "frequency": mhz}))
        elif c == "center" and other.level_ft >= 10000:
            level = max(11000, min(41000, other.level_ft))
            if doing == "level":
                out.append(("checkin", "common.roger", {"level": level}))
            elif doing == "climbing":
                out.append(("atc", "common.climb", {"altitude": min(41000, level + rng.choice((2000, 4000)))}))
            else:
                out.append(("atc", "common.descend", {"altitude": max(11000, level - rng.choice((2000, 4000, 6000)))}))
            if scene.handoff is not None:
                station, mhz = scene.handoff
                out.append(("atc", "common.contact", {"station": station, "frequency": mhz}))
        return [(kind, iid, slots) for kind, iid, slots in out if "altitude" not in slots or slots["altitude"] >= 1000]


def _digit_words(n: int) -> str:
    names = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner")
    return " ".join(names[int(d)] for d in str(n))


def _sentence(spoken: str) -> str:
    return spoken[:1].upper() + spoken[1:] if spoken else spoken


def seed_for(callsign: str, seed: int) -> int:
    return seed or zlib.crc32(("chatter" + callsign).encode())


__all__ = ["DOINGS", "GAP_S", "Chatter", "Line", "Other", "Scene", "seed_for"]
