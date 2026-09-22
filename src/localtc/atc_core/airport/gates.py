"""Gates and parking: which stand ATC sends an arrival to, and which one a departure starts from.

The sim's parking spots carry a name ("GATE B 25", "PARKING 3", "S PARKING 2") and a kind (gate_medium,
ramp_ga_small, ramp_cargo, fuel, vehicle). Airliners go to a gate, heavies to a heavy gate, everyone else
to a GA ramp. A spot with an aircraft sitting on it is taken.
"""

import math
import random
import zlib
from collections.abc import Iterable
from dataclasses import dataclass

from localtc.atc_core.airport.geometry import AirportGeometry
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
    label: str  # "B25", "3", "S2"

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


def gates(geometry: AirportGeometry) -> list[Gate]:
    return [g for s in geometry.airport.parking if (g := parse(s)) is not None]


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
           seed: str) -> Gate | None:
    """A free stand for this aircraft, picked the same way every time the same flight replays. An airliner
    with no free gate of its size gets any free gate; if the whole field is full, nothing is assigned and
    ground just says "taxi to parking"."""
    traffic = list(traffic)
    heavy = airline and is_heavy(aircraft_type)
    everything = gates(geometry)
    free = [g for g in everything if not occupied(g, geometry, traffic)]
    choices = [g for g in free if suitable(g, airline=airline, heavy=heavy)]
    if not choices and airline:
        choices = [g for g in free if g.word == "gate"]
    if not choices:
        return None
    choices.sort(key=lambda g: g.index)
    return random.Random(zlib.crc32(f"gate{geometry.airport.icao}{seed}".encode())).choice(choices)


def parked_at(geometry: AirportGeometry, lat: float, lon: float) -> Gate | None:
    """The stand an aircraft is sitting on, if any."""
    here = geometry.xy(lat, lon)
    near = [(math.dist(here, geometry.xy(g.spot.lat, g.spot.lon)), g) for g in gates(geometry)]
    near = [(d, g) for d, g in near if d <= max(g.spot.radius_m, NEAR_SPOT_M)]
    return min(near, key=lambda dg: dg[0])[1] if near else None
