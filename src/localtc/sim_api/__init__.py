"""The sim-facing contract: event types, the SimSource interface and session time.

Depends on nothing else in LocalTC, so any component can import it on any OS.
"""

from localtc.sim_api.clock import Clock, SessionClock, StreamClock
from localtc.sim_api.codec import decode_event, encode_event
from localtc.sim_api.events import (
    BUS_EVENT_TYPES,
    RADIO_EVENT_TYPES,
    SIM_EVENT_TYPES,
    AircraftIdentity,
    AtcTransmission,
    BusEvent,
    ConnectionStatus,
    Event,
    OwnshipState,
    PttPressed,
    PttReleased,
    RadioEvent,
    SessionInfo,
    SimEvent,
    SimLifecycle,
    TrafficSnapshot,
    TrafficTarget,
    Transcript,
    event_type,
)
from localtc.sim_api.source import SimSource, SourceUnavailable

__all__ = [
    "BUS_EVENT_TYPES",
    "RADIO_EVENT_TYPES",
    "SIM_EVENT_TYPES",
    "AircraftIdentity",
    "AtcTransmission",
    "BusEvent",
    "Clock",
    "ConnectionStatus",
    "Event",
    "OwnshipState",
    "PttPressed",
    "PttReleased",
    "RadioEvent",
    "SessionClock",
    "SessionInfo",
    "SimEvent",
    "SimLifecycle",
    "SimSource",
    "SourceUnavailable",
    "StreamClock",
    "TrafficSnapshot",
    "TrafficTarget",
    "Transcript",
    "decode_event",
    "encode_event",
    "event_type",
]
