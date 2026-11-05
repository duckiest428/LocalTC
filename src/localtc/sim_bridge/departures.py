"""An airport's departure procedures (SIDs) from the sim's navdata: which runways each is flown from and where it goes, so ATC
never clears a flight on a SID that doesn't leave from the runway it's given.

The same kind of request as ``arrivals``: the airport, its departures by name, under each its runway transitions (the runways
it's flown from) and enroute transitions, and the fixes of each. The replies come as a tree, each item naming its parent.
"""

import struct
from dataclasses import dataclass, field

from localtc.sim_api import DepartureProcedure, DeparturesData
from localtc.sim_bridge.arrivals import T_AIRPORT, T_ENROUTE_TRANSITION, T_RUNWAY_TRANSITION, _printable, _text
from localtc.sim_bridge.facilities import RUNWAY_DESIGNATORS
from localtc.sim_bridge.protocol import FacilityData

T_DEPARTURE = 10
LEG = struct.Struct("<i8s")  # TYPE, FIX_ICAO: a size nothing else here has
NAME8 = struct.Struct("<8s")
RUNWAY = struct.Struct("<ii")


def definition_lines() -> list[str]:
    """The strings passed to SimConnect_AddToFacilityDefinition, in order."""
    legs = ["OPEN APPROACH_LEG", "TYPE", "FIX_ICAO", "CLOSE APPROACH_LEG"]
    return ["OPEN AIRPORT", "ICAO",
            "OPEN DEPARTURE", "NAME", *legs,
            "OPEN RUNWAY_TRANSITION", "RUNWAY_NUMBER", "RUNWAY_DESIGNATOR", *legs, "CLOSE RUNWAY_TRANSITION",
            "OPEN ENROUTE_TRANSITION", "NAME", *legs, "CLOSE ENROUTE_TRANSITION",
            "CLOSE DEPARTURE", "CLOSE AIRPORT"]


@dataclass
class _Node:
    kind: str  # airport, departure, runway, enroute, leg
    parent: int
    name: str = ""


@dataclass
class DepartureAssembler:
    icao: str
    started_t: float = 0.0
    nodes: dict[int, _Node] = field(default_factory=dict)

    def add(self, msg: FacilityData) -> None:
        for payload in (msg.payload, msg.payload_bool8):
            node = self._read(msg, payload)
            if node is not None:
                self.nodes[msg.unique_id] = node
                return

    def _read(self, msg: FacilityData, payload: bytes) -> _Node | None:
        size = len(payload)
        if size == LEG.size:
            return _Node("leg", msg.parent_id, name=_text(LEG.unpack(payload)[1]))
        if size != NAME8.size:
            return None
        parent = self.nodes.get(msg.parent_id)
        if msg.type == T_RUNWAY_TRANSITION or (msg.type not in (T_ENROUTE_TRANSITION, T_DEPARTURE, T_AIRPORT)
                                               and parent is not None and parent.kind == "departure"
                                               and not _printable(payload)):
            number, designator = RUNWAY.unpack(payload)
            return _Node("runway", msg.parent_id, name=f"{number:02d}{RUNWAY_DESIGNATORS.get(designator, '')}")
        name = _text(payload)
        if msg.type == T_AIRPORT or (msg.type not in (T_DEPARTURE, T_ENROUTE_TRANSITION) and parent is None and not self.nodes):
            return _Node("airport", msg.parent_id, name=name)
        if msg.type == T_DEPARTURE or (parent is not None and parent.kind == "airport"):
            return _Node("departure", msg.parent_id, name=name)
        return _Node("enroute", msg.parent_id, name=name)

    def build(self, t: float) -> DeparturesData:
        out: list[DepartureProcedure] = []
        for uid, node in self.nodes.items():
            if node.kind != "departure":
                continue
            runways, fixes, enroute = [], [], []
            for child_id, child in self.nodes.items():
                if child.parent != uid:
                    continue
                if child.kind == "runway":
                    runways.append(child.name)
                    fixes += [leg.name for leg in self.nodes.values() if leg.kind == "leg" and leg.parent == child_id]
                elif child.kind == "enroute":
                    enroute.append(child.name)
                    fixes += [leg.name for leg in self.nodes.values() if leg.kind == "leg" and leg.parent == child_id]
                elif child.kind == "leg":
                    fixes.append(child.name)
            out.append(DepartureProcedure(name=node.name, runways=tuple(runways), enroute=tuple(enroute),
                                          fixes=tuple(dict.fromkeys(f for f in fixes if f))))
        return DeparturesData(t=t, airport=self.icao, departures=tuple(out))
