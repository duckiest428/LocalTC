"""Session state: everything ATC knows about the flight.

``SessionState`` is mutable and owned by ``AtcEngine`` alone. ``snapshot()``
produces a frozen, JSON-serializable ``SessionSnapshot``: the single read
interface for the Phase 2 LLM prompt, a future UI, and debugging.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import msgspec

from localtc.atc_core.facilities import Facility
from localtc.atc_core.phraseology import slots as slot_types
from localtc.atc_core.readback import Interpretation, PendingReadback
from localtc.atc_core.values import Callsign
from localtc.sim_api import AtcAlert, OwnshipState


@dataclass
class FlightInfo:
    callsign: Callsign | None = None
    rules: str = "IFR"
    origin: str | None = None
    destination: str | None = None
    cruise_ft: int | None = None
    aircraft_type: str = ""


@dataclass
class CommsState:
    tuned_mhz: float | None = None
    tuned: Facility | None = None
    expected: Facility | None = None  # where ATC last sent the pilot
    contacted: set[str] = field(default_factory=set)  # controllers that have used the full callsign
    last_pilot_t: float | None = None
    last_atc_t: float | None = None


@dataclass
class Assignments:
    squawk: str | None = None
    altitude_ft: int | None = None
    cruise_ft: int | None = None
    heading: int | None = None  # a vector being flown
    speed_kt: int | None = None  # an assigned airspeed
    departure_runway: str | None = None
    arrival_runway: str | None = None
    taxi_route: tuple[str, ...] = ()
    departure_mhz: float | None = None
    approach: str | None = None  # display, e.g. "ILS RWY 14R"
    atis: str | None = None  # the origin's ATIS letter the pilot reported
    arrival_atis: str | None = None  # the destination's


@dataclass
class Clearance:
    kind: str  # ifr, taxi, takeoff, line_up, approach, landing, taxi_in
    instruction_id: str
    controller: str
    issued_t: float
    readback: str = "pending"  # pending, correct, incorrect, incomplete, none


@dataclass
class Exchange:
    t: float
    speaker: str  # "pilot" or "atc"
    controller: str | None
    text: str
    interpretation: Interpretation | None = None


@dataclass
class IssuedInstruction:
    instruction_id: str
    slots: dict[str, Any]
    facility: Facility
    t: float


@dataclass
class SessionState:
    flight: FlightInfo = field(default_factory=FlightInfo)
    aircraft: OwnshipState | None = None
    phase: str | None = None
    phase_since_t: float = 0.0
    phase_history: deque = field(default_factory=lambda: deque(maxlen=20))
    comms: CommsState = field(default_factory=CommsState)
    assignments: Assignments = field(default_factory=Assignments)
    clearances: dict[str, Clearance] = field(default_factory=dict)
    pending: PendingReadback | None = None
    read_back: PendingReadback | None = None  # the last instruction read back correctly
    exchanges: deque = field(default_factory=lambda: deque(maxlen=30))
    alerts: list[AtcAlert] = field(default_factory=list)
    issued: dict[str, IssuedInstruction] = field(default_factory=dict)  # last issue of each instruction id
    last_issued: IssuedInstruction | None = None
    flags: set[str] = field(default_factory=set)  # one-shot automatic instructions already given


# --- snapshot (frozen, JSON-serializable) -------------------------------------------------------------


class FacilitySnapshot(msgspec.Struct, frozen=True, kw_only=True):
    controller: str
    station: str
    mhz: float


class ClearanceSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    kind: str
    instruction_id: str
    controller: str
    issued_t: float
    readback: str


class PendingSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    instruction_id: str
    controller: str
    expected: dict[str, str]
    required: list[str]
    attempts: int


class ExchangeSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    t: float
    speaker: str
    controller: str | None
    text: str
    kind: str | None = None
    intent: str | None = None
    status: str | None = None


class SessionSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    t: float
    callsign: str | None
    rules: str
    origin: str | None
    destination: str | None
    cruise_ft: int | None
    phase: str | None
    phase_since_t: float
    position: dict[str, Any]
    tuned: FacilitySnapshot | None
    expected_contact: FacilitySnapshot | None
    assignments: dict[str, Any]
    clearances: list[ClearanceSnapshot]
    pending: PendingSnapshot | None
    recent_exchanges: list[ExchangeSnapshot]
    alerts: list[str]


def _facility(f: Facility | None) -> FacilitySnapshot | None:
    return FacilitySnapshot(controller=f.controller, station=f.station, mhz=f.mhz) if f else None


def _display(element: str, value: Any) -> str:
    slot = slot_types.SLOTS.get(element)
    if slot is not None and isinstance(value, slot.value_type):
        return slot.display(value)
    return str(value)


def snapshot(state: SessionState, t: float) -> SessionSnapshot:
    own = state.aircraft
    position = {}
    if own is not None:
        position = {
            "lat": own.lat, "lon": own.lon, "alt_ft": round(own.alt_indicated_ft), "agl_ft": round(own.alt_agl_ft),
            "hdg_mag": round(own.hdg_mag), "gs_kt": round(own.gs_kt), "vs_fpm": round(own.vs_fpm),
            "on_ground": own.on_ground, "squawk": own.squawk, "com1_mhz": own.com1_mhz,
        }
    a = state.assignments
    pending = state.pending
    return SessionSnapshot(
        t=t,
        callsign=state.flight.callsign.ident if state.flight.callsign else None,
        rules=state.flight.rules,
        origin=state.flight.origin,
        destination=state.flight.destination,
        cruise_ft=state.flight.cruise_ft,
        phase=state.phase,
        phase_since_t=state.phase_since_t,
        position=position,
        tuned=_facility(state.comms.tuned),
        expected_contact=_facility(state.comms.expected),
        assignments={k: v for k, v in {
            "squawk": a.squawk, "altitude_ft": a.altitude_ft, "cruise_ft": a.cruise_ft, "departure_runway": a.departure_runway,
            "arrival_runway": a.arrival_runway, "taxi_route": list(a.taxi_route) or None, "departure_mhz": a.departure_mhz,
            "approach": a.approach, "atis": a.atis,
        }.items() if v is not None},
        clearances=[
            ClearanceSnapshot(kind=c.kind, instruction_id=c.instruction_id, controller=c.controller, issued_t=c.issued_t, readback=c.readback)
            for c in state.clearances.values()
        ],
        pending=PendingSnapshot(
            instruction_id=pending.instruction_id,
            controller=pending.controller,
            expected={k: _display(k, v) for k, v in pending.expected.items()},
            required=list(pending.required),
            attempts=pending.attempts,
        ) if pending else None,
        recent_exchanges=[
            ExchangeSnapshot(
                t=e.t, speaker=e.speaker, controller=e.controller, text=e.text,
                kind=e.interpretation.kind if e.interpretation else None,
                intent=e.interpretation.intent if e.interpretation else None,
                status=e.interpretation.status if e.interpretation and e.interpretation.kind == "readback" else None,
            )
            for e in state.exchanges
        ],
        alerts=[f"{alert.kind}: {alert.detail}" for alert in state.alerts],
    )
