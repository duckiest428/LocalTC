"""Event types shared by every LocalTC component.

Every event carries ``t``: seconds since the session started, stamped by
whatever produced it. Consumers must use ``t`` (never the wall clock) so a
replayed session behaves exactly like the live one.

The ``tag`` of each class is written to recordings as ``"type"``. Renaming a
tag breaks old recordings, so treat tags as a file format.
"""

from typing import Literal, Union, get_args

import msgspec

from localtc.sim_api.airport import Airport


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
    # Added in Phase 1; defaults keep Phase 0 recordings loadable.
    on_runway: bool = False
    wind_dir_true: float = 0.0
    wind_kt: float = 0.0
    magvar: float = 0.0
    altimeter_setting_inhg: float = 0.0


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


class AirportData(Event, tag="airport_data"):
    airport: Airport


# --- Radio events (produced by LocalTC itself, published on the bus) --------


class PttPressed(Event, tag="ptt_pressed"):
    radio: int = 1


class PttReleased(Event, tag="ptt_released"):
    radio: int = 1
    audio_ref: str | None = None  # path relative to the recording directory


class Transcript(Event, tag="transcript"):
    text: str  # "" when push-to-talk carried no speech
    radio: int = 1
    confidence: float | None = None  # speech-to-text confidence, 0-1
    audio_ref: str | None = None
    stt_ms: float = 0.0  # speech-to-text time
    source: str = ""  # voice, typed, copilot, or "" (older recordings, scripts)


class AtcTransmission(Event, tag="atc_transmission"):
    station: str  # spoken station name, e.g. "Paine Tower"
    frequency_mhz: float
    text: str  # display text
    audio_ref: str | None = None
    controller: str = ""  # clearance, ground, tower, departure, center, approach
    instruction_id: str | None = None  # phraseology template id
    spoken: str = ""  # text normalized for speech synthesis


# --- ATC core events (produced by localtc.atc_core) -------------------------


class PhaseChanged(Event, tag="phase_changed"):
    previous: str | None
    phase: str
    reason: str = ""


class ReadbackEvaluated(Event, tag="readback_evaluated"):
    instruction_id: str
    status: str  # correct, incorrect, incomplete, no_match
    missing: tuple[str, ...] = ()
    mismatched: dict[str, str] = {}  # element -> what the pilot said


class RadioTuned(Event, tag="radio_tuned"):
    """COM1 changed frequency; says which ATC facility (if any) works it."""

    frequency_mhz: float
    radio: int = 1
    controller: str | None = None
    station: str | None = None


class AtcAlert(Event, tag="atc_alert"):
    kind: str  # runway_incursion, takeoff_without_clearance, emergency, ...
    detail: str = ""


class LlmExchange(Event, tag="llm_exchange"):
    """One call to the local language model, recorded so a replay reproduces it without the model.

    ``key`` identifies the request (model, prompts, schema); replay looks the response up by it.
    """

    purpose: str  # "understand" or "phrase"
    model: str
    key: str
    prompt: str  # the final user message; the system prompt and examples are fixed per LocalTC version
    response: str  # raw model output, "" on timeout or error
    outcome: str  # used, invalid, rejected, timeout, error, recorded_miss
    detail: str = ""  # why it was invalid or rejected, or the error
    trigger: str = ""  # why the model was asked (see atc_core.llm.triggers)
    latency_ms: float = 0.0
    attempt: int = 1


SimEvent = Union[OwnshipState, AircraftIdentity, TrafficSnapshot, SimLifecycle, ConnectionStatus, AirportData]
RadioEvent = Union[PttPressed, PttReleased, Transcript, AtcTransmission]
AtcEvent = Union[PhaseChanged, ReadbackEvaluated, AtcAlert, RadioTuned, LlmExchange]
BusEvent = Union[SimEvent, RadioEvent, AtcEvent]

SIM_EVENT_TYPES: tuple[type, ...] = get_args(SimEvent)
RADIO_EVENT_TYPES: tuple[type, ...] = get_args(RadioEvent)
ATC_EVENT_TYPES: tuple[type, ...] = get_args(AtcEvent)
BUS_EVENT_TYPES: tuple[type, ...] = SIM_EVENT_TYPES + RADIO_EVENT_TYPES + ATC_EVENT_TYPES


def event_type(event: Event) -> str:
    """The recording tag for an event, e.g. ``"ownship_state"``."""
    return type(event).__struct_config__.tag


class SessionInfo(msgspec.Struct, frozen=True, kw_only=True):
    source_kind: Literal["live", "replay"]
    sim_product: str = ""  # e.g. "MSFS 2024"
    sim_version: str = ""
    simconnect_version: str = ""
    recording: str | None = None  # set by replay sources
