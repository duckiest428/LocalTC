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
    # Added in Phase 4 (ATIS and weather); None where a recording predates them.
    temperature_c: float | None = None  # outside air temperature at the aircraft
    visibility_m: float | None = None
    precip: int = 0  # AMBIENT PRECIP STATE bits: 2 none, 4 rain, 8 snow
    in_cloud: bool = False
    zulu_s: float | None = None  # sim time of day, seconds since 00:00Z
    # Fuel and weight; None where a recording predates them.
    fuel_lb: float | None = None  # total fuel on board
    fuel_flow_pph: float | None = None  # burn on engine 1, for an endurance estimate
    gross_weight_lb: float | None = None
    # For the ATIS (0.4); None / False where a recording predates them.
    precip_rate_mm: float | None = None  # AMBIENT PRECIP RATE, mm/h
    in_smoke: bool = False


class AircraftSystems(Event, tag="aircraft_systems"):
    """The cockpit's switches and settings the copilot checks and sets (``localtc.crew``): sent once a second while
    any of them changes. Read with key-event names the stock aircraft honour; an add-on may leave some at 0."""

    gear_pct: float = 0.0  # 0 up, 100 down and locked (in transit between)
    flaps_pct: float = 0.0  # trailing-edge flaps, 0-100
    flaps_positions: int = 0  # handle detents beyond "up"
    spoilers_pct: float = 0.0
    spoilers_armed: bool = False
    light_landing: bool = False
    light_taxi: bool = False
    light_strobe: bool = False
    light_beacon: bool = False
    light_nav: bool = False
    light_logo: bool = False
    ap_master: bool = False
    ap_heading: bool = False  # heading hold / HDG mode
    ap_nav: bool = False
    ap_approach: bool = False
    ap_altitude: bool = False
    ap_vs: bool = False
    ap_flc: bool = False  # flight level change / speed on pitch
    athr_armed: bool = False
    ap_heading_sel: float = 0.0  # the heading bug, degrees
    ap_altitude_sel: float = 0.0  # feet
    ap_altitude_sel_3: float = 0.0  # the same, index 3: the FCU of the A32NX-based aircraft (Headwind A330)
    ap_speed_sel: float = 0.0  # knots
    ap_vs_sel: float = 0.0  # feet per minute
    com1_standby_mhz: float = 0.0
    radio_height_ft: float = 0.0
    battery: bool = False
    engines_running: int = 0  # how many
    mach: float = 0.0
    # The aircraft's own numbers and warnings, for the copilot's callouts (a separate request: 0 / False where the sim
    # or an add-on doesn't give them).
    vs0_kt: float = 0.0  # stall speed, landing configuration
    vs1_kt: float = 0.0  # stall speed, clean
    takeoff_kt: float = 0.0  # the design takeoff (rotation) speed
    vmo_kt: float = 0.0  # the design cruise speed limit (VC)
    stall_warning: bool = False
    overspeed_warning: bool = False
    loc_received: bool = False  # NAV1 has a localizer signal
    gs_received: bool = False
    loc_deviation: int = 0  # -127..127, to 10: full scale is off the needle
    gs_deviation: int = 0
    # Since 0.4 (a third request, so a name an aircraft doesn't know costs only these): the load factor for turbulence,
    # ice and anti-ice, the autobrake and the reversers. -1 / 0 / False where not given.
    g_force: float = 1.0
    ice_pct: float = 0.0  # structural ice, 0-100
    anti_ice: bool = False  # engine anti-ice (engine 1) or the wing de-ice switch
    autobrake: int = -1  # the autobrake switch position (what each means is per aircraft: crew/profiles); -1 unknown
    autobrake_active: bool = False
    reverser_pct: float = 0.0  # the most deployed reverser
    jet: bool = False  # turbine engines (ENGINE TYPE 1): reversers expected


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


class WeatherReport(Event, tag="weather_report"):
    """An airport's latest real observation (its METAR, from aviationweather.gov): the weather ATC gives for an airport
    the aircraft is far from. The sim only gives the weather where the aircraft is. Recorded, so a replay decides
    with the report the flight had."""

    icao: str
    raw: str = ""  # the METAR as published
    observed: str = ""  # "1553Z"
    wind_dir_true: float | None = None  # None: variable (VRB) or calm
    wind_kt: float = 0.0
    gust_kt: float | None = None
    visibility_sm: float | None = None
    altimeter_inhg: float | None = None
    temperature_c: float | None = None
    dewpoint_c: float | None = None


class AtisReport(Event, tag="atis_report"):
    """An airport's real ATIS, as published (the FAA's digital ATIS, from atis.info): used with ``[atc] atis_source``
    "real" or "hybrid". Recorded, so a replay hears the ATIS the flight had."""

    icao: str
    letter: str
    text: str  # as published
    kind: str = "both"  # both, arrival, departure
    zulu: str = ""  # "1756"


class NearbyAirport(msgspec.Struct, frozen=True, kw_only=True):
    icao: str
    lat: float
    lon: float
    elev_ft: float = 0.0


class NearbyAirports(Event, tag="nearby_airports"):
    """The airports the sim knows around the aircraft, nearest first: where ATC looks for a diversion."""

    airports: tuple[NearbyAirport, ...] = ()


class ArrivalLeg(msgspec.Struct, frozen=True, kw_only=True):
    """A fix on an arrival (STAR) and what's published there: an altitude ("at", "above", "below", "between") and a
    speed. ``transition``: "" the common part, else the runway ("RW16L") or enroute transition it's on."""

    fix: str
    lat: float | None = None
    lon: float | None = None
    altitude: str = ""  # "", at, above, below, between
    alt1_ft: int = 0  # at, at or above, at or below; the top of a "between"
    alt2_ft: int = 0  # the bottom of a "between"
    speed_kt: int = 0  # 0: none
    transition: str = ""


class ArrivalData(Event, tag="arrival_data"):
    """An arrival procedure's legs, from the sim's navdata: the copilot checks its altitude and speed restrictions.
    ``legs`` empty: the sim has no such arrival (or nothing published on it)."""

    airport: str
    name: str
    legs: tuple[ArrivalLeg, ...] = ()


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


# --- The intercom: the pilot and the copilot (produced by localtc.crew and the voice input) ---------------


class IntercomPressed(Event, tag="intercom_pressed"):
    """The intercom key went down: the pilot talking to the copilot, not on the radio."""


class IntercomReleased(Event, tag="intercom_released"):
    audio_ref: str | None = None


class IntercomHeard(Event, tag="intercom_heard"):
    """What the pilot said to the copilot. ``source``: voice, or typed (the app's crew box)."""

    text: str
    confidence: float | None = None
    audio_ref: str | None = None
    stt_ms: float = 0.0
    source: str = "voice"


class CrewSpeech(Event, tag="crew_speech"):
    """The copilot speaking on the intercom. ``kind``: reply, done, refused, confirm, alert (what it is, for the log)."""

    text: str
    spoken: str = ""  # the words for speech, if they differ from the text ("flaps one plus F")
    kind: str = "reply"


class CrewAction(Event, tag="crew_action"):
    """Something the copilot did in the cockpit, or wouldn't do: ``outcome`` sent (the sim was told), done (the sim
    shows it), failed (it didn't take), refused (unsafe), confirm (waiting for the pilot's "confirm")."""

    action: str  # gear_down, flaps, landing_lights, ap_altitude, squawk, ...
    outcome: str
    value: str = ""  # "2", "on", "10000"
    detail: str = ""


class CopilotEvent(Event, tag="copilot_event"):
    """What the copilot made of something and did about it, for the record and the replay: ``kind`` heard (an
    utterance and how it was read), asked (a confirmation or "did you mean"), done, cancelled (a queued call or action
    dropped, and why), expired, held, failed, recovered, error. ``utterance``: the id of the words it's about."""

    kind: str
    detail: str = ""
    utterance: str = ""
    act: str = ""  # command, question, report, correction, negation, acknowledgement, answer, radio, chat, unclear
    action: str = ""  # the command or call concerned
    generation: int = 0  # the flight it belongs to (a new flight or a reconnect starts the next)


class AtcTransmission(Event, tag="atc_transmission"):
    station: str  # spoken station name, e.g. "Paine Tower"
    frequency_mhz: float
    text: str  # display text
    audio_ref: str | None = None
    controller: str = ""  # clearance, ground, tower, departure, center, approach
    instruction_id: str | None = None  # phraseology template id
    spoken: str = ""  # text normalized for speech synthesis
    worded_by: str = ""  # "template" or "model" (the language model's words for it); "": before this was recorded
    # The controller's manner, for the voice ("tower:hurried", ":busy" on a busy frequency): atc_core.personality.
    manner: str = ""
    locale: str = ""  # the region's English, for the voice ("en-GB", "en-AU"); "": none in particular (region.accent)


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


class AtcThinking(Event, tag="atc_thinking"):
    """A controller working out an answer (the language model has the pilot's call): the app shows it, nothing
    is said. ``busy`` False when the answer is out, or there won't be one."""

    station: str
    frequency_mhz: float
    busy: bool = True


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


class AtcDecision(Event, tag="atc_decision"):
    """How ATC came to its answer to one pilot transmission, kept apart: what the grammar read, what the language
    model read (after the checks), which reading the engine acted on, what it decided, who worded the reply, and
    why anything the model gave wasn't used. The ``LlmExchange`` events before it have the raw answers."""

    pilot: str  # what the pilot said
    mode: str = ""  # [llm] mode: scripted, semi, mostly_llm, llm, off
    trigger: str = ""  # why the grammar's reading alone wasn't enough (atc_core.llm.triggers); "" none
    grammar: str = ""  # the grammar's reading: "request ready_to_taxi atis=B", "readback correct", "unknown"
    model: str = ""  # the model's checked reading ("" not asked, or nothing usable)
    used: str = ""  # the reading acted on: "grammar" or "model"
    decision: str = ""  # the replies decided, as template ids ("ground.taxi, common.info"); "" nothing said
    wording: str = ""  # who words each reply, in the same order: "template" or "model"
    fallback: str = ""  # what of the model's wasn't used, and why ("" all of it was, or it wasn't asked)


class FlightArrived(Event, tag="flight_arrived"):
    """Parked at a gate or stand at the destination after landing, the taxi in over: the flight is done (the session
    stops on it when [session] auto_stop_at_gate says to). Once a flight."""

    airport: str  # ICAO
    gate: str  # "Gate B25", "Parking 3", or "parking"


class AtisBroadcast(Event, tag="atis_broadcast"):
    """An airport's ATIS, sent when the pilot tunes its frequency and again whenever the letter changes."""

    airport: str
    station: str  # "Phoenix Sky Harbor"
    frequency_mhz: float
    letter: str
    text: str
    spoken: str
    # The same broadcast worded a little differently for each time round the loop (0.4); () reads ``spoken`` each time.
    variants: tuple[str, ...] = ()
    locale: str = ""  # the region's English, for the voice
    source: str = ""  # where it's from and how old ("real ATIS 1756Z", "METAR 1753Z", "simulator, observed 2 min ago")


class RadioChatter(Event, tag="radio_chatter"):
    """Somebody else on the frequency: another aircraft, or ATC talking to it. Background radio, generated
    (atc_core/chatter.py), with nothing behind it and nothing for the pilot to answer."""

    station: str  # the controller's station ("Montreal Tower")
    frequency_mhz: float
    speaker: str  # "atc" or "pilot"
    callsign: str  # the other aircraft, as said ("Westjet 452")
    text: str
    spoken: str = ""
    controller: str = ""  # tower, ground, ... (the controller's manner of speaking)
    locale: str = ""  # the region's English, for the voices


class SessionNote(Event, tag="session_note"):
    """A note the pilot added to the recording (the app's dev mode): "ATC should have cleared me here"."""

    text: str


# --- LocalTC's traffic (localtc.traffic): only with [traffic] enabled ------------------------------------------------


class TrafficIdentity(Event, tag="traffic_identity"):
    """Who an AI aircraft is and where it's going, as the sim's AI has it: its model and livery (to put the same one
    back), its origin and destination, and its AI state ("taxi", "takeoff", "sleep" ...). Asked every few seconds."""

    object_id: int
    title: str = ""
    livery: str = ""
    origin: str = ""
    destination: str = ""
    state: str = ""


class AiObjectAssigned(Event, tag="ai_object_assigned"):
    """The sim's answer to an aircraft LocalTC created: its object id (``SpawnAiAircraft.request_id``)."""

    request_id: int
    object_id: int


class ModelList(Event, tag="model_list"):
    """The aircraft models and liveries installed (MSFS 2024's EnumerateSimObjectsAndLiveries): (title, livery)."""

    models: tuple[tuple[str, str], ...] = ()


class AircraftInputEvents(Event, tag="aircraft_input_events"):
    """The user aircraft's MSFS 2024 input events by name ("LIGHTING_LANDING_1"), listed once per aircraft: what its
    copilot profile can move them by (``[actions.x] input``), and what ``localtc debug aircraft`` writes out."""

    names: tuple[str, ...] = ()


class AircraftVars(Event, tag="aircraft_vars"):
    """Variables the copilot's profile reads (``WatchVars``): an add-on's own switch positions and windows, by name
    ("L:VC_GEAR_Lever"), whatever the sim's standard variables say."""

    values: dict[str, float] = {}


class TrafficControlStatus(Event, tag="traffic_control_status"):
    """What LocalTC's traffic is doing, for the app and the log: the real flights it's flying in the sim (``live``),
    the aircraft it parked at the gates (``parked``), and the sim's own traffic still around (``native``: it should be
    off while LocalTC's is on)."""

    mode: str  # off, on
    source: str = ""  # where the live positions come from ("adsb.lol"), "" while none has answered
    live: int = 0
    parked: int = 0
    native: int = 0
    fsltl: bool = False
    note: str = ""  # a problem, said plainly ("MSFS's own traffic is on: ...")
    recent: tuple[str, ...] = ()  # what it did last, newest first: "Delta 123 told to go around: you're on 28C"


SimEvent = Union[OwnshipState, AircraftIdentity, TrafficSnapshot, SimLifecycle, ConnectionStatus, AirportData,
                 NearbyAirports, AircraftSystems, ArrivalData, TrafficIdentity, AiObjectAssigned, ModelList,
                 TrafficControlStatus, AircraftInputEvents, WeatherReport, AtisReport, AircraftVars]
RadioEvent = Union[PttPressed, PttReleased, Transcript, AtcTransmission]
AtcEvent = Union[PhaseChanged, ReadbackEvaluated, AtcAlert, RadioTuned, LlmExchange, AtisBroadcast, RadioChatter,
                 AtcThinking, AtcDecision, FlightArrived]
AppEvent = Union[SessionNote]
CrewEvent = Union[IntercomPressed, IntercomReleased, IntercomHeard, CrewSpeech, CrewAction, CopilotEvent]
BusEvent = Union[SimEvent, RadioEvent, AtcEvent, AppEvent, CrewEvent]

SIM_EVENT_TYPES: tuple[type, ...] = get_args(SimEvent)
RADIO_EVENT_TYPES: tuple[type, ...] = get_args(RadioEvent)
ATC_EVENT_TYPES: tuple[type, ...] = get_args(AtcEvent)
APP_EVENT_TYPES: tuple[type, ...] = get_args(AppEvent)
CREW_EVENT_TYPES: tuple[type, ...] = get_args(CrewEvent)
BUS_EVENT_TYPES: tuple[type, ...] = SIM_EVENT_TYPES + RADIO_EVENT_TYPES + ATC_EVENT_TYPES + APP_EVENT_TYPES + CREW_EVENT_TYPES


def event_type(event: Event) -> str:
    """The recording tag for an event, e.g. ``"ownship_state"``."""
    return type(event).__struct_config__.tag


class SessionInfo(msgspec.Struct, frozen=True, kw_only=True):
    source_kind: Literal["live", "replay"]
    sim_product: str = ""  # e.g. "MSFS 2024"
    sim_version: str = ""
    simconnect_version: str = ""
    recording: str | None = None  # set by replay sources
