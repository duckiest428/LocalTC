"""Event types shared by every LocalTC component.

Every event carries ``t``: seconds since the session started, stamped by
whatever produced it. Consumers must use ``t`` (never the wall clock) so a
replayed session behaves exactly like the live one.

The ``tag`` of each class is written to recordings as ``"type"``. Renaming a
tag breaks old recordings, so treat tags as a file format.
"""

from typing import Literal, Union, get_args

import msgspec


class Event(msgspec.Struct, frozen=True, kw_only=True, tag_field="type"):
    t: float


# --- Sim events (produced by a SimSource) -----------------------------------

XpdrMode = Literal["off", "standby", "test", "on", "alt", "ground"]


class OwnshipState(Event, tag="ownship_state"):
    lat: float
    lon: float
    alt_msl_ft: float
    alt_indicated_ft: float
    alt_agl_ft: float
    altimeter_inhg: float
    hdg_mag: float
    hdg_true: float
    ias_kt: float
    gs_kt: float
    vs_fpm: float
    on_ground: bool
    squawk: str
    xpdr_mode: XpdrMode
    com1_mhz: float
    com2_mhz: float
    xpdr_ident: bool = False
    com1_tx: bool = False
    com2_tx: bool = False
    com1_type: str = ""
    com1_ident: str = ""
    gear_down: bool = True
    flaps_index: int = 0
    parking_brake: bool = False
    engine_running: bool = False


class AircraftIdentity(Event, tag="aircraft_identity"):
    title: str = ""
    atc_id: str = ""
    airline: str = ""
    flight_number: str = ""
    atc_type: str = ""
    atc_model: str = ""


class TrafficTarget(msgspec.Struct, frozen=True, kw_only=True):
    object_id: int
    atc_id: str = ""
    airline: str = ""
    flight_number: str = ""
    atc_model: str = ""
    lat: float
    lon: float
    alt_ft: float
    hdg_true: float
    gs_kt: float
    on_ground: bool


class TrafficSnapshot(Event, tag="traffic_snapshot"):
    targets: tuple[TrafficTarget, ...] = ()


LifecycleKind = Literal[
    "sim_start", "sim_stop", "paused", "unpaused", "flight_loaded", "aircraft_loaded", "crashed"
]


class SimLifecycle(Event, tag="sim_lifecycle"):
    kind: LifecycleKind
    detail: str = ""


class ConnectionStatus(Event, tag="connection_status"):
    connected: bool
    detail: str = ""


# --- Radio events (produced by LocalTC itself, published on the bus) --------


class PttPressed(Event, tag="ptt_pressed"):
    radio: int = 1


class PttReleased(Event, tag="ptt_released"):
    radio: int = 1
    audio_ref: str | None = None  # path relative to the recording directory


class Transcript(Event, tag="transcript"):
    text: str
    confidence: float | None = None
    audio_ref: str | None = None


class AtcTransmission(Event, tag="atc_transmission"):
    station: str
    frequency_mhz: float
    text: str
    audio_ref: str | None = None


SimEvent = Union[OwnshipState, AircraftIdentity, TrafficSnapshot, SimLifecycle, ConnectionStatus]
RadioEvent = Union[PttPressed, PttReleased, Transcript, AtcTransmission]
BusEvent = Union[SimEvent, RadioEvent]

SIM_EVENT_TYPES: tuple[type, ...] = get_args(SimEvent)
RADIO_EVENT_TYPES: tuple[type, ...] = get_args(RadioEvent)
BUS_EVENT_TYPES: tuple[type, ...] = SIM_EVENT_TYPES + RADIO_EVENT_TYPES


def event_type(event: Event) -> str:
    """The recording tag for an event, e.g. ``"ownship_state"``."""
    return type(event).__struct_config__.tag


class SessionInfo(msgspec.Struct, frozen=True, kw_only=True):
    source_kind: Literal["live", "replay"]
    sim_product: str = ""  # e.g. "MSFS 2024"
    sim_version: str = ""
    simconnect_version: str = ""
    recording: str | None = None  # set by replay sources
