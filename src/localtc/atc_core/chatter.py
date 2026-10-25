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
from dataclasses import dataclass, field, replace
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
    ident: str = ""  # its callsign as filed ("RYR123"): the radio log colours an airline by it


# What another aircraft is doing, as the engine sees it in the sim's traffic.
DOINGS = ("at_gate", "taxiing_out", "taxiing_in", "holding", "landing", "climbing", "descending", "level")


@dataclass(frozen=True)
class Other:
    """A real aircraft in the sim that this controller could be talking to."""

    object_id: int
    callsign: Callsign
    doing: str  # one of DOINGS
    level_ft: int = 0  # its altitude, to the nearest thousand (airborne)
    # Landing: the runway it's lined up with (not the one in use: Heathrow's A321 was on 27L); departing: the one ATC
    # gave it (``AtcEngine.runway_for``)
    runway: str = ""
    # Arriving, on approach's frequency: the next vector the pilot's own flight would get from where it is
    # (vectors.vector): the leg, the magnetic heading, which way to turn to it. Empty: not an arrival here.
    leg: str = ""
    heading: int = 0
    turn: str = ""
    final_nm: float | None = None  # on the final already, this far out


@dataclass
class Scene:
    """What the other flights can be told here: the airport's runway in use and taxiways, the wind, altitudes."""

    controller: str
    station: str
    runway: str | None = None
    wind: Wind | None = None
    routes: list[tuple[str, ...]] = field(default_factory=list)  # real taxi routes from gates to the runway in use
    routes_by_runway: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)  # ... to each departure runway
    handoff: tuple[str, float] | None = None  # (station, MHz) the next controller, for "contact ..."
    icao_region: bool = False
    level_ft: int = 35000  # around where the pilot's own flight is: centres work flights near its level
    exclude: str = ""  # the pilot's own callsign: never borrowed
    others: list[Other] = field(default_factory=list)  # the sim's aircraft this controller is working
    approach: Any = None  # approach's arrivals: the approach in use (an Approach), as the pilot's own flight gets it
    approach_alt: int = 3000  # where the final is joined
    atis: str = ""  # the arrival ATIS letter the arrivals check in with


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
            return [replace(line, ident=cs.ident) for line in lines]
        if kind == "arrival_checkin":  # "Detroit Approach, Delta 1529, one one thousand, information Bravo"
            level = other.level_ft
            atis = f", information {scene.atis}" if scene.atis else ""
            said = f", information {speech.letter(scene.atis)}" if scene.atis else ""
            text = f"{scene.station}, {shown}, {speech.altitude_display(level)}{atis}"
            spoken = f"{scene.station}, {speech.callsign(cs)}, {speech.altitude(level)}{said}"
            lines.append(Line("pilot", shown, text, spoken))
            lines.append(Line("atc", shown, atc.text, atc.spoken))
            if self.library.get(iid).pilot_readback:
                said = self.library.pilot_readback(iid, slots)
                lines.append(Line("pilot", shown, _sentence(said), said))
            return [replace(line, ident=cs.ident) for line in lines]
        if kind == "request":  # the pilot asks first: "Phoenix Ground, United 709, gate B22, request pushback"
            text, spoken = self._request(scene, other, iid, slots, shown)
            lines.append(Line("pilot", shown, text, spoken))
        lines.append(Line("atc", shown, atc.text, atc.spoken))
        if (said := readback_words(iid, slots, cs, shown, self._habit(cs))) is not None:
            lines.append(Line("pilot", shown, *said))
        elif self.library.get(iid).pilot_readback:
            spoken = self.library.pilot_readback(iid, slots)
            lines.append(Line("pilot", shown, _sentence(spoken), spoken))
        return [replace(line, ident=cs.ident) for line in lines]

    def _habit(self, cs: Callsign) -> random.Random:
        """How this crew reads back: each its own way of putting it most of the time (seeded by its callsign), and now
        and then another, so no two crews sound alike and none sounds the same every time."""
        seed = zlib.crc32(cs.ident.encode()) if self.rng.random() < 0.7 else self.rng.randrange(1 << 30)
        return random.Random(seed)

    def _request(self, scene: Scene, other: Other, iid: str, slots: dict[str, Any], shown: str) -> tuple[str, str]:
        """The pilot's call before the instruction it gets: (text, spoken)."""
        cs, rng = other.callsign, self.rng
        station = scene.station if rng.random() < 0.8 else scene.station.split()[-1]  # "Phoenix Ground", "Ground"
        if iid.startswith("ground.pushback"):
            ask = rng.choice(("request pushback", "ready to push", "request push and start", "ready for pushback"))
        elif iid.startswith("ground.taxi_in"):
            ask = rng.choice(("clear of the runway", "clear of the runway, taxi to the gate", "with you, clear of the runway"))
        elif iid.startswith("tower."):
            r = slots.get("runway", "")
            asks = (f"holding short runway {r}, ready", "ready for departure", f"ready, runway {r}",
                    f"holding short {r}, ready to go")
            text_ask = rng.choice(asks)
            spoken_ask = text_ask.replace(f"runway {r}", f"runway {speech.runway(r)}").replace(f"short {r}",
                                                                                              f"short {speech.runway(r)}")
            spoken_ask = spoken_ask.replace(f"ready, runway {r}", f"ready, runway {speech.runway(r)}")
            return f"{station}, {shown}, {text_ask}", f"{station}, {speech.callsign(cs)}, {spoken_ask}"
        else:
            ask = rng.choice(("ready to taxi", "ready to taxi with the numbers", "request taxi"))
        return f"{station}, {shown}, {ask}", f"{station}, {speech.callsign(cs)}, {ask}"

    def _options(self, scene: Scene, other: Other) -> list[tuple[str, str, dict[str, Any]]]:
        """What this controller would say to ``other``, given what it's doing."""
        rng = self.rng
        c, doing = scene.controller, other.doing
        out: list[tuple[str, str, dict[str, Any]]] = []
        # Its own runway where ATC has given it one (several in use: not everybody's the pilot's)
        runway, wind = (other.runway if other.doing != "landing" else "") or scene.runway, scene.wind
        routes = scene.routes_by_runway.get(runway) or scene.routes
        route = rng.choice(routes) if routes else ()
        if c == "ground":
            # A crew calls ground before it moves: pushed back once it asks, taxied once it says it's ready. One
            # already rolling had its taxi clearance before it moved: what's left to tell it is the hold short, or
            # the tower (a taxi clearance given to one already half way out, unasked, was nothing like a frequency).
            if doing == "at_gate":
                out.append(("request", "ground.pushback", {"turn": rng.choice(("left", "right"))}))
            elif doing == "taxiing_out" and scene.handoff is not None:
                station, mhz = scene.handoff
                out.append(("atc", "ground.handoff_tower", {"station": station, "frequency": mhz}))
            elif doing == "taxiing_out" and runway and route:
                out.append(("request", "ground.taxi_out", {"runway": runway, "taxi_route": route}))
            elif doing == "taxiing_in" and route:
                out.append(("request", "ground.taxi_in", {"taxi_route": tuple(reversed(route))}))  # the way back in
        elif c == "tower" and runway:
            if doing == "holding":
                out.append(("request", "tower.takeoff", {"runway": runway}))
                out.append(("request", "tower.luaw", {"runway": runway}))
            elif doing == "landing" and wind is not None:
                out.append(("atc", "tower.land", {"runway": other.runway or runway, "wind": wind}))
        elif c == "approach" and other.leg and scene.approach is not None and doing in ("descending", "level"):
            out += self._arrival(scene, other)
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
                # Answered the way the pilot's own check-in is: mostly "roger" (its level known from the last
                # controller), now and then "maintain FL350", and now and then a step climb.
                checkin = "center.checkin_level" if rng.random() < 0.25 else "center.checkin_ack"
                out.append(("checkin", checkin, {"level": level, "station": scene.station, "altitude": level}))
                if level <= 39000 and rng.random() < 0.4:
                    out.append(("atc", "common.climb", {"altitude": level + 2000}))
            elif doing == "climbing":
                out.append(("atc", "common.climb", {"altitude": min(41000, level + rng.choice((2000, 4000)))}))
            else:
                out.append(("atc", "common.descend", {"altitude": max(11000, level - rng.choice((2000, 4000, 6000)))}))
            if scene.handoff is not None:
                station, mhz = scene.handoff
                out.append(("atc", "common.contact", {"station": station, "frequency": mhz}))
        return [(kind, iid, slots) for kind, iid, slots in out if "altitude" not in slots or slots["altitude"] >= 1000]

    def _arrival(self, scene: Scene, other: Other) -> list[tuple[str, str, dict[str, Any]]]:
        """An arrival on approach's frequency, worked the way the pilot's own flight is: checked in with the approach to
        expect, vectored (the heading the vectors give from where it is), cleared for the approach, sent to tower.
        Before, approach only ever told the others "descend and maintain", and they didn't feel there."""
        approach, floor, level = scene.approach, scene.approach_alt, other.level_ft
        lower = max(floor, level - self.rng.choice((1000, 2000, 3000)))
        if other.final_nm is not None:  # on the final: tower's now
            if scene.handoff is not None:
                station, mhz = scene.handoff
                return [("atc", "approach.handoff_tower", {"station": station, "frequency": mhz})]
            return []
        out: list[tuple[str, str, dict[str, Any]]] = []
        heading = {"heading": other.heading, "turn": other.turn or "left"}
        if other.leg == "intercept":
            kind = getattr(approach, "kind", "")
            iid = "approach.intercept_visual" if kind == "VISUAL" else \
                "approach.intercept_cleared" if kind in ("ILS", "LOC", "LDA", "SDF") else "approach.intercept_course"
            out.append(("atc", iid, {**heading, "altitude": max(floor, min(level, floor + 1000)), "approach": approach}))
            return out
        if level >= floor + 4000:
            out.append(("arrival_checkin", "approach.descend", {"altitude": lower, "approach": approach,
                                                                "station": scene.station}))
        if lower < level:
            out.append(("atc", "approach.turn_descend", {**heading, "altitude": lower}))
        out.append(("atc", "approach.heading" if other.leg in ("base", "downwind") else "approach.turn",
                    {**heading, "approach": approach}))
        return out


def readback_words(iid: str, slots: dict[str, Any], cs: Callsign, shown: str, rng: random.Random
                   ) -> tuple[str, str] | None:
    """A crew's readback of ``iid`` in one of the ways crews really say it, (text, spoken): "Runway two six, taxi via
    charlie", "Two six via charlie", "Taxi two six, charlie, bravo"; "Down to one one thousand", "Descend and
    maintain one one thousand". None: the template's own. Every readback in the same order and the same words was
    what made the frequency sound made up."""
    family = iid.split(".", 1)[1] if "." in iid else iid
    said_cs = speech.callsign(cs)
    forms: list[tuple[str, str]] = []  # with {cs} for the callsign

    def both(text: str, spoken: str) -> None:
        forms.append((text, spoken))

    if iid in ("ground.taxi_out", "ground.taxi_out_at") and slots.get("taxi_route"):
        r, route = slots["runway"], tuple(slots["taxi_route"])
        rs, ts = speech.runway(r), speech.taxi_route(route)
        rt = ", ".join(route)
        both(f"Runway {r}, taxi via {rt}, {{cs}}", f"Runway {rs}, taxi via {ts}, {{cs}}")
        both(f"Taxi {r} via {rt}, {{cs}}", f"Taxi {rs} via {ts}, {{cs}}")
        both(f"{r} via {rt}, {{cs}}", f"{rs} via {ts}, {{cs}}")
        both(f"Via {rt} to {r}, {{cs}}", f"Via {ts} to {rs}, {{cs}}")
        both(f"{{cs}}, runway {r} via {rt}", f"{{cs}}, runway {rs} via {ts}")
    elif iid == "ground.taxi_in" and slots.get("taxi_route"):
        route = tuple(slots["taxi_route"])
        rt, ts = ", ".join(route), speech.taxi_route(route)
        both(f"Taxi to parking via {rt}, {{cs}}", f"Taxi to parking via {ts}, {{cs}}")
        both(f"{rt} to the gate, {{cs}}", f"{ts} to the gate, {{cs}}")
        both(f"Via {rt}, {{cs}}", f"Via {ts}, {{cs}}")
        both(f"In via {rt}, {{cs}}", f"In via {ts}, {{cs}}")
    elif iid.startswith("ground.pushback") and "turn" in slots:
        t = slots["turn"]
        for text in (f"Push and start, tail {t}, {{cs}}", f"Push approved, tail {t}, {{cs}}",
                     f"Pushing back, tail {t}, {{cs}}", f"Tail {t}, {{cs}}", f"Push and start approved, {{cs}}"):
            both(text, text)
    elif iid == "ground.handoff_tower" or family in ("contact", "handoff_tower", "handoff_departure", "handoff_center"):
        station, mhz = str(slots.get("station", "")), slots.get("frequency")
        if not mhz:
            return None
        f, fs = speech.frequency_display(mhz), speech.frequency(mhz)
        last = station.split()[-1] if station else ""
        both(f"{station} {f}, {{cs}}", f"{station} {fs}, {{cs}}")
        both(f"Over to {last.lower()} {f}, {{cs}}", f"Over to {last.lower()} {fs}, {{cs}}")
        both(f"{f}, good day, {{cs}}", f"{fs}, good day, {{cs}}")
        both(f"Switching to {last.lower()}, {{cs}}", f"Switching to {last.lower()}, {{cs}}")
        both(f"{f}, {{cs}}, see ya", f"{fs}, {{cs}}, see ya")
    elif iid in ("common.climb", "common.descend") and "altitude" in slots:
        a = int(slots["altitude"])
        ad, as_ = speech.altitude_display(a), speech.altitude(a)
        up = iid == "common.climb"
        verb, short, ing = ("Climb", "Up to", "Climbing") if up else ("Descend", "Down to", "Descending")
        both(f"{verb} and maintain {ad}, {{cs}}", f"{verb} and maintain {as_}, {{cs}}")
        both(f"{short} {ad}, {{cs}}", f"{short} {as_}, {{cs}}")
        both(f"{ing} {ad}, {{cs}}", f"{ing} {as_}, {{cs}}")
        both(f"{ad}, {{cs}}", f"{as_}, {{cs}}")
        both(f"{verb} {ad}, {{cs}}", f"{verb} {as_}, {{cs}}")
    elif iid in ("approach.heading", "approach.turn") and "heading" in slots:
        h, turn = int(slots["heading"]), slots.get("turn", "")
        hd, hs = f"{h:03d}", speech.heading(h)
        both(f"Heading {hd}, {{cs}}", f"Heading {hs}, {{cs}}")
        if turn:
            both(f"{turn.capitalize()} {hd}, {{cs}}", f"{turn.capitalize()} {hs}, {{cs}}")
            both(f"Turn {turn} heading {hd}, {{cs}}", f"Turn {turn} heading {hs}, {{cs}}")
            both(f"{turn.capitalize()} heading {hd}, {{cs}}", f"{turn.capitalize()} heading {hs}, {{cs}}")
    elif iid == "tower.takeoff" and "runway" in slots:
        r = slots["runway"]
        rs = speech.runway(r)
        both(f"Cleared for takeoff runway {r}, {{cs}}", f"Cleared for takeoff runway {rs}, {{cs}}")
        both(f"Runway heading, cleared for takeoff {r}, {{cs}}", f"Runway heading, cleared for takeoff {rs}, {{cs}}")
        both(f"Cleared to go, {r}, {{cs}}", f"Cleared to go, {rs}, {{cs}}")
        both(f"Runway {r}, cleared for takeoff, {{cs}}", f"Runway {rs}, cleared for takeoff, {{cs}}")
    elif iid == "tower.land" and "runway" in slots:
        r = slots["runway"]
        rs = speech.runway(r)
        both(f"Cleared to land runway {r}, {{cs}}", f"Cleared to land runway {rs}, {{cs}}")
        both(f"Cleared to land {r}, {{cs}}", f"Cleared to land {rs}, {{cs}}")
        both("Cleared to land, {cs}", "Cleared to land, {cs}")
        both(f"{{cs}}, cleared to land {r}", f"{{cs}}, cleared to land {rs}")
    elif iid == "tower.luaw" and "runway" in slots:
        r = slots["runway"]
        rs = speech.runway(r)
        both(f"Line up and wait {r}, {{cs}}", f"Line up and wait {rs}, {{cs}}")
        both(f"Runway {r}, line up and wait, {{cs}}", f"Runway {rs}, line up and wait, {{cs}}")
        both(f"Lining up {r}, {{cs}}", f"Lining up {rs}, {{cs}}")
    if not forms:
        return None
    text, spoken = rng.choice(forms)
    return text.replace("{cs}", shown), spoken.replace("{cs}", said_cs)


def _digit_words(n: int) -> str:
    names = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner")
    return " ".join(names[int(d)] for d in str(n))


def _sentence(spoken: str) -> str:
    return spoken[:1].upper() + spoken[1:] if spoken else spoken


def seed_for(callsign: str, seed: int) -> int:
    return seed or zlib.crc32(("chatter" + callsign).encode())


__all__ = ["DOINGS", "GAP_S", "Chatter", "Line", "Other", "Scene", "seed_for"]
