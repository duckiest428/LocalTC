"""The rest of the frequency: other aircraft being cleared, checking in and reading back.

A frequency with only one aircraft on it is a strange, private place. Real ones carry a steady murmur of
other flights: "Westjet 452, runway 06L, cleared to land, wind 060 at 6" and "cleared to land 06L, Westjet
452". This makes that murmur, and nothing more: there is no traffic behind it, nothing is tracked, and the
engine never waits for, or answers, any of it (``RadioChatter`` events, not transmissions to the pilot).

The words are the same templates ATC uses with the pilot, filled in with the airport's own runway in use,
wind and taxiways, and callsigns of airlines that fly there. The engine decides when (a quiet frequency, a
gap of a minute or two that depends on how busy that kind of controller is); this module decides what.
"""

import random
import zlib
from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core.phraseology import TemplateLibrary, speech
from localtc.atc_core.values import Callsign, Wind

# Airlines heard on North American frequencies, and on European and other ICAO ones.
AMERICAS = ("AAL", "ACA", "ASA", "DAL", "FFT", "JBU", "NKS", "SKW", "SWA", "UAL", "WJA", "JZA", "POE", "FDX",
            "UPS", "ENY", "RPA", "ROU", "TSC", "FLE", "AAY", "SCX", "EDV")
EUROPE = ("BAW", "DLH", "AFR", "KLM", "EZY", "RYR", "SWR", "AUA", "IBE", "VLG", "SAS", "TAP", "EIN", "WZZ",
          "EWG", "FIN", "TRA", "CFG", "AEE", "LOT", "UAE", "QTR", "THY")
GA_CHANCE = 0.15  # at an airport, now and then a light aircraft rather than an airliner

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


class Chatter:
    def __init__(self, library: TemplateLibrary, seed: int = 0) -> None:
        self.library = library
        self.rng = random.Random(seed or 1)

    def gap(self, controller: str) -> float:
        low, high = GAP_S.get(controller, (60.0, 150.0))
        return self.rng.uniform(low, high)

    def callsign(self, scene: Scene) -> Callsign:
        rng = self.rng
        at_airport = scene.controller in ("ground", "tower", "clearance")
        if at_airport and not scene.icao_region and rng.random() < GA_CHANCE:
            letters = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2))
            return Callsign.named(f"N{rng.randint(1, 9)}{rng.randint(10, 99)}{letters}")
        airlines = EUROPE if scene.icao_region else AMERICAS
        while True:
            code = rng.choice(airlines)
            number = str(rng.choice((rng.randint(10, 99), rng.randint(100, 999), rng.randint(1000, 4999))))
            cs = Callsign.named(f"{code}{number}")
            if cs.ident != scene.exclude:
                return cs

    def exchange(self, scene: Scene) -> list[Line]:
        """One exchange between the controller and somebody else: ATC's call and the readback (or the other way
        round, for a check-in). Empty if there's nothing sensible to say here."""
        options = self._options(scene)
        if not options:
            return []
        kind, iid, slots = self.rng.choice(options)
        cs = self.callsign(scene)
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

    def _options(self, scene: Scene) -> list[tuple[str, str, dict[str, Any]]]:
        rng = self.rng
        c = scene.controller
        out: list[tuple[str, str, dict[str, Any]]] = []
        runway, wind = scene.runway, scene.wind
        route = rng.choice(scene.routes) if scene.routes else ()
        if c == "ground":
            if runway and route:
                out.append(("atc", "ground.taxi_out", {"runway": runway, "taxi_route": route}))
            if route:
                out.append(("atc", "ground.taxi_in", {"taxi_route": tuple(reversed(route))}))  # the way back in
            out.append(("atc", "ground.pushback", {"turn": rng.choice(("left", "right"))}))
        elif c == "tower" and runway:
            out.append(("atc", "tower.takeoff", {"runway": runway}))
            out.append(("atc", "tower.luaw", {"runway": runway}))
            if wind is not None:
                out.append(("atc", "tower.land", {"runway": runway, "wind": wind}))
                out.append(("atc", "tower.land", {"runway": runway, "wind": wind}))  # landings are what towers say most
        elif c in ("departure", "approach"):
            if c == "departure":
                out.append(("atc", "common.climb", {"altitude": rng.choice((7000, 9000, 11000, 13000, 17000))}))
            else:
                out.append(("atc", "common.descend", {"altitude": rng.choice((3000, 4000, 5000, 6000, 8000))}))
            if scene.handoff is not None:
                station, mhz = scene.handoff
                out.append(("atc", "common.contact", {"station": station, "frequency": mhz}))
        elif c == "center":
            level = max(24000, min(41000, scene.level_ft + rng.choice((-4000, -2000, 2000, 4000))))
            out.append(("atc", "common.climb", {"altitude": level}))
            out.append(("atc", "common.descend", {"altitude": max(11000, level - 10000)}))
            out.append(("checkin", "common.roger", {"level": level}))
            if scene.handoff is not None:
                station, mhz = scene.handoff
                out.append(("atc", "common.contact", {"station": station, "frequency": mhz}))
        return out


def _digit_words(n: int) -> str:
    names = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner")
    return " ".join(names[int(d)] for d in str(n))


def _sentence(spoken: str) -> str:
    return spoken[:1].upper() + spoken[1:] if spoken else spoken


def seed_for(callsign: str, seed: int) -> int:
    return seed or zlib.crc32(("chatter" + callsign).encode())


__all__ = ["GAP_S", "Chatter", "Line", "Scene", "seed_for"]
