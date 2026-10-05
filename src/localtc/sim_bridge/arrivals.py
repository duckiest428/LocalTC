"""An airport's arrival procedures (STARs) from the sim's navdata, for the copilot to check the restrictions on them.

A request of its own (not part of the airport layout, which every airport fetch gets): the airport, its arrivals by
name, and under each the legs of its common part, its runway transitions and its enroute transitions. Every leg has its
fix, the altitude published there (``APPROACH_ALT_DESC``: 1 at, 2 at or above, 3 at or below, 4 between ALTITUDE1 and
ALTITUDE2, in metres) and the speed limit (knots, 0 none), as the SimConnect facility documentation gives them.

The replies come as a tree: each item names its parent (``parent_id``), so a leg is put under the arrival or the
transition it belongs to. Items are told apart by their type, else by their size. Only the named arrival is kept.
"""

import logging
import struct
from dataclasses import dataclass, field
from typing import Any

from localtc.sim_api import ArrivalData, ArrivalLeg
from localtc.sim_api.airport import FEET_PER_METER
from localtc.sim_bridge.facilities import RUNWAY_DESIGNATORS
from localtc.sim_bridge.protocol import FacilityData

log = logging.getLogger(__name__)

# SIMCONNECT_FACILITY_DATA_TYPE: the ones this request gets back.
T_AIRPORT, T_APPROACH_LEG, T_ARRIVAL, T_RUNWAY_TRANSITION, T_ENROUTE_TRANSITION = 0, 7, 11, 12, 13

LEG_FIELDS = ("TYPE", "FIX_ICAO", "FIX_LATITUDE", "FIX_LONGITUDE", "APPROACH_ALT_DESC", "ALTITUDE1", "ALTITUDE2",
              "SPEED_LIMIT")
LEG = struct.Struct("<i8sddifff")
LEG_SHORT = struct.Struct("<i8sifff")  # a sim that doesn't give the fix's position: matched to the flight plan's fixes
NAME8 = struct.Struct("<8s")
RUNWAY = struct.Struct("<ii")
DESCRIPTORS = {1: "at", 2: "above", 3: "below", 4: "between"}


def definition_lines() -> list[str]:
    """The strings passed to SimConnect_AddToFacilityDefinition, in order."""
    legs = ["OPEN APPROACH_LEG", *LEG_FIELDS, "CLOSE APPROACH_LEG"]
    return ["OPEN AIRPORT", "ICAO",
            "OPEN ARRIVAL", "NAME", *legs,
            "OPEN RUNWAY_TRANSITION", "RUNWAY_NUMBER", "RUNWAY_DESIGNATOR", *legs, "CLOSE RUNWAY_TRANSITION",
            "OPEN ENROUTE_TRANSITION", "NAME", *legs, "CLOSE ENROUTE_TRANSITION",
            "CLOSE ARRIVAL", "CLOSE AIRPORT"]


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()


def _feet(metres: float) -> int:
    return int(round(metres * FEET_PER_METER / 100.0) * 100)


@dataclass
class _Node:
    kind: str  # airport, arrival, runway, enroute, leg
    parent: int
    name: str = ""
    leg: ArrivalLeg | None = None
    order: int = 0


@dataclass
class ArrivalAssembler:
    icao: str
    name: str  # the arrival wanted ("ANJLL4")
    started_t: float = 0.0
    nodes: dict[int, _Node] = field(default_factory=dict)
    seen: int = 0

    def add(self, msg: FacilityData) -> None:
        for payload in (msg.payload, msg.payload_bool8):
            node = self._read(msg, payload)
            if node is not None:
                node.order = self.seen
                self.seen += 1
                self.nodes[msg.unique_id] = node
                return

    def _read(self, msg: FacilityData, payload: bytes) -> _Node | None:
        size = len(payload)
        if msg.type == T_APPROACH_LEG or size in (LEG.size, LEG_SHORT.size):
            if size == LEG.size:
                kind, fix, lat, lon, desc, alt1, alt2, speed = LEG.unpack(payload)
            elif size == LEG_SHORT.size:
                (kind, fix, desc, alt1, alt2, speed), lat, lon = LEG_SHORT.unpack(payload), None, None
            else:
                return None
            descriptor = DESCRIPTORS.get(desc, "")
            high, low = (max(alt1, alt2), min(alt1, alt2)) if descriptor == "between" else (alt1, 0.0)
            ok = lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0.0, 0.0)
            leg = ArrivalLeg(fix=_text(fix), lat=round(lat, 6) if ok else None, lon=round(lon, 6) if ok else None,
                             altitude=descriptor, alt1_ft=_feet(high) if descriptor else 0,
                             alt2_ft=_feet(low) if descriptor == "between" else 0,
                             speed_kt=int(round(speed)) if 0 < speed < 400 else 0)
            return _Node("leg", msg.parent_id, leg=leg)
        if size != NAME8.size:
            return None
        parent = self.nodes.get(msg.parent_id)
        if msg.type == T_RUNWAY_TRANSITION or (msg.type not in (T_ENROUTE_TRANSITION, T_ARRIVAL, T_AIRPORT)
                                               and parent is not None and parent.kind == "arrival"
                                               and not _printable(payload)):
            number, designator = RUNWAY.unpack(payload)
            return _Node("runway", msg.parent_id, name=f"RW{number:02d}{RUNWAY_DESIGNATORS.get(designator, '')}")
        name = _text(payload)
        if msg.type == T_AIRPORT or (msg.type not in (T_ARRIVAL, T_ENROUTE_TRANSITION) and parent is None and not self.nodes):
            return _Node("airport", msg.parent_id, name=name)
        if msg.type == T_ARRIVAL or (parent is not None and parent.kind == "airport"):
            return _Node("arrival", msg.parent_id, name=name)
        return _Node("enroute", msg.parent_id, name=name)

    def build(self, t: float) -> ArrivalData:
        """The wanted arrival's legs, in the order the sim gave them, each with the transition it's on."""
        wanted = self.name.upper()
        arrivals = {uid: n for uid, n in self.nodes.items() if n.kind == "arrival"
                    and (n.name.upper() == wanted or _same_procedure(n.name, wanted))}
        legs: list[ArrivalLeg] = []
        for _, node in sorted(self.nodes.items(), key=lambda kv: kv[1].order):
            if node.kind != "leg" or node.leg is None:
                continue
            owner = self.nodes.get(node.parent)
            if owner is None:
                continue
            if owner.kind == "arrival" and node.parent in arrivals:
                legs.append(node.leg)
            elif owner.kind in ("runway", "enroute") and owner.parent in arrivals:
                legs.append(_with(node.leg, transition=owner.name))
        return ArrivalData(t=t, airport=self.icao, name=self.name, legs=tuple(legs))


def _with(leg: ArrivalLeg, **changes: Any) -> ArrivalLeg:
    from msgspec.structs import replace

    return replace(leg, **changes)


def _printable(raw: bytes) -> bool:
    text = raw.split(b"\0", 1)[0]
    return bool(text) and all(48 <= c <= 57 or 65 <= c <= 90 for c in text)


def _same_procedure(sim: str, plan: str) -> bool:
    """SimBrief's "ANJLL4" and the sim's "ANJL4" (navdata names are cut to five characters, then the number)."""
    sim, plan = sim.upper(), plan.upper()
    if len(plan) < 3 or len(sim) < 3 or not sim[-1].isdigit() or not plan[-1].isdigit() or sim[-1] != plan[-1]:
        return False
    return plan[:-1].startswith(sim[:-1]) or sim[:-1].startswith(plan[:-1])
