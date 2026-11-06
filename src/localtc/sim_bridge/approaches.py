"""An airport's instrument approaches from the sim's navdata, with their transitions: the way from a STAR's last fix to
the final. Denver's LONGZ arrival ends at KAILE, five miles off the final for 17R; the approach's transitions go on from
there to it, and with them ATC clears the approach instead of turning the flight onto headings.

The same kind of request as ``arrivals`` and ``departures``: the airport, each approach (its kind, suffix and runway),
under it its transitions (the fix each starts at, and their legs) and its final approach legs. The replies come as a
tree, each item naming its parent; they're told apart by their type, else by their size and what they're under.
"""

import struct
from dataclasses import dataclass, field

from localtc.sim_api import ApproachesData, ApproachFix, ApproachRoute
from localtc.sim_bridge.arrivals import _text
from localtc.sim_bridge.facilities import APPROACH_KINDS, RUNWAY_DESIGNATORS
from localtc.sim_bridge.protocol import FacilityData

T_AIRPORT, T_APPROACH, T_TRANSITION, T_LEG, T_FINAL_LEG = 0, 5, 6, 7, 8
APPROACH = struct.Struct("<iiii")  # TYPE, SUFFIX, RUNWAY_NUMBER, RUNWAY_DESIGNATOR
NAME8 = struct.Struct("<8s")  # the airport's ICAO, a transition's IAF
LEG = struct.Struct("<8sdd")  # FIX_ICAO, FIX_LATITUDE, FIX_LONGITUDE


def definition_lines() -> list[str]:
    """The strings passed to SimConnect_AddToFacilityDefinition, in order."""
    leg = ["FIX_ICAO", "FIX_LATITUDE", "FIX_LONGITUDE"]
    return ["OPEN AIRPORT", "ICAO",
            "OPEN APPROACH", "TYPE", "SUFFIX", "RUNWAY_NUMBER", "RUNWAY_DESIGNATOR",
            "OPEN APPROACH_TRANSITION", "IAF_ICAO", "OPEN APPROACH_LEG", *leg, "CLOSE APPROACH_LEG",
            "CLOSE APPROACH_TRANSITION",
            "OPEN FINAL_APPROACH_LEG", *leg, "CLOSE FINAL_APPROACH_LEG",
            "CLOSE APPROACH", "CLOSE AIRPORT"]


@dataclass
class _Node:
    kind: str  # airport, approach, transition, leg, final
    parent: int
    name: str = ""
    fix: ApproachFix | None = None
    order: int = 0
    approach: tuple[str, str, str] = ("", "", "")  # kind, runway, suffix


@dataclass
class ApproachAssembler:
    icao: str
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
        parent = self.nodes.get(msg.parent_id)
        if size == LEG.size:
            ident, lat, lon = LEG.unpack(payload)
            ok = abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0.0, 0.0)
            fix = ApproachFix(ident=_text(ident), lat=round(lat, 6) if ok else None, lon=round(lon, 6) if ok else None)
            final = msg.type == T_FINAL_LEG or (msg.type != T_LEG and parent is not None and parent.kind == "approach")
            return _Node("final" if final else "leg", msg.parent_id, fix=fix)
        if size == APPROACH.size:
            kind, suffix, number, designator = APPROACH.unpack(payload)
            runway = f"{number:02d}{RUNWAY_DESIGNATORS.get(designator, '')}" if number else ""
            letter = chr(suffix) if 65 <= suffix <= 90 else ""
            return _Node("approach", msg.parent_id, approach=(APPROACH_KINDS.get(kind, ""), runway, letter))
        if size != NAME8.size:
            return None
        name = _text(payload)
        if msg.type == T_TRANSITION or (msg.type != T_AIRPORT and parent is not None and parent.kind == "approach"):
            return _Node("transition", msg.parent_id, name=name)
        return _Node("airport", msg.parent_id, name=name)

    def build(self, t: float) -> ApproachesData:
        ordered = sorted(self.nodes.items(), key=lambda kv: kv[1].order)
        out: list[ApproachRoute] = []
        for uid, node in ordered:
            if node.kind != "approach":
                continue
            kind, runway, suffix = node.approach
            transitions: list[tuple[ApproachFix, ...]] = []
            for tid, child in ordered:
                if child.parent == uid and child.kind == "transition":
                    legs = tuple(n.fix for _, n in ordered if n.parent == tid and n.kind == "leg" and n.fix is not None)
                    if legs:
                        transitions.append(legs)
            final = tuple(n.fix for _, n in ordered if n.parent == uid and n.kind == "final" and n.fix is not None)
            out.append(ApproachRoute(kind=kind, runway=runway, suffix=suffix, transitions=tuple(transitions), final=final))
        return ApproachesData(t=t, airport=self.icao, approaches=tuple(out))
