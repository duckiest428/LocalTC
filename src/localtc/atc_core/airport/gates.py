"""Gates and parking: which stand ATC sends an arrival to, and which one a departure starts from.

The sim's parking spots carry a name ("GATE B 25", "PARKING 3", "S PARKING 2") and a kind (gate_medium,
ramp_ga_small, ramp_cargo, fuel, vehicle). Airliners go to a gate, heavies to a heavy gate, everyone else
to a GA ramp. A spot with an aircraft sitting on it is taken.

With the airport's real gates (``real_gates``, from OpenStreetMap), a stand takes the real gate's name ("Gate E9"
where the scenery says "GATE 88"), and an international flight goes to a gate that takes international arrivals.
"""

import math
import random
import zlib
from collections.abc import Iterable
from dataclasses import dataclass

from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.airport.real_gates import GateData, match
from localtc.atc_core.readback.normalize import Token
from localtc.sim_api import ParkingSpot, TrafficTarget

# Types that need a heavy gate. Matched against the start of the sim's model name ("B777", "A350-900").
HEAVY_TYPES = ("B74", "B77", "B78", "A33", "A34", "A35", "A38", "MD11", "DC10", "B747", "B777", "B787",
               "A330", "A340", "A350", "A380", "BOEING 747", "BOEING 777", "BOEING 787", "AIRBUS A3")
OCCUPIED_MIN_M = 15.0  # an aircraft this close to a spot's centre (or within its radius) has it
PARKED_KT = 3.0
NEAR_SPOT_M = 60.0  # the departure gate: the spot the aircraft is parked on, if it is on one


@dataclass(frozen=True)
class Gate:
    spot: ParkingSpot
    word: str  # "gate" or "parking"
    label: str  # "B25", "3", "S2": the real gate's ("E9") when the airport's real gates are known
    international: bool = False  # takes international arrivals (the real gates)
    real: bool = False  # named from the airport's real gates

    @property
    def index(self) -> int:
        return self.spot.index

    @property
    def display(self) -> str:
        return f"{self.word.capitalize()} {self.label}".strip()


def parse(spot: ParkingSpot) -> Gate | None:
    """A parking spot as ATC names it; None for spots nobody is sent to (fuel, vehicles)."""
    if spot.kind in ("fuel", "vehicle") or not spot.kind:
        return None
    tokens = spot.name.upper().split()
    word = "gate" if spot.kind.startswith("gate") or "GATE" in tokens else "parking"
    label = "".join(t for t in tokens if t not in ("GATE", "PARKING", "DOCK", "RAMP"))
    return Gate(spot, word, label)


_MATCHED: dict[tuple[int, str, int], dict] = {}  # the last matching (an airport's stands, its real gates)


def gates(geometry: AirportGeometry, real: GateData | None = None) -> list[Gate]:
    """The airport's stands; with ``real``, the gate stands named after the real gates they match."""
    parsed = [g for s in geometry.airport.parking if (g := parse(s)) is not None]
    if real is None or not real.gates:
        return parsed
    key = (id(geometry), geometry.icao + real.icao, len(real.gates))
    if key not in _MATCHED:
        spots = [(g.index, g.label, g.spot.lat, g.spot.lon) for g in parsed if g.word == "gate"]
        _MATCHED.clear()
        _MATCHED[key] = match(spots, real, geometry.xy)
    named_by = _MATCHED[key]
    return [Gate(g.spot, g.word, r.ref, r.international, True) if (r := named_by.get(g.index)) is not None else g
            for g in parsed]


def suitable(gate: Gate, *, airline: bool, heavy: bool) -> bool:
    kind = gate.spot.kind
    if heavy:
        return kind == "gate_heavy"
    if airline:
        return kind.startswith("gate")
    return kind.startswith("ramp_ga")


def is_heavy(aircraft_type: str) -> bool:
    model = aircraft_type.upper()
    return any(model.startswith(t) or t in model for t in HEAVY_TYPES)


def occupied(gate: Gate, geometry: AirportGeometry, traffic: Iterable[TrafficTarget]) -> bool:
    here = geometry.xy(gate.spot.lat, gate.spot.lon)
    reach = max(gate.spot.radius_m, OCCUPIED_MIN_M)
    return any(t.on_ground and t.gs_kt < PARKED_KT and math.dist(here, geometry.xy(t.lat, t.lon)) <= reach
               for t in traffic)


def assign(geometry: AirportGeometry, *, airline: bool, aircraft_type: str, traffic: Iterable[TrafficTarget],
           seed: str, real: GateData | None = None, international: bool = False) -> Gate | None:
    """A free stand for this aircraft, picked the same way every time the same flight replays. An airliner
    with no free gate of its size gets any free gate; if the whole field is full, nothing is assigned and
    ground just says "taxi to parking". With the real gates: a stand with a real gate's name over one the
    scenery numbered on its own, an international gate for an international flight, and a domestic one otherwise."""
    traffic = list(traffic)
    heavy = airline and is_heavy(aircraft_type)
    everything = gates(geometry, real)
    free = [g for g in everything if not occupied(g, geometry, traffic)]
    choices = [g for g in free if suitable(g, airline=airline, heavy=heavy)]
    if not choices and airline:
        choices = [g for g in free if g.word == "gate"]
    if not choices:
        return None
    usual = [g for g in choices if not odd_number(g, everything)]
    choices = usual or choices
    if airline:  # a real gate (E9), not a stand the real gates didn't name (the scenery's "88")
        choices = [g for g in choices if g.real] or choices
        if any(g.international for g in everything):
            choices = [g for g in choices if g.international == international] or choices
    choices.sort(key=lambda g: g.index)
    return random.Random(zlib.crc32(f"gate{geometry.airport.icao}{seed}".encode())).choice(choices)


def odd_number(gate: Gate, everything: list[Gate]) -> bool:
    """A stand numbered unlike the gates around it: Seattle's scenery has "B 241", "B 249", "B 251" among B1-B16,
    extra positions a controller wouldn't send anybody to by that name."""
    letters = "".join(c for c in gate.label if c.isalpha())[:1]
    digits = "".join(c for c in gate.label if c.isdigit())
    group = ["".join(c for c in g.label if c.isdigit()) for g in everything
             if g.word == gate.word and "".join(c for c in g.label if c.isalpha())[:1] == letters]
    short = sum(1 for d in group if 0 < len(d) <= 2)
    return len(digits) >= 3 and short > len(group) / 2


def parked_at(geometry: AirportGeometry, lat: float, lon: float, real: GateData | None = None) -> Gate | None:
    """The stand an aircraft is sitting on, if any."""
    here = geometry.xy(lat, lon)
    near = [(math.dist(here, geometry.xy(g.spot.lat, g.spot.lon)), g) for g in gates(geometry, real)]
    near = [(d, g) for d, g in near if d <= max(g.spot.radius_m, NEAR_SPOT_M)]
    return min(near, key=lambda dg: dg[0])[1] if near else None


def requested(tokens: list[Token]) -> str | None:
    """The gate a pilot asks for ("gate Echo 9", "taxi to B 25", "stand 46"): "E9", "B25", "46"; None."""
    for i, token in enumerate(tokens):
        if token.text not in ("gate", "stand", "to"):
            continue
        rest = tokens[i + 1:i + 3]
        if len(rest) == 2 and len(rest[0].text) == 1 and rest[0].text.isalpha() and rest[1].text.isdigit():
            return rest[0].text.upper() + rest[1].text
        if token.text in ("gate", "stand") and rest and rest[0].text.isdigit():
            return rest[0].text
    return None


def named(geometry: AirportGeometry, label: str, real: GateData | None = None) -> Gate | None:
    """The gate called ``label`` ("E9" is "GATE E 9" or "GATE E9", or the stand matched to the real gate E9), or None
    if there's none by that name."""
    want = label.upper().replace(" ", "")
    return next((g for g in gates(geometry, real) if g.word == "gate" and g.label.upper().replace(" ", "") == want), None)
