"""``AtcEngine``: the deterministic IFR dialogue.

Synchronous and driven purely by events: ``handle(event) -> list[BusEvent]``.
Given the same events, config and seed it produces the same transmissions,
which is what makes replay-based scenario tests possible. Replies are
scheduled a short, seeded delay after the pilot's transmission and go out on
the first event at or after their due time.
"""

import math
import random
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from localtc.atc_core.airport import AirportGeometry, TaxiGraph, published, select_approach, select_runway
from localtc.atc_core.airspace import Airspace, Area
from localtc.atc_core.facilities import (
    Facility,
    airport_facilities,
    area_center,
    center_facility,
    channel_khz,
    is_sector_airport,
    sector_center,
)
from localtc.atc_core.llm import LlmPhraser
from localtc.atc_core.llm.triggers import question_topic
from localtc.atc_core.phase import FlightPhase, PhaseThresholds, PhaseTracker
from localtc.atc_core.phraseology import TemplateLibrary, speech
from localtc.atc_core.readback import (
    ChainInterpreter,
    GrammarInterpreter,
    InterpretContext,
    Interpretation,
    Interpreter,
    PendingReadback,
    SayAgainInterpreter,
)
from localtc.atc_core.route import Route, RouteFix
from localtc.atc_core.session import Clearance, Exchange, IssuedInstruction, SessionSnapshot, SessionState, snapshot
from localtc.atc_core.values import Approach, Callsign, Phrase, Wind, clean_sim_name
from localtc.atc_core.weather import AtisBoard, AtisInfo, WeatherTracker, components, magnetic_wind
from localtc.sim_api import (
    AircraftIdentity,
    Airport,
    AirportData,
    AtcAlert,
    AtcTransmission,
    AtisBroadcast,
    BusEvent,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    RadioTuned,
    ReadbackEvaluated,
    SimLifecycle,
    TrafficSnapshot,
    TrafficTarget,
    Transcript,
)

P = FlightPhase
DEPARTURE_PHASES = {P.PARKED, P.PUSHBACK, P.TAXI_OUT, P.RUNWAY_HOLD, P.TAKEOFF, P.DEPARTURE, P.CRUISE}
RESERVED_SQUAWKS = {"1200", "1202", "1255", "1276", "1277", "2000", "4000", "0000"}
PTT_TIMEOUT_S = 30.0  # a PttPressed without a release can't silence ATC forever
NM_M = 1852.0
AIRBORNE_PHASES = {P.DEPARTURE, P.CRUISE, P.ARRIVAL, P.APPROACH}
SILENT_PILOT_S = 30.0  # an instruction unanswered this long: "how do you read?", then once more, then give up
NUDGE_REPLY_S = 4.0  # after the pilot answers "how do you read", the instruction follows this soon
STALE_READBACK_S = 45.0  # answered with something else and then quiet this long: repeat it once, then stop waiting
MISSED_CHECKIN_S = 45.0  # on the new frequency but quiet this long: the controller calls first
NOT_SWITCHED_S = 45.0  # still on the old frequency this long after reading back a handoff: say it again
TRAFFIC_NM, TRAFFIC_ALT_FT, TRAFFIC_REPEAT_S = 5.0, 1200.0, 300.0
STANDBY_CHANCE = 0.3  # clearance delivery sometimes has to go get it
MAX_TAILWIND_REQUEST_KT = 10.0  # a pilot's runway request is granted up to this much tailwind
MAX_READBACK_ATTEMPTS = 3  # then ATC repeats the instruction once more and stops asking
STT_WAIT_S = 8.0  # with voice input: how long ATC waits after push-to-talk for the transcript
VECTOR_FROM_NM = 45.0  # approach starts vectoring within this of the field
VECTOR_GAP_S = 45.0  # quiet between one vector or speed instruction and the next
CROSSING_CLEARANCE_M = 200.0  # how close to a hold-short point the clearance to cross comes
GIVE_WAY_NM = 0.15  # traffic this close on the ground is close enough to wait for
GIVE_WAY_MOVING_KT = 3.0  # both have to be moving for one to be in the other's way
GIVE_WAY_AHEAD_DEG = 60.0  # how far off the nose it can be and still be in front
GIVE_WAY_CROSSING_DEG = 45.0  # anything straighter than this is going our way, not across us
GIVE_WAY_GAP_S = 90.0  # quiet between one of these and the next
GO_AROUND_NM = 1.5  # short final: an aircraft still on the runway means going around
# Landing traffic this close on final and a departure waits for it: 4 nm (about 90 s at approach speed)
# with the runway empty, 6 nm when it first has to be vacated, 2.5 nm once lined up and ready to roll.
DEPARTURE_ARRIVAL_NM, OCCUPIED_ARRIVAL_NM, LINED_UP_ARRIVAL_NM = 4.0, 6.0, 2.5
RUNWAY_CLEAR_KT = 40.0  # faster than this on the runway and it is getting off it
SEQUENCE_NM = 12.0  # where the landing order is given
SEQUENCE_ALT_FT = 3000.0  # traffic within this of our altitude counts as being on the same final
RIDE_ANSWER_S = 120.0  # a ride report answered within this of being asked for
MAX_ENDURANCE_MIN = 20 * 60  # beyond this the numbers are not telling us anything useful
EMERGENCY_LAND_NM = 25.0  # with an emergency running, tower clears the landing from this far out
MIN_TURN_DEG = 12.0  # a smaller correction isn't worth a transmission
REVECTOR_DEG = 20.0  # a new vector has to differ from the one already given by at least this much
SPEED_CONTROL_MIN_KT = 200.0  # slower than this and there is nothing to manage
SPEED_GATES = ((30.0, 250, "speed250"), (18.0, 210, "speed210"), (10.0, 180, "speed180"))
APPROACH_CLEARANCE_NM = 18.0  # approach clears the approach and hands off to tower within this of the field
DESCENT_LEAD_MIN = 5.0  # the descent is cleared this long before the top of descent, at the current groundspeed
# Joining the final: within this of the extended centreline, this far out or less, pointing no further off
# the final course than this (so a base leg counts and a downwind doesn't).
JOIN_LATERAL_NM, JOIN_FINAL_NM, JOIN_HEADING_DEG = 3.0, 20.0, 100.0
ATIS_CHECK_S = 30.0  # how often the flight's ATIS are brought up to date
ATIS_KINDS = ("atis", "awos", "asos")
REPEAT_WINDOW_S = 180.0
CONFIRM_WORDS = {"confirm", "confirming", "verify"}  # a readback repeated within this long of the instruction is just a repeat
LINED_UP_NM = 4.0  # this close on a runway's final, the pilot has chosen it
LANDING_CLEARANCE_NM = 6.0  # tower clears a quiet pilot to land by this distance on final
PUSHBACK_LOOK_M = 40.0  # how far along the taxi route to look for the direction the tail should go
PUSHBACK_STRAIGHT_DEG = 25.0  # the route this close to straight ahead: push straight back
CHAT_GAP_S = 1800.0  # quiet between one centre starting a conversation and the next
CHAT_SETTLE_S = 120.0  # settled on a centre's frequency before it starts a conversation
OFFER_HIGHER_CHANCE = 0.5
OFFER_WINDOW_S = 180.0  # how long an offered level stays on the table
ACCEPT_WORDS = {"affirmative", "affirm", "yes", "accept", "take", "climb", "climbing", "able", "wilco"}
DECLINE_WORDS = {"negative", "unable", "no", "stay", "staying", "remain", "remaining", "keep", "prefer", "happy"}
ALTITUDE_CHECKS = 2  # "check altitude" this many times for one assigned altitude, then something else
FIRM_DECLINE_WORDS = {"negative", "unable"}  # "no" alone is too often a false start: "no, no, no, we'll be able"
MAX_OFFERED_FT = 41000
DEPARTURE_ENDS_FT = 15000.0  # above this the departure controller hands the climb to a centre
DEPARTURE_ENDS_NM = 40.0  # or this far from the field, whichever comes first
SECTOR_NEAREST_NM = 150.0  # an airport further away than this names no centre for where the flight is
SECTOR_MIN_S = 1200.0  # shortest time on one enroute centre before being handed to the next
SECTOR_LAST_NM = 250.0  # inside this of the destination the arrival takes over; no more sector changes
SECTOR_DWELL_S = 30.0  # across a real centre boundary this long before the handoff: not skimming it
AREA_RECHECK_S = 5.0  # how often the aircraft's position is checked against the airspace outlines
APPROACH_CEILING_FT = 17000.0  # inside the destination's approach area and below this, approach takes the arrival
CONTROLLER_WORDS = {"clearance": "clearance", "delivery": "clearance", "ground": "ground", "tower": "tower",
                    "departure": "departure", "center": "center", "approach": "approach"}

# Which controller handles each pilot request (None: whoever is tuned).
REQUEST_CONTROLLER = {
    "request_ifr_clearance": "clearance",
    "request_pushback": "ground",
    "ready_to_taxi": "ground",
    "ready_for_departure": "tower",
    "report_final": "tower",
    "request_taxi_parking": "ground",
    "clear_of_runway": "ground",
}


@dataclass
class EngineConfig:
    destination: str | None = None
    cruise_ft: int | None = None
    callsign: str | None = None  # overrides the sim's ATC ID
    rules: str = "IFR"
    center_name: str = "Seattle"
    center_mhz: float = 125.1
    strict_callsign: bool = False
    seed: int = 0  # 0 = derived from the callsign
    thresholds: PhaseThresholds = field(default_factory=PhaseThresholds)
    response_delay_s: tuple[float, float] = (1.5, 3.0)
    min_gap_s: float = 8.0  # quiet time before an automatic ATC call
    await_transcripts: bool = False  # voice input: a transcript follows each push-to-talk release
    speech_s_per_char: float = 0.0  # voice out: how long speech takes; the frequency is busy meanwhile (0: instant)
    unscripted: bool = True  # ATC starts things too: traffic, altitude checks, "how do you read", stand by
    approach: str = "auto"  # "auto": what the airport publishes, the weather and the aircraft allow
    sid: str | None = None  # departure procedure from the flight plan, named in the IFR clearance
    star: str | None = None  # arrival procedure from the flight plan: "descend via" it
    route: tuple[RouteFix, ...] = ()  # the plan's fixes: when the climb ends, where the descent begins
    transition_ft: int = 18000  # at or above this everyone flies the standard altimeter setting


@dataclass
class _Scheduled:
    due: float
    instruction_id: str
    slots: dict[str, Any]
    facility: Facility
    clearance: str | None = None
    handoff_to: Facility | None = None
    expects_readback: bool = True
    on_issue: Callable[[], None] | None = None
    note: Phrase | None = None  # said after the instruction: weather, the ATIS, a caution


class AtcEngine:
    def __init__(
        self,
        config: EngineConfig | None = None,
        *,
        library: TemplateLibrary | None = None,
        interpreter: Interpreter | None = None,
        phraser: LlmPhraser | None = None,
    ) -> None:
        self.cfg = config or EngineConfig()
        self.route = Route(self.cfg.route)
        self.airspace = Airspace.load()
        self._area_cache: dict[str, tuple[float, Any]] = {}  # lookups, reused for AREA_RECHECK_S of sim time
        self.library = library or TemplateLibrary.load()
        self.interpreter = interpreter or ChainInterpreter(GrammarInterpreter(), SayAgainInterpreter())
        self.phraser = phraser  # words replies that have no template; None: they get "unable"
        self.state = SessionState()
        flight = self.state.flight
        flight.rules = self.cfg.rules
        flight.destination = self.cfg.destination.upper() if self.cfg.destination else None
        flight.cruise_ft = self.cfg.cruise_ft
        if self.cfg.callsign:
            flight.callsign = Callsign.named(self.cfg.callsign)
        self.tracker = PhaseTracker(destination=flight.destination, cruise_ft=flight.cruise_ft, thresholds=self.cfg.thresholds)
        self.facilities: list[Facility] = []
        self.airport_requests: list[str] = []  # airports the engine needs; the service fetches them
        self._requested: set[str] = set()
        self._scheduled: list[_Scheduled] = []
        self._rng: random.Random | None = None
        self._was_on_runway: bool | None = None  # None until the first tick
        self._ptt_since: float | None = None  # set while the pilot holds push-to-talk
        self._stt_since: float | None = None  # set from push-to-talk release until its transcript arrives
        self._tx_mhz: float | None = None  # the frequency the pilot keyed the mic on
        self._radio_busy_until = -math.inf  # voice out: until ATC (or the copilot) has finished speaking
        self._departure_override: str | None = None  # a departure runway the pilot asked for
        self._approach_kind: str | None = None  # an approach type the pilot asked for
        self._going_around = False
        self._traffic: dict[int, TrafficTarget] = {}
        self._traffic_nm: dict[int, float] = {}  # distance at the previous snapshot, to see who's closing
        self._traffic_called: dict[int, float] = {}  # object id -> when ATC last called it
        self._tuned_since = 0.0
        self._last_handoff: tuple[float, Facility, Facility] | None = None  # (when, from, to) of the last handoff
        self._rehanded: set[tuple[str, str]] = set()  # handoffs already said a second time
        self._crossings: tuple[str, ...] = ()  # runways the taxi route goes across
        self._crossed: set[str] = set()  # ... and the ones already cleared to cross (full names, "09/27")
        self._crossing: str | None = None  # the runway cleared across and not yet left behind
        self._via_floor: int | None = None  # the altitude a "descend via" ends at, while it is the clearance
        self._takeoff_wait: str | None = None  # the runway a departure is being held for
        self._takeoff_hold: str | None = None  # why: "arrival" (holding short) or "occupied" (lined up)
        self._expected: dict[str, dict[str, Any]] = {}  # every value each instruction gave, by instruction id
        self._was_crossing = False
        self._sector: Facility | None = None  # the enroute centre working the airspace the flight is in
        self._chat_t = -math.inf  # when a centre last started a conversation of its own
        self._asked_ride_t = -math.inf  # when a centre last asked after the ride
        self._gave_way_t = -math.inf  # when ground last held this aircraft for another
        self._vector_t = -math.inf  # when approach last gave a vector or a speed
        self._chatted: set[str] = set()  # centres that have already started one
        self._offered_level: tuple[float, int] | None = None  # (when, altitude) a level offered and not yet taken
        self._sector_since = -math.inf
        self._sector_next: tuple[str, float] | None = None  # a new centre's area entered, and since when
        self._repeated: set[str] = set()  # instructions already said a second time for a silent pilot
        self._nudged: set[str] = set()  # ... and the ones already asked "how do you read" about
        self._deviation_since: float | None = None
        self._altitude_checked_t = -math.inf
        self._altitude_checks: dict[int, int] = {}  # "check altitude" calls made, by assigned altitude
        self._checked_in: set[str] = set()  # stations the pilot has checked in with (or that called first)
        self._pending_alerts: list[BusEvent] = []  # raised inside a check that only answers yes or no
        self.weather = WeatherTracker()
        self.atis = AtisBoard(self.cfg.seed)
        self._atis_checked_t = -math.inf
        self._atis_tuned: str | None = None  # airport whose ATIS COM1 is on
        self._atis_news: set[str] = set()  # airports whose ATIS letter changed; tell the pilot
        self._t = 0.0

    # --- public ---------------------------------------------------------------------------

    def handle(self, event: BusEvent) -> list[BusEvent]:
        self._t = max(self._t, event.t)
        out: list[BusEvent] = []
        if isinstance(event, AirportData):
            self.tracker.handle(event)
            self._rebuild_facilities()
        elif isinstance(event, AircraftIdentity):
            if not self.cfg.callsign and event.atc_id:
                self.state.flight.callsign = Callsign.from_sim(event.atc_id, event.airline, event.flight_number, event.atc_type)
            elif self.cfg.callsign and self.state.flight.callsign is not None:
                # Keep the type for abbreviated callsigns ("Boeing 38B") even when the ident is overridden.
                self.state.flight.callsign = replace(self.state.flight.callsign, type_name=clean_sim_name(event.atc_type))
            self.state.flight.aircraft_type = clean_sim_name(event.atc_model)
        elif isinstance(event, SimLifecycle):
            self.tracker.handle(event)
        elif isinstance(event, TrafficSnapshot):
            # Aircraft on the ground are kept too: they are the ones occupying a runway or crossing in
            # front of a taxiing aircraft. The advisory call is the only thing that wants them left out.
            self._traffic = {target.object_id: target for target in event.targets}
        elif isinstance(event, OwnshipState):
            out += self._on_ownship(event)
        elif isinstance(event, PttPressed):
            self._ptt_since = event.t
            own = self.state.aircraft
            # A transcript arrives after the key is released, maybe after a frequency change: it belongs here.
            self._tx_mhz = (own.com2_mhz if event.radio == 2 else own.com1_mhz) if own else None
        elif isinstance(event, PttReleased):
            self._ptt_since = None
            if self.cfg.await_transcripts:
                self._stt_since = event.t  # don't answer, or call, while the pilot's words are being transcribed
        elif isinstance(event, Transcript):
            self._ptt_since = self._stt_since = None
            if event.source == "copilot":
                self._radio_busy_until = max(self._radio_busy_until, event.t + self._speech_s(event.text))
            if event.text.strip():  # an empty one: push-to-talk carried no speech, nothing to answer
                out += self._on_pilot(event)
            self._tx_mhz = None
        out += self._flush(event.t)
        return out

    def snapshot(self) -> SessionSnapshot:
        return snapshot(self.state, self._t)

    @property
    def idle(self) -> bool:
        """Nothing scheduled to say and the pilot isn't transmitting: a good moment for the pilot to call."""
        return not self._scheduled and not self._transmitting(self._t) and self._t >= self._radio_busy_until

    def facility(self, controller: str) -> Facility | None:
        """The facility for a controller in the current flight context (origin before arrival, destination after)."""
        candidates = [f for f in self.facilities if f.controller == controller]
        if not candidates:
            return None
        arriving = self.state.phase is not None and P(self.state.phase) not in DEPARTURE_PHASES
        preferred = self.state.flight.destination if arriving else self.state.flight.origin
        return next((f for f in candidates if f.airport == preferred), candidates[0])

    def geometry(self, icao: str | None) -> AirportGeometry | None:
        return self.tracker.context_builder.airports.get(icao) if icao else None

    # --- ATIS and weather ------------------------------------------------------------------------

    def _airport_name(self, icao: str) -> str:
        geo = self.geometry(icao)
        return speech.airport_name(geo.airport.name, icao) if geo is not None else icao

    def _atis_airport(self, mhz: float) -> str | None:
        """The airport whose ATIS (or AWOS/ASOS) broadcasts on ``mhz``, if any."""
        for icao, geo in self.tracker.context_builder.airports.items():
            if any(f.kind in ATIS_KINDS and channel_khz(f.mhz) == channel_khz(mhz) for f in geo.airport.frequencies):
                return icao
        return None

    def current_atis(self, icao: str | None) -> AtisInfo | None:
        return self.atis.current.get(icao) if icao else None

    def _refresh_atis(self, own: OwnshipState, *, force: bool = False) -> list[BusEvent]:
        """Keep the flight's airports' ATIS current (every ``ATIS_CHECK_S``); broadcast the tuned one."""
        if not force and own.t - self._atis_checked_t < ATIS_CHECK_S:
            return []
        self._atis_checked_t = own.t
        st = self.state
        airports = self.tracker.context_builder.airports
        out: list[BusEvent] = []
        for icao in dict.fromkeys(a for a in (st.flight.origin, st.flight.destination, self._atis_tuned) if a):
            geo = airports.get(icao)
            weather = self.weather.surface(icao, airports) if geo is not None else None
            if geo is None or weather is None:
                continue
            had = icao in self.atis.current
            info = self.atis.update(icao, geo, weather, own.zulu_s, self._airport_name(icao), own.t)
            if info is not None and had:
                self._atis_news.add(icao)
            if icao == self._atis_tuned and (info is not None or force):
                current = self.atis.current[icao]
                mhz = next(f.mhz for f in geo.airport.frequencies if f.kind in ATIS_KINDS)
                out.append(AtisBroadcast(t=own.t, airport=icao, station=current.name, frequency_mhz=mhz,
                                         letter=current.letter, text=current.text, spoken=current.spoken))
        return out

    def _runway_end(self, icao: str | None, own: OwnshipState):
        """The runway in use at an airport: its ATIS runway, or the best one for the wind."""
        geo = self.geometry(icao)
        if geo is None:
            return None
        if icao == self.state.flight.origin and self._departure_override and (end := geo.end(self._departure_override)):
            return end
        info = self.atis.current.get(icao or "")
        if info is not None and (end := geo.end(info.runway)) is not None:
            return end
        return select_runway(geo, own.wind_dir_true, own.wind_kt)

    def _atis_note(self, icao: str | None, reported: str | None) -> Phrase | None:
        """"information Charlie is current, altimeter 29.92" when the pilot didn't report the current ATIS."""
        info = self.current_atis(icao)
        if info is None or (reported or "").upper() == info.letter:
            return None
        parts = [("information {atis} is current", {"atis": info.letter})]
        if info.weather.altimeter_inhg is not None:
            parts.append(("altimeter {altimeter}", {"altimeter": info.weather.altimeter_inhg}))
        return self._phrases(parts)

    def _caution_note(self, icao: str | None, *, wind: bool = False) -> Phrase | None:
        """Cautions for the weather at an airport ("caution gusty winds"), and optionally the wind."""
        info, own = self.current_atis(icao), self.state.aircraft
        parts: list[tuple[str, dict[str, Any]]] = []
        if wind and own is not None:
            parts.append(("wind {wind}", {"wind": self._wind(own)}))
        if info is not None:
            parts += [(remark, {}) for remark in info.remarks if remark.startswith("caution")]
        return self._phrases(parts) if parts else None

    def _altimeter_note(self, icao: str | None) -> Phrase | None:
        altimeter = self.weather.altimeter_inhg
        if altimeter is None or icao is None:
            return None
        # Above the transition altitude everyone is on the standard setting and a local altimeter means
        # nothing yet; it comes with the descent through the transition level.
        own = self.state.aircraft
        if own is not None and own.alt_indicated_ft >= self.cfg.transition_ft:
            return None
        return self._phrases([(f"{self._airport_name(icao).split()[0]} altimeter {{altimeter}}", {"altimeter": altimeter})])

    def _phrases(self, parts: list[tuple[str, dict[str, Any]]]) -> Phrase:
        phrases = [Phrase(*self.library.fill(text, slots, context="note")) for text, slots in parts]
        return sum(phrases[1:], phrases[0])

    # --- telemetry ------------------------------------------------------------------------------

    def _on_ownship(self, own: OwnshipState) -> list[BusEvent]:
        st = self.state
        st.aircraft = own
        change = self.tracker.handle(own)
        ctx = self.tracker.context
        out: list[BusEvent] = []

        if st.flight.origin is None and own.on_ground and ctx.airport is not None:
            st.flight.origin = ctx.airport.icao
            self._rebuild_facilities()
        dest = st.flight.destination
        if dest and dest not in self.tracker.context_builder.airports and dest not in self._requested:
            self._requested.add(dest)
            self.airport_requests.append(dest)

        self.weather.update(own, self.tracker.context_builder.airports)
        previous = (st.comms.tuned_mhz, st.comms.tuned, self._atis_tuned)
        st.comms.tuned_mhz = own.com1_mhz
        st.comms.tuned = self._facility_for(own.com1_mhz)
        self._atis_tuned = self._atis_airport(own.com1_mhz) if st.comms.tuned is None else None
        if (st.comms.tuned_mhz, st.comms.tuned, self._atis_tuned) != previous:
            tuned = st.comms.tuned
            self._tuned_since = own.t
            expected = st.comms.expected
            if st.pending is not None and expected is not None and tuned == expected and st.pending.controller != tuned.controller:
                # Switched to the new frequency without reading it back: that's the answer. It counts as
                # read back, so a readback that lands a moment later -- the copilot changes frequency the
                # instant it is told to, and the two controllers may even share one -- isn't a stray call
                # to the new controller, who would have nothing to make of it but "say again".
                st.pending, st.read_back = None, st.pending
            elif st.pending is not None and tuned is not None and st.pending.controller != tuned.controller:
                st.pending = None  # left that controller: whatever they were waiting for is moot now
            if self._atis_tuned is not None:
                out.append(RadioTuned(t=own.t, radio=1, frequency_mhz=own.com1_mhz, controller="atis",
                                      station=f"{self._airport_name(self._atis_tuned)} ATIS"))
            else:
                out.append(RadioTuned(t=own.t, radio=1, frequency_mhz=own.com1_mhz,
                                      controller=tuned.controller if tuned else None, station=tuned.station if tuned else None))
        out += self._refresh_atis(own, force=self._atis_tuned is not None and self._atis_tuned != previous[2])

        if change is not None:
            st.phase, st.phase_since_t = change.phase, change.t
            st.phase_history.append((change.t, change.phase))
            out.append(change)
            out += self._on_phase_change(change, own)
        out += self._monitor(own)
        out += self._pending_alerts
        self._pending_alerts = []
        return out

    def _on_phase_change(self, change: PhaseChanged, own: OwnshipState) -> list[BusEvent]:
        st, t = self.state, change.t
        out: list[BusEvent] = []
        phase = P(change.phase)
        if phase is P.TAXI_OUT and change.previous in (P.PARKED, P.PUSHBACK) and "taxi" not in st.clearances \
                and st.flight.origin:
            out.append(self._alert(t, "taxi_without_clearance", "moving without a taxi clearance"))
            ground = self.facility("ground")
            if ground is not None and st.comms.tuned is not None and st.comms.tuned.controller == "ground":
                self._schedule(t, "ground.hold_position", {}, ground, delay=False)
        elif phase is P.TAKEOFF and "takeoff" not in st.clearances:
            out.append(self._alert(t, "takeoff_without_clearance", "takeoff roll without a takeoff clearance"))
        elif phase is P.TAXI_IN and change.previous == P.LANDING and "landing" not in st.clearances:
            out.append(self._alert(t, "landed_without_clearance", "landed without a landing clearance"))
        elif phase is P.DEPARTURE and change.previous == P.LANDING and st.comms.tuned is not None:
            self._go_around(None, st.comms.tuned, t, own)  # went around without saying so: tower gives the instructions
        return out

    def _monitor(self, own: OwnshipState) -> list[BusEvent]:
        """Automatic ATC calls and alerts that don't wait for the pilot."""
        st, ctx, t = self.state, self.tracker.context, own.t
        out: list[BusEvent] = []
        if own.squawk == "7700" and "squawk_7700" not in st.flags:
            st.flags.add("squawk_7700")
            out.append(self._alert(t, "emergency", "squawking 7700"))

        # Accusing a pilot of a runway incursion takes the sim's own word for it. The runway polygon is
        # built from a centre point, a length and a width, and where a taxiway runs close alongside one
        # -- Vancouver's INNER past runway 13 -- it covers ground the aircraft is entitled to be on.
        on_runway_now = ctx.on_runway and own.on_runway
        entered_runway = on_runway_now and self._was_on_runway is False  # starting on a runway isn't an incursion
        self._was_on_runway = on_runway_now
        if self._crossing is not None and not on_runway_now and self._was_crossing:
            self._crossing = None  # over it and off the other side: the clearance to cross is used up
        self._was_crossing = on_runway_now and self._crossing is not None
        if entered_runway and own.on_ground and st.phase in (P.TAXI_OUT, P.RUNWAY_HOLD):
            crossing = ctx.runway is not None and ctx.runway.name == self._crossing
            if not ({"takeoff", "line_up"} & st.clearances.keys()) and not crossing:
                where = f"runway {ctx.runway.name}" if ctx.runway else "a runway"
                out.append(self._alert(t, "runway_incursion", f"entered {where} without clearance"))

        if self.cfg.unscripted and st.phase is not None and own.on_ground and self._can_call(t, own) \
                and self._ground_conflict(own):
            return out
        if self.cfg.unscripted and st.phase is not None:
            out += self._watch(own)
        if st.phase is None or not self._can_call(t, own):
            return out
        phase = P(st.phase)
        tuned = st.comms.tuned.controller if st.comms.tuned else None
        if self.cfg.unscripted and phase in AIRBORNE_PHASES:
            if self._runway_conflict(own) or self._emergency_handling(own) or self._sequence_on_final(own) \
                    or self._radar_vectors(own) or self._traffic_advisory(own) or self._altitude_check(own) \
                    or self._enroute_chat(own):
                return out

        def once(flag: str) -> bool:
            if flag in st.flags:
                return False
            st.flags.add(flag)
            return True

        spoke_here = (st.comms.last_pilot_t or -math.inf) >= self._tuned_since  # news waits for the check-in
        if self._atis_news and spoke_here and (news := self._atis_update(phase, tuned)) is not None:
            icao, info = news
            self._atis_news.discard(icao)
            parts = [("information {atis} is now current", {"atis": info.letter})]
            if info.weather.altimeter_inhg is not None:
                parts.append(("altimeter {altimeter}", {"altimeter": info.weather.altimeter_inhg}))
            self._schedule(t, "common.info", {"message": self._phrases(parts)}, st.comms.tuned, delay=False,
                           on_issue=lambda: self._assign(**({"arrival_atis": info.letter} if icao == st.flight.destination
                                                            else {"atis": info.letter})))
        elif phase in (P.TAXI_OUT, P.RUNWAY_HOLD) and tuned == "ground" and (crossing := self._crossing_due(own)) is not None:
            self._crossed.add(crossing)
            self._crossing = crossing
            self._schedule(t, "ground.cross_runway", {"runway": crossing.split("/")[0]}, st.comms.tuned, delay=False)
        elif phase in (P.RUNWAY_HOLD, P.TAXI_OUT) and tuned == "tower" and self._takeoff_wait is not None \
                and "takeoff" not in st.clearances and st.pending is None:
            self._release(t, st.comms.tuned, self._takeoff_wait, answering=False)
        elif phase is P.RUNWAY_HOLD and tuned == "ground" and "taxi" in st.clearances and once("handoff_tower"):
            if (tower := self.facility("tower")) is not None:
                self._handoff(t, "ground.handoff_tower", st.comms.tuned, tower)
        elif phase is P.DEPARTURE and own.alt_agl_ft > 500 and tuned == "tower" and once("handoff_departure"):
            if (departure := self.facility("departure") or self._center()) is not None:
                self._handoff(t, "tower.handoff_departure", st.comms.tuned, departure)
        elif tuned == "departure" and self._leaving_departure(own, phase) and once("handoff_center"):
            center = self._center()
            if self.center_area(own) is not None and (here := self._sector_candidate(own)) is not None \
                    and here.station != center.station:
                center = here  # climbed out into the next centre's airspace already: that one takes it
            self._sector, self._sector_since = center, t  # the first sector of the cruise
            self._handoff(t, "departure.handoff_center", st.comms.tuned, center)
        elif phase in (P.CRUISE, P.ARRIVAL) and tuned == "center" and not self._arriving_in_area(own) \
                and (crossing := self._sector_crossing(own)) is not None:
            self._sector, self._sector_since = crossing, t
            self._handoff(t, "center.handoff_center", st.comms.tuned, crossing)
        elif phase is P.CRUISE and tuned in ("center", "departure") and "descend" not in st.flags and self._descent_due(own) \
                and (plan := self._arrival_plan(own)) is not None:
            self._clear_descent(t, own, st.comms.tuned, plan)
        elif phase in (P.ARRIVAL, P.APPROACH) and tuned in ("center", "departure"):
            if "descend" not in st.flags and (plan := self._arrival_plan(own)) is not None:
                st.flags.add("descend")
                altitude, instruction = self._arrival_altitude(plan["arrival_alt"], own, "center")
                self._schedule(
                    t, instruction, {"altitude": altitude, "approach": plan["approach"]}, st.comms.tuned,
                    delay=False, on_issue=lambda: self._assign(altitude_ft=altitude, approach=plan["approach"].display,
                                                              arrival_runway=plan["approach"].runway),
                    note=self._altimeter_note(st.flight.destination),
                )
            elif (
                self._arriving_in_area(own)
                and (approach := self.facility("approach")) is not None and once("handoff_approach")
            ):
                self._handoff(t, "center.handoff_approach", st.comms.tuned, approach)
        elif phase in (P.ARRIVAL, P.APPROACH, P.LANDING) and tuned == "approach" and "approach" not in st.clearances:
            # The approach is cleared well before the aircraft is established, not as it crosses the
            # threshold of the approach phase: a pilot flying an ILS wants the clearance before
            # intercepting, with time to brief it and change to tower.
            if self._joining_final(own) and (spoke_here or t - self._tuned_since >= MISSED_CHECKIN_S):
                self._clear_approach(t, own, st.comms.tuned, delay=False)
        elif phase in (P.ARRIVAL, P.APPROACH, P.LANDING) and tuned == "tower" and "landing" not in st.clearances:
            on_final = phase is P.LANDING or (ctx.final is not None and ctx.final.distance_nm <= LANDING_CLEARANCE_NM)
            runway = self._landing_runway(own)
            if on_final and (runway is None or self._traffic_on_runway(own, runway) is None):
                self._clear_to_land(t, own, st.comms.tuned, delay=False)
        elif phase is P.TAXI_IN and tuned == "tower" and once("exit_contact_ground"):
            if (ground := self.facility("ground")) is not None:
                self._handoff(t, "tower.exit_contact_ground", st.comms.tuned, ground)
        return out

    def _atis_update(self, phase: FlightPhase, tuned: str | None) -> tuple[str, AtisInfo] | None:
        """A changed ATIS worth telling the pilot about: the origin's while on its ground or tower frequency,
        the destination's once talking to approach or tower there."""
        st = self.state
        for icao in list(self._atis_news):
            info = self.current_atis(icao)
            if info is None:
                self._atis_news.discard(icao)
                continue
            departing = icao == st.flight.origin and phase in (P.PARKED, P.TAXI_OUT, P.RUNWAY_HOLD) and tuned in ("ground", "tower")
            arriving = icao == st.flight.destination and phase in (P.ARRIVAL, P.APPROACH) and tuned in ("approach", "center")
            if departing or arriving:
                return icao, info
            if not (icao in (st.flight.origin, st.flight.destination)):
                self._atis_news.discard(icao)
        return None

    # --- ATC starts something: the unscripted moments ------------------------------------------------------

    def _watch(self, own: OwnshipState) -> list[BusEvent]:
        """A silent pilot, a missed check-in, a handoff not taken. These run even with a readback pending."""
        st, t = self.state, own.t
        if self._scheduled or self._transmitting(t) or t < self._radio_busy_until:
            return []
        if "emergency" in st.flags:
            return []  # nothing is chased while an emergency is running
        last_pilot = st.comms.last_pilot_t if st.comms.last_pilot_t is not None else -math.inf
        last_atc = st.comms.last_atc_t if st.comms.last_atc_t is not None else -math.inf
        pending = st.pending
        if pending is not None and last_pilot < pending.issued_t and t - last_atc >= SILENT_PILOT_S:
            issued = st.issued.get(pending.instruction_id)
            if issued is None or st.comms.tuned is None or not issued.facility.matches(st.comms.tuned_mhz or 0.0):
                return []  # the pilot isn't on that frequency: they can't hear it
            # Asked once, said once more, then let go. Re-issuing makes a fresh pending, so without
            # keeping these per instruction the pair would start over and never stop.
            if pending.instruction_id not in self._nudged:
                self._nudged.add(pending.instruction_id)
                st.pending = replace(pending, nudged=True)
                self._schedule(t, "common.how_read", {"station": issued.facility.station}, issued.facility, delay=False,
                               expects_readback=False)
            elif pending.instruction_id not in self._repeated:
                self._repeated.add(pending.instruction_id)
                self._schedule(t, pending.instruction_id, issued.slots, issued.facility, delay=False)
            else:
                st.pending = None
                return [self._alert(t, "readback_unresolved", f"{pending.instruction_id}: no answer from the pilot")]
            return []
        if pending is not None and last_pilot >= pending.issued_t:
            # The pilot answered with something else and never read it back. Say it once more, then stop
            # waiting: a readback that never comes mustn't silence ATC for the whole flight. Answering
            # "how do you read" is the exception -- the point of asking was to say it again, so that
            # follows straight away and the readback is still expected.
            answered_check = pending.nudged and pending.instruction_id not in self._repeated
            if t - max(last_atc, last_pilot) < (NUDGE_REPLY_S if answered_check else STALE_READBACK_S):
                return []
            issued = st.issued.get(pending.instruction_id)
            if issued is not None and pending.instruction_id not in self._repeated and st.comms.tuned is not None \
                    and issued.facility.matches(st.comms.tuned_mhz or 0.0):
                self._repeated.add(pending.instruction_id)
                st.pending = replace(pending, issued_t=t, nudged=False)
                self._schedule(t, pending.instruction_id, issued.slots, issued.facility, delay=False,
                               expects_readback=answered_check)
                return []
            st.pending = None
            return [self._alert(t, "readback_unresolved", f"{pending.instruction_id}: never read back")]
        if self._last_handoff is not None and not own.on_ground:
            handed_t, old, new = self._last_handoff
            tuned = st.comms.tuned
            if tuned == new and last_pilot < self._tuned_since and t - self._tuned_since >= MISSED_CHECKIN_S \
                    and new.controller in ("departure", "approach"):
                self._last_handoff = None  # on frequency but quiet: the new controller calls first
                st.comms.contacted.add(new.controller)
                self._checkin(t, new, own)
            elif tuned == old and st.pending is None and t - max(handed_t, last_pilot) >= NOT_SWITCHED_S:
                self._last_handoff = None  # still on the old frequency: say it once more (once: then it's the pilot's call)
                if (old.station, new.station) not in self._rehanded:
                    self._rehanded.add((old.station, new.station))
                    self._handoff_again(t, old, new)
        return []

    def _handoff_again(self, t: float, old: Facility, new: Facility) -> None:
        self._schedule(t, "common.contact", {"station": new.station, "frequency": new.mhz}, old, delay=False, handoff_to=new)

    def _traffic_advisory(self, own: OwnshipState) -> bool:
        """Real AI traffic nearby and converging: "traffic, two o'clock, four miles, opposite direction, 3,500"."""
        st, t = self.state, own.t
        tuned = st.comms.tuned
        if tuned is None or tuned.controller not in ("tower", "departure", "center", "approach") or not self._traffic:
            return False
        best: tuple[float, TrafficTarget] | None = None
        ground_ft = self.tracker.context.airport.airport.elev_ft if self.tracker.context.airport is not None else 0.0
        for oid, target in self._traffic.items():
            if target.on_ground:
                continue  # traffic advisories are for aircraft in the air
            nm = _distance_nm(own.lat, own.lon, target.lat, target.lon)
            closing = nm < self._traffic_nm.get(oid, math.inf) - 0.05
            self._traffic_nm[oid] = nm
            if abs(target.alt_ft - own.alt_msl_ft) > TRAFFIC_ALT_FT or nm > TRAFFIC_NM or (not closing and nm > 2.5):
                continue
            if target.alt_ft - ground_ft < 500:
                continue  # landing or just off the runway: the tower is sequencing it, not worth a call
            if self._on_a_final(target):
                continue  # lined up to land: in the landing order, not in anyone's way up here
            if t - self._traffic_called.get(oid, -math.inf) < TRAFFIC_REPEAT_S:
                continue
            if best is None or nm < best[0]:
                best = (nm, target)
        if best is None:
            return False
        nm, target = best
        bearing = _bearing(own.lat, own.lon, target.lat, target.lon)
        clock = round(((bearing - own.hdg_true) % 360) / 30) % 12 or 12
        relative = (target.hdg_true - own.hdg_true) % 360
        if 5 <= clock <= 7 and (relative < 30 or relative > 330):
            return False  # behind and going the same way: it can't be seen, and it isn't closing on anything
        self._traffic_called[target.object_id] = t
        direction = ("same direction" if relative < 30 or relative > 330 else "opposite direction" if 150 <= relative <= 210
                     else "crossing left to right" if relative < 180 else "crossing right to left")
        miles = max(1, round(nm))
        altitude = int(round(target.alt_ft / 100) * 100)
        kind = clean_sim_name(target.atc_model)
        display = f"{clock} o'clock, {miles} mile{'s' if miles != 1 else ''}, {direction}, {speech.altitude_display(altitude)}"
        spoken = (f"{speech.number_words(clock)} o'clock, {speech.number_words(min(miles, 99))} "
                  f"mile{'s' if miles != 1 else ''}, {direction}, {speech.altitude(altitude)}")
        if kind:
            display, spoken = display + f", {kind}", spoken + f", {speech.digits(kind) if any(c.isdigit() for c in kind) else kind}"
        self._schedule(t, "common.traffic", {"message": Phrase(display, spoken)}, tuned, delay=False, expects_readback=False)
        return True

    def _on_a_final(self, target: TrafficTarget) -> bool:
        """Established on a final approach within 10 nm of a runway at the airport nearby."""
        geo = self.tracker.context.airport
        if geo is None:
            return False
        final = geo.final_approach(target.lat, target.lon, target.hdg_true, max_distance_nm=10.0)
        return final is not None and target.alt_ft - geo.airport.elev_ft < final.distance_nm * 400 + 1000

    def _ground_conflict(self, own: OwnshipState) -> bool:
        """Another aircraft taxiing across in front of this one: hold position and let it go by.

        Close quarters on the ground is the other thing ground control is for, and until now a flight
        taxied through the traffic as though it were not there.
        """
        st, t = self.state, own.t
        tuned = st.comms.tuned
        if tuned is None or tuned.controller != "ground" or not own.on_ground:
            return False
        if P(st.phase) not in (P.TAXI_OUT, P.TAXI_IN) or own.gs_kt < GIVE_WAY_MOVING_KT:
            return False
        if t - self._gave_way_t < GIVE_WAY_GAP_S:
            return False
        for target in self._traffic.values():
            if not target.on_ground or target.gs_kt < GIVE_WAY_MOVING_KT:
                continue
            nm = _distance_nm(own.lat, own.lon, target.lat, target.lon)
            if nm > GIVE_WAY_NM:
                continue
            ahead = abs(((_bearing(own.lat, own.lon, target.lat, target.lon) - own.hdg_true + 540) % 360) - 180)
            crossing = abs(((target.hdg_true - own.hdg_true + 540) % 360) - 180)
            if ahead > GIVE_WAY_AHEAD_DEG or crossing < GIVE_WAY_CROSSING_DEG:
                continue  # not in front, or going the same way as us
            self._gave_way_t = t
            side = "left" if ((target.hdg_true - own.hdg_true) % 360) < 180 else "right"
            kind = clean_sim_name(target.atc_model) or "traffic"
            display = f"{kind} crossing {side} to {'right' if side == 'left' else 'left'}"
            spoken = (f"{speech.digits(kind) if any(c.isdigit() for c in kind) else kind} "
                      f"crossing {side} to {'right' if side == 'left' else 'left'}")
            self._schedule(t, "ground.give_way", {"message": Phrase(display, spoken)}, tuned, delay=False,
                           expects_readback=False)
            return True
        return False

    def _release(self, t: float, facility: Facility, runway: str, *, answering: bool) -> None:
        """Tower's answer to a departure: cleared for takeoff, line up and wait, or hold short for traffic.

        A departure is only cleared onto an empty runway with nobody close on final. Landing traffic
        near the threshold holds it short; a runway still occupied with nobody arriving lets it line up
        behind. Tower keeps watching and clears it the moment the runway is free (``_takeoff_wait``).
        """
        st = self.state
        blocked = self._departure_blocked(runway, lined_up=self._takeoff_hold == "occupied")
        if blocked is None:
            self._takeoff_wait = self._takeoff_hold = None
            self._schedule(t, "tower.takeoff", {"runway": runway}, facility, clearance="takeoff", delay=answering,
                           on_issue=lambda: self._assign(departure_runway=runway),
                           note=self._caution_note(st.flight.origin, wind=True))
            return
        reason, target, miles = blocked
        if reason == "arrival" and self._takeoff_hold == "occupied":
            return  # lined up already: it goes the moment the runway is free, not back to the hold line
        if self._takeoff_wait == runway and self._takeoff_hold == reason and not answering:
            return  # already told; still waiting for it to clear
        self._takeoff_wait, self._takeoff_hold = runway, reason
        if reason == "arrival":
            kind = clean_sim_name(target.atc_model)
            what = f"{kind} " if kind else ""
            n = max(1, round(miles))
            message = Phrase(f"traffic {what}on {n} mile final",
                             f"traffic {speech.digits(what) if any(c.isdigit() for c in what) else what}on a "
                             f"{speech.number_words(n)} mile final")
            self._schedule(t, "tower.hold_short_traffic", {"hold_short": runway, "message": message}, facility,
                           delay=answering)
        else:
            self._schedule(t, "tower.luaw", {"runway": runway}, facility, clearance="line_up", delay=answering,
                           on_issue=lambda: self._assign(departure_runway=runway))

    def _departure_blocked(self, runway: str, *, lined_up: bool = False) -> tuple[str, TrafficTarget, float] | None:
        """What stops a takeoff on ``runway`` now: ("arrival", aircraft, miles) for landing traffic close in on
        either end's final, ("occupied", aircraft, 0) for anything on the runway itself; None when it's free."""
        geo = self.geometry(self.state.flight.origin)
        end = geo.end(runway) if geo is not None else None
        if geo is None or end is None:
            return None
        occupied: TrafficTarget | None = None
        arriving: list[tuple[float, int, TrafficTarget]] = []
        for target in self._traffic.values():
            if target.on_ground:
                on = geo.runway_at(target.lat, target.lon)
                if on is not None and on.name == end.runway.name:
                    occupied = target
                continue
            final = geo.final_approach(target.lat, target.lon, target.hdg_true, max_distance_nm=OCCUPIED_ARRIVAL_NM)
            if final is not None and final.end.runway.name == end.runway.name and target.alt_ft - geo.airport.elev_ft < 3000:
                arriving.append((final.distance_nm, target.object_id, target))
        closest = min(arriving, default=None)
        margin = LINED_UP_ARRIVAL_NM if lined_up else OCCUPIED_ARRIVAL_NM if occupied is not None else DEPARTURE_ARRIVAL_NM
        if closest is not None and closest[0] <= margin:
            return "arrival", closest[2], closest[0]
        return ("occupied", occupied, 0.0) if occupied is not None else None

    def _landing_runway(self, own: OwnshipState) -> str | None:
        ctx = self.tracker.context
        if ctx.final is not None and ctx.final.distance_nm <= LINED_UP_NM:
            return ctx.final.end.ident
        return self.state.assignments.arrival_runway or (ctx.final.end.ident if ctx.final else None)

    def _traffic_on_runway(self, own: OwnshipState, runway: str) -> TrafficTarget | None:
        """An aircraft sitting on, or rolling down, the runway this one is about to land on."""
        geo = self.geometry(self.state.flight.destination)
        end = geo.end(runway) if geo is not None else None
        if geo is None or end is None:
            return None
        for target in self._traffic.values():
            if not target.on_ground or target.gs_kt > RUNWAY_CLEAR_KT:
                continue
            on = geo.runway_at(target.lat, target.lon)
            if on is not None and on.name == end.runway.name:
                return target
        return None

    def _runway_conflict(self, own: OwnshipState) -> bool:
        """Short final with somebody still on the runway: send this one around.

        Tower's whole job at this moment. Landing over the top of a stationary aircraft is the one
        thing a controller is there to prevent, and the sim's traffic is right there in the data.
        """
        st, ctx, t = self.state, self.tracker.context, own.t
        tuned = st.comms.tuned
        if tuned is None or tuned.controller != "tower" or own.on_ground or self._going_around:
            return False
        if P(st.phase) not in (P.APPROACH, P.LANDING) or ctx.final is None or ctx.final.distance_nm > GO_AROUND_NM:
            return False
        runway = self._landing_runway(own)
        if runway is None or self._traffic_on_runway(own, runway) is None:
            return False
        geo = self.geometry(st.flight.destination)
        altitude = int(math.ceil((((geo.airport.elev_ft if geo else 0) + 2000) / 100)) * 100)
        self._going_around = True
        for kind in ("approach", "landing"):
            st.clearances.pop(kind, None)
        st.pending = None
        self._schedule(t, "tower.go_around_traffic", {"altitude": altitude}, tuned, delay=False,
                       on_issue=lambda: self._assign(altitude_ft=altitude))
        return True

    def _sequence_on_final(self, own: OwnshipState) -> bool:
        """Where this aircraft fits in the landing order: "number two, follow the 737 on a four mile final"."""
        st, ctx, t = self.state, self.tracker.context, own.t
        tuned = st.comms.tuned
        if tuned is None or tuned.controller != "tower" or own.on_ground or "sequenced" in st.flags:
            return False
        if P(st.phase) not in (P.APPROACH, P.LANDING) or ctx.final is None or ctx.final.distance_nm > SEQUENCE_NM:
            return False
        runway = self._landing_runway(own)
        geo = self.geometry(st.flight.destination)
        end = geo.end(runway) if geo is not None and runway else None
        if end is None:
            return False
        ahead = []
        for target in self._traffic.values():
            if target.on_ground or abs(target.alt_ft - own.alt_msl_ft) > SEQUENCE_ALT_FT:
                continue
            their_final = geo.final_approach(target.lat, target.lon, target.hdg_true)
            if their_final is not None and their_final.end.ident == end.ident \
                    and their_final.distance_nm < ctx.final.distance_nm:
                ahead.append((their_final.distance_nm, target.object_id, target))
        if not ahead:
            return False
        st.flags.add("sequenced")
        distance, _, target = min(ahead)
        kind = clean_sim_name(target.atc_model) or "traffic"
        miles = max(1, round(distance))
        display = f"number two, follow the {kind} on a {miles} mile final"
        spoken = (f"number two, follow the {speech.digits(kind) if any(c.isdigit() for c in kind) else kind} "
                  f"on a {speech.number_words(min(miles, 99))} mile final")
        self._schedule(t, "tower.sequence", {"message": Phrase(display, spoken)}, tuned, delay=False,
                       expects_readback=False)
        return True

    def _enroute_chat(self, own: OwnshipState) -> bool:
        """Something for the long quiet hours: a centre asking after the ride, or offering a higher level.

        Neither is an instruction. The ride report wants no readback and the level is only offered, so a
        pilot who is away from the desk or simply not interested is not left with something outstanding.
        """
        st, t = self.state, own.t
        tuned = st.comms.tuned
        if P(st.phase) is not P.CRUISE or tuned is None or tuned.controller != "center" or "emergency" in st.flags:
            return False
        if t - self._chat_t < CHAT_GAP_S or t - self._tuned_since < CHAT_SETTLE_S:
            return False
        # One conversation per controller. A centre that asked after the ride and heard nothing back
        # doesn't ask again every three quarters of an hour for the rest of the crossing.
        if tuned.station in self._chatted:
            return False
        # Cruising levels are thousands of feet, and the aircraft's own altitude wanders a little: a
        # flight at 35,200 is at FL350, not FL352.
        level = int(round(own.alt_indicated_ft / 1000.0) * 1000)
        if level < 10000:
            return False
        self._chatted.add(tuned.station)
        self._chat_t = t
        higher = level + 2000
        if self._offered_level is None and higher <= MAX_OFFERED_FT and self._random().random() < OFFER_HIGHER_CHANCE:
            self._offered_level = (t, higher)
            self._schedule(t, "center.offer_higher", {"altitude": higher}, tuned, delay=False, expects_readback=False)
        else:
            self._asked_ride_t = t
            self._schedule(t, "center.say_ride", {"altitude": level}, tuned, delay=False, expects_readback=False)
        return True

    def _offer_open(self, t: float) -> bool:
        return self._offered_level is not None and t - self._offered_level[0] <= OFFER_WINDOW_S

    def _answer_offer(self, text: str, facility: Facility, t: float) -> bool:
        """A level was offered; did the pilot take it? Taking it makes it an instruction, turning it down
        withdraws it. Anything else leaves it on the table until it lapses."""
        if not self._offer_open(t):
            return False
        words = set(re.findall(r"[a-z']+", text.lower()))
        accepts = bool(words & ACCEPT_WORDS)
        if words & FIRM_DECLINE_WORDS or (words & DECLINE_WORDS and not accepts):
            self._offered_level = None
            self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return True
        return accepts and self._accepts_higher(t, facility)

    def _accepts_higher(self, t: float, facility: Facility) -> bool:
        """The pilot takes a level that was offered: now it is an instruction, to be read back."""
        if not self._offer_open(t):
            return False
        altitude = self._offered_level[1]
        self._offered_level = None
        self._schedule(t, "common.climb", {"altitude": altitude}, facility,
                       on_issue=lambda: self._assign(altitude_ft=altitude, cruise_ft=altitude))
        return True

    def _altitude_check(self, own: OwnshipState) -> bool:
        """Level at the assigned altitude, then drifting 300 ft off it for 15 s: "check altitude"."""
        st, t = self.state, own.t
        if "emergency" in st.flags:
            return False  # an aircraft in trouble is not chased about its altitude
        assigned = st.assignments.altitude_ft
        if assigned is None or "approach" in st.clearances or P(st.phase) not in (P.DEPARTURE, P.CRUISE, P.ARRIVAL):
            self._deviation_since = None
            return False
        if self._via_floor is not None and assigned == self._via_floor:
            return False  # descending via the arrival: its own restrictions set the altitudes, not one number
        if P(st.phase) is P.ARRIVAL and "descend" not in st.flags:
            return False  # starting down with the descent still to come: the answer is the descent, not "check"
        off = abs(own.alt_indicated_ft - assigned)
        if off <= 200:
            st.flags.add(f"reached:{assigned}")
            self._deviation_since = None
            return False
        if f"reached:{assigned}" not in st.flags or off <= 300:
            return False  # still climbing or descending to it
        if self._deviation_since is None:
            self._deviation_since = t
        if t - self._deviation_since < 15 or t - self._altitude_checked_t < 180 or st.comms.tuned is None:
            return False
        self._altitude_checked_t = t
        checks = self._altitude_checks.get(assigned, 0)
        self._altitude_checks[assigned] = checks + 1
        if checks < ALTITUDE_CHECKS:
            self._schedule(t, "common.check_altitude", {"altitude": assigned}, st.comms.tuned, delay=False)
            return True
        # Told twice and still going. A controller doesn't keep repeating it for the rest of the flight:
        # one climbing on up toward the filed level gets that level, so the clearance matches what the
        # radar shows; anything else is written up once and left alone.
        cruise = st.assignments.cruise_ft or st.flight.cruise_ft
        if checks == ALTITUDE_CHECKS and cruise and assigned < own.alt_indicated_ft < cruise + 200 and own.vs_fpm > 300:
            self._schedule(t, "common.climb", {"altitude": cruise}, st.comms.tuned, delay=False,
                           on_issue=lambda: self._assign(altitude_ft=cruise))
            self._deviation_since = None
            return True
        if checks == ALTITUDE_CHECKS:
            self._pending_alerts.append(self._alert(t, "altitude_deviation", f"{int(own.alt_indicated_ft)} ft, assigned {assigned}"))
        return False

    def _can_call(self, t: float, own: OwnshipState) -> bool:
        st = self.state
        if st.pending is not None or self._scheduled or self._transmitting(t):
            return False
        last = max(x for x in (st.comms.last_atc_t, st.comms.last_pilot_t, -math.inf) if x is not None)
        return t - last >= self.cfg.min_gap_s

    # --- pilot transmissions -----------------------------------------------------------------------

    def _on_pilot(self, ev: Transcript) -> list[BusEvent]:
        st, t = self.state, ev.t
        st.comms.last_pilot_t = t
        own = st.aircraft
        mhz = self._tx_mhz if self._tx_mhz is not None else ((own.com2_mhz if ev.radio == 2 else own.com1_mhz) if own else None)
        facility = self._facility_for(mhz) if mhz is not None else None
        if st.pending is not None and mhz is not None and facility is not None and facility.controller != st.pending.controller:
            # One frequency, two controllers (ground also works clearance delivery; KPHX approach also
            # lists departure's frequency): the one waiting for a readback is the one listening.
            issued = st.issued.get(st.pending.instruction_id)
            waiting = issued.facility if issued is not None else self.facility(st.pending.controller)
            if waiting is not None and waiting.matches(mhz):
                facility = waiting
        pending = st.pending if st.pending is not None and facility is not None and st.pending.controller == facility.controller else None
        if facility is None:
            st.exchanges.append(Exchange(t, "pilot", None, ev.text, None))
            where = f"{mhz:.3f}" if mhz is not None else "an unknown frequency"
            return [self._alert(t, "no_atc_on_frequency", f"no LocalTC controller on {where}; transmission not answered")]
        if pending is None and self._handed_off_from(facility) is not None:
            # The pilot is still on the old frequency after reading back the handoff: send them again,
            # whatever they said. Nothing else on this frequency is theirs to ask for any more.
            new = self._handed_off_from(facility)
            st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
            self._schedule(t, "common.contact", {"station": new.station, "frequency": new.mhz}, facility,
                           handoff_to=new, expects_readback=False)  # already read back once: just the reminder
            return []
        if CONFIRM_WORDS & set(re.findall(r"[a-z]+", ev.text.lower())):
            answer = self._confirm_query(ev.text, facility, mhz, t, pending)
            if answer is not None:  # "just to confirm, taxi to 06L?": affirmative, or negative with the right value
                st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
                return answer
        if pending is None and self._repeats_readback(ev.text, facility, mhz, t):
            # The pilot read it back again (maybe didn't hear "readback correct"): nothing to add.
            st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
            return []
        last_atc = next((e.text for e in reversed(st.exchanges) if e.speaker == "atc" and e.controller == facility.controller), None)
        interp = self.interpreter.interpret(ev.text, pending, InterpretContext(
            callsign=self._callsign(), phase=st.phase, strict_callsign=self.cfg.strict_callsign, t=t,
            station=facility.station, last_atc=last_atc, confidence=ev.confidence,
        ))
        if interp.kind == "unknown" and (topic := question_topic(ev.text)) is not None:
            # Without the language model: "request frequency for tower" is still clearly a question.
            interp = replace(interp, kind="request", intent="question", values={"topic": topic}, needs_fallback=False)
        st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, interp))
        out: list[BusEvent] = list(interp.exchanges)
        if interp.intent != "emergency" and (problem := self._problem(ev.text, facility, t)) is not None:
            out.append(problem)
            if interp.kind == "unknown" or (interp.kind != "readback" and interp.intent in (
                    None, "other", "question", "report_problem", "acknowledge")):
                return out  # the problem was the message: acknowledged, nothing to decline or ask again
        if interp.kind == "readback":
            return out + self._on_readback(interp, facility, t)
        if interp.intent == "emergency":
            return out + self._emergency(interp, facility, t)
        if interp.kind == "request":
            return out + self._on_request(interp, facility, t)
        if self._answer_offer(ev.text, facility, t):
            return out  # an answer to a level that was offered, in whatever words
        if t - self._asked_ride_t <= RIDE_ANSWER_S:
            # ATC asked after the ride, which invites an answer in the pilot's own words. Whatever
            # comes back is the answer, not something to ask again about.
            self._asked_ride_t = -math.inf
            self._schedule(t, "common.pirep", {}, facility, expects_readback=False)
            return out
        if pending is None and self._expects_checkin(facility, own):
            # The first call to a new airborne controller is the check-in, whatever speech-to-text made of
            # it ("Frontier 24 climbing 5000"): the controller already has the flight from the handoff,
            # on radar. Asking "say again" three times here left one flight at 5,000 ft all the way up.
            self._checkin(t, facility, own)
            return out
        if st.pending is not None and pending is not None:
            st.pending = replace(st.pending, attempts=st.pending.attempts + 1)
            if st.pending.attempts >= MAX_READBACK_ATTEMPTS:
                return out + self._give_up_readback(facility, t)
        self._schedule(t, "common.say_again", {}, facility)
        return out

    def _expects_checkin(self, facility: Facility, own: OwnshipState | None) -> bool:
        """Whether this controller is still waiting to hear from the flight for the first time."""
        if own is None or own.on_ground or facility.controller not in ("departure", "center", "approach"):
            return False
        return facility.station not in self._checked_in

    def _problem(self, text: str, facility: Facility, t: float) -> BusEvent | None:
        """A failure or a request for the crash trucks, short of a mayday: acknowledged once, then the flight goes on."""
        from localtc.atc_core.readback.intents import reports_problem
        from localtc.atc_core.readback.normalize import normalize

        kind = reports_problem(normalize(text))
        if kind is None or f"problem:{kind}" in self.state.flags:
            return None
        self.state.flags.add(f"problem:{kind}")
        self._schedule(t, f"common.{kind}", {}, facility, expects_readback=False)
        return self._alert(t, "pilot_problem", text)

    def _handed_off_from(self, facility: Facility) -> Facility | None:
        """The facility the pilot should be talking to instead of ``facility``: they read back a handoff off
        this frequency and haven't switched. None when this controller is still theirs."""
        st = self.state
        expected = st.comms.expected
        if expected is None or expected == facility or self._last_handoff is None:
            return None
        _, old, new = self._last_handoff
        if old != facility or new != expected:
            return None
        done = st.read_back
        return new if done is not None and done.controller == facility.controller else None

    def _confirm_query(self, text: str, facility: Facility, mhz: float | None, t: float,
                       pending: PendingReadback | None = None) -> list[BusEvent] | None:
        """The pilot checks something from the last instruction this controller gave. None: not about that.

        Still waiting for the readback is when a pilot is most likely to ask ("just to confirm, taxi to
        06L?"), so the instruction being waited on is the one asked about. A right answer settles it:
        the pilot has the part they were unsure of, which is what the readback was for."""
        done = pending if pending is not None else self.state.read_back
        if done is None or t - done.issued_t > REPEAT_WINDOW_S:
            return None
        # Asked about against everything the instruction said, not only what is still owed: after
        # "negative, taxi via A, C" the readback waits on the route, but "confirm runway 06L?" is
        # still a question about that clearance.
        whole = self._expected.get(done.instruction_id)
        about = replace(done, expected=whole, required=tuple(whole)) if whole else done
        issued = self.state.issued.get(done.instruction_id)
        same_frequency = issued is not None and mhz is not None and issued.facility.matches(mhz)
        if done.controller != facility.controller and not same_frequency:
            return None
        heard = GrammarInterpreter().interpret(text, about, InterpretContext(callsign=self._callsign()))
        if heard.kind != "readback" or not (set(heard.values) & set(about.expected)):
            return None
        if heard.mismatched:
            correction = self._fragments(list(heard.mismatched), issued.slots if issued else {})
            self._schedule(t, "common.negative", {"correction": correction}, facility, expects_readback=False)
        else:
            if pending is not None and self.state.pending is pending:
                self.state.pending, self.state.read_back = None, pending
            self._schedule(t, "common.affirmative", {}, facility, expects_readback=False)
        return []

    def _repeats_readback(self, text: str, facility: Facility, mhz: float | None, t: float) -> bool:
        done = self.state.read_back
        if done is None or t - done.issued_t > REPEAT_WINDOW_S:
            return False
        issued = self.state.issued.get(done.instruction_id)
        same_frequency = issued is not None and mhz is not None and issued.facility.matches(mhz)
        if done.controller != facility.controller and not same_frequency:
            return False
        again = GrammarInterpreter().interpret(text, done, InterpretContext(callsign=self._callsign()))
        return again.kind == "readback" and not again.mismatched

    def _on_readback(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        st = self.state
        pending = st.pending
        assert pending is not None
        issued = st.issued.get(pending.instruction_id)
        slots = issued.slots if issued else {}
        out: list[BusEvent] = [
            ReadbackEvaluated(
                t=t, instruction_id=pending.instruction_id, status=interp.status, missing=interp.missing,
                mismatched={k: _display(v) for k, v in {**interp.mismatched, **interp.unclear}.items()},
            )
        ]
        for clearance in st.clearances.values():
            if clearance.instruction_id == pending.instruction_id and clearance.readback in ("pending", "incorrect", "incomplete"):
                clearance.readback = interp.status
        if interp.status == "correct":
            st.pending, st.read_back = None, st.pending
            if self.library.get(pending.instruction_id).ack == "readback_correct":
                self._schedule(t, "common.readback_correct", {}, facility)
            return out
        if pending.attempts + 1 >= MAX_READBACK_ATTEMPTS:
            return out + self._give_up_readback(facility, t)
        # The follow-up only has to fix what was wrong or missing.
        st.pending = replace(
            pending, attempts=pending.attempts + 1, required=tuple([*interp.mismatched, *interp.unclear, *interp.missing]),
            optional=(), confirming=interp.status == "unclear",
        )
        if interp.status == "unclear":  # probably right, misheard: "confirm frequency 120.1"
            self._schedule(t, "common.confirm", {"correction": self._fragments(list(interp.unclear), slots)}, facility,
                           expects_readback=False)
        elif interp.status == "incorrect":
            correction = self._fragments(list(interp.mismatched), slots)
            self._schedule(t, "common.negative", {"correction": correction}, facility, expects_readback=False)
        else:
            self._schedule(t, "common.read_back", {"missing": self._fragments(list(interp.missing), slots)}, facility,
                           expects_readback=False)
        return out

    def _give_up_readback(self, facility: Facility, t: float) -> list[BusEvent]:
        """Several tries and still no good readback: say the instruction once more, then stop asking.
        Without this, a readback the parser can't match turns into "say again" forever."""
        st = self.state
        pending = st.pending
        assert pending is not None
        st.pending, st.read_back = None, pending  # a late readback is then recognised, not "say again"
        issued = st.issued.get(pending.instruction_id)
        if issued is not None:
            self._schedule(t, pending.instruction_id, issued.slots, facility, expects_readback=False)
        return [self._alert(t, "readback_unresolved", f"{pending.instruction_id}: no correct readback after "
                                                       f"{MAX_READBACK_ATTEMPTS} tries")]

    def _on_request(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        st = self.state
        intent = interp.intent
        own = st.aircraft
        if (letter := interp.values.get("atis")) and st.phase is not None and P(st.phase) not in DEPARTURE_PHASES:
            self._assign(arrival_atis=letter)  # "with information Delta" on arrival: the destination's ATIS
        if intent == "radio_check":
            self._schedule(t, "common.radio_check", {"station": facility.station}, facility, expects_readback=False)
            return []
        if self._answer_offer(interp.text or "", facility, t):
            return []  # a level was offered: taken, or turned down
        if intent == "question":
            return self._answer(interp, facility, t)
        if intent == "request_altitude":
            self._altitude_request(interp, facility, t, own)
            return []
        if intent == "other":
            return self._decline(interp, facility, t)
        unscripted = {
            "request_direct": self._direct, "request_vectors": self._vectors, "request_runway": self._runway_request,
            "request_return": self._return, "going_around": self._go_around, "report_conditions": self._pirep,
            "request_turn": self._turn_request,
            "traffic_report": self._traffic_reply,
        }
        if intent in unscripted:
            return unscripted[intent](interp, facility, t, own) or []
        target = REQUEST_CONTROLLER.get(intent or "")
        if intent == "report_final" and facility.controller == "approach" and "approach" not in st.clearances and own is not None:
            self._clear_approach(t, own, facility, delay=True)  # "cleared to land?" on approach: clear it, send to tower
            return []
        if target is not None and facility.controller != target:
            target_facility = self.facility(target)
            if target_facility is not None and target_facility.matches(facility.mhz):
                facility = target_facility  # e.g. ground also works clearance delivery
            elif target_facility is not None:
                self._handoff(t, "common.contact", facility, target_facility, delay=True)
                return []
        if intent == "request_ifr_clearance":
            self._ifr_clearance(t, facility, interp)
        elif intent == "request_pushback":
            self._pushback(t, facility, own)
        elif intent == "ready_to_taxi":
            if interp.values.get("atis"):
                self._assign(atis=interp.values["atis"])
            self._taxi_out(t, facility, own, atis=interp.values.get("atis"))
        elif intent == "ready_for_departure":
            runway = st.assignments.departure_runway or interp.values.get("runway") or self._departure_runway(own)
            if runway is None:
                self._schedule(t, "common.say_again", {}, facility)
                return []
            self._release(t, facility, runway, answering=True)
        elif intent == "checkin":
            self._checkin(t, facility, own)
        elif intent == "report_final" and facility.controller == "approach":
            # "Established on the RNAV 01" to approach: ready for the approach clearance, whatever the
            # geometry says (a procedure with an RF leg or a course reversal joins the final late).
            if "approach" not in st.clearances and own is not None:
                self._clear_approach(t, own, facility, delay=True)
            else:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
        elif intent == "report_final":
            landing = st.clearances.get("landing")
            if landing is not None and landing.readback == "correct":
                self._schedule(t, "common.roger", {}, facility)  # already cleared: the pilot is just reporting
            else:
                self._tower_inbound(t, own, facility, runway=interp.values.get("runway"))
        elif intent in ("request_taxi_parking", "clear_of_runway"):
            self._taxi_in(t, facility, own)
        elif intent == "say_again":
            last = st.last_issued
            if last is not None and last.facility.controller == facility.controller:
                self._schedule(t, last.instruction_id, last.slots, facility)
            else:
                self._schedule(t, "common.say_again", {}, facility)
        # acknowledge: nothing to say
        return []

    # --- unscripted moments: requests off the standard flow ----------------------------------------------

    def _airborne_controller(self, facility: Facility, own: OwnshipState | None) -> bool:
        return facility.controller in ("departure", "center", "approach") and own is not None and not own.on_ground

    def _direct(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        st = self.state
        fix = interp.values.get("fix")
        if not self._airborne_controller(facility, own) or "approach" in st.clearances:
            self._schedule(t, "common.unable", {}, facility)
            return
        if not fix:
            self._schedule(t, "common.say_again", {}, facility)
            return
        dest = st.flight.destination
        name = self._airport_name(dest) if dest else ""
        words = set(str(fix).lower().split())
        if dest and (words & {"airport", "field", "destination"} or str(fix).upper() == dest
                     or words & {w.lower() for w in name.split() if len(w) > 3}):
            fix = name  # "direct to the airport", "direct KBFI", "direct Boeing": the destination
        self._schedule(t, "common.direct", {"fix": str(fix)}, facility)

    def _vectors(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        st = self.state
        if not self._airborne_controller(facility, own) or "approach" in st.clearances:
            self._schedule(t, "common.unable", {}, facility)
            return
        if interp.values.get("runway") or interp.values.get("approach"):
            if self._set_arrival(interp.values.get("runway"), interp.values.get("approach"), own) is None:
                self._schedule(t, "common.unable", {}, facility)
                return
        plan = self._arrival_plan(own)
        heading = self._vector_heading(own, plan["approach"].runway) if plan else None
        if plan is None or heading is None:
            self._schedule(t, "common.unable", {}, facility)
            return
        self._schedule(t, "approach.vectors", {"heading": heading, "approach": plan["approach"]}, facility,
                       on_issue=lambda: self._assign(approach=plan["approach"].display, arrival_runway=plan["approach"].runway))

    def _radar_vectors(self, own: OwnshipState) -> bool:
        """Approach turning the aircraft onto the final and slowing it down, without being asked.

        Until now a flight was left to find its own way to the runway and only heard from approach when
        the clearance came. A radar controller turns you onto the localiser and manages your speed, and
        that is most of what talking to approach sounds like.
        """
        st, ctx, t = self.state, self.tracker.context, own.t
        tuned = st.comms.tuned
        if tuned is None or tuned.controller != "approach" or "approach" in st.clearances:
            return False
        if t - self._vector_t < VECTOR_GAP_S or (st.comms.last_pilot_t or -math.inf) < self._tuned_since:
            return False  # wait for the check-in, and leave room between instructions
        plan = self._arrival_plan(own)
        runway = plan["approach"].runway if plan else st.assignments.arrival_runway
        # Vectoring belongs before the approach clearance, not instead of it: once the aircraft is close
        # enough to be cleared, the clearance is the next thing said.
        distance = ctx.destination_distance_nm
        if runway is None or distance is None or not APPROACH_CLEARANCE_NM < distance <= VECTOR_FROM_NM:
            return False
        if self._speed_control(own, t, tuned):
            return True
        established = ctx.final is not None and ctx.final.end.ident == runway
        if established:
            return False  # on the final already: nothing to vector
        vector = self._vector_plan(own, runway)
        if vector is None:
            return False
        heading, joining = vector
        turn = self._turn_towards(own.hdg_mag, heading)
        if turn is None:
            return False  # already pointing that way
        assigned = st.assignments.heading
        if assigned is not None and abs(((heading - assigned + 540) % 360) - 180) < REVECTOR_DEG:
            return False  # the same vector again: the aircraft is still turning onto the last one
        self._vector_t = t
        self._schedule(t, "approach.intercept" if joining else "approach.turn",
                       {"heading": heading, "turn": turn, "approach": plan["approach"]}, tuned, delay=False,
                       on_issue=lambda: self._assign(heading=heading))
        return True

    def _speed_control(self, own: OwnshipState, t: float, facility: Facility) -> bool:
        """ "Reduce speed to 210 knots": only worth saying to something fast enough to need it."""
        ctx = self.tracker.context
        if own.ias_kt < SPEED_CONTROL_MIN_KT or ctx.destination_distance_nm is None:
            return False
        for distance, speed, flag in SPEED_GATES:
            if ctx.destination_distance_nm <= distance and own.ias_kt > speed + 20 and flag not in self.state.flags:
                self.state.flags.add(flag)
                self._vector_t = t
                self._schedule(t, "approach.speed", {"speed": speed}, facility, delay=False,
                               on_issue=lambda s=speed: self._assign(speed_kt=s))
                return True
        return False

    @staticmethod
    def _turn_towards(current: float, target: int) -> str | None:
        """ "left" or "right" for the shorter way round, or None when it is barely a turn at all."""
        difference = ((target - current + 540) % 360) - 180
        if abs(difference) < MIN_TURN_DEG:
            return None
        return "right" if difference > 0 else "left"

    def _vector_heading(self, own: OwnshipState, runway: str) -> int | None:
        plan = self._vector_plan(own, runway)
        return plan[0] if plan is not None else None

    def _vector_plan(self, own: OwnshipState, runway: str) -> tuple[int, bool] | None:
        """A magnetic heading toward a point 8 nm out on the runway's final, and whether it joins it.

        Far from that gate the heading is a leg towards it; close to it the turn is a 30 degree
        intercept of the final approach course, which is a different thing to say on the radio.
        """
        geo = self.geometry(self.state.flight.destination)
        end = geo.end(runway) if geo is not None else None
        if geo is None or end is None:
            return None
        course = math.radians(end.heading_true)
        gate = (end.threshold[0] - 8 * NM_M * math.sin(course), end.threshold[1] - 8 * NM_M * math.cos(course))
        x, y = geo.xy(own.lat, own.lon)
        joining = math.hypot(gate[0] - x, gate[1] - y) < 3 * NM_M
        if joining:
            # Close to the gate: join the final at 30 degrees from whichever side the aircraft is on.
            side = math.sin(course) * (y - end.threshold[1]) - math.cos(course) * (x - end.threshold[0])
            true = end.heading_true + (30 if side > 0 else -30)
        else:
            true = math.degrees(math.atan2(gate[0] - x, gate[1] - y))
        magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180
        return int(round(((true - magvar) % 360) / 10) * 10) % 360 or 360, joining

    def _set_arrival(self, runway: str | None, kind: str | None, own: OwnshipState | None) -> Approach | None:
        """The pilot's choice of arrival runway and approach, if the airport and the weather allow it."""
        st = self.state
        geo = self.geometry(st.flight.destination)
        plan = self._arrival_plan(own) if own is not None else None
        runway = runway or (plan["approach"].runway if plan else None)
        end = geo.end(runway) if geo is not None and runway else None
        if end is None or own is None:
            return None
        weather = self.weather.surface(st.flight.destination or "", self.tracker.context_builder.airports)
        if weather is not None and components(end, weather)[0] < -MAX_TAILWIND_REQUEST_KT:
            return None
        if kind == "VISUAL" and weather is not None and weather.visibility_sm is not None and weather.visibility_sm < 3:
            return None
        wanted = kind if kind in ("ILS", "RNAV", "VISUAL") else None
        kind = self._approach_for(end.ident, has_ils=end.has_ils, requested=wanted)
        if wanted is not None and kind != wanted:
            return None  # the airport doesn't have that approach: "unable", with the wind or as it stands
        self._approach_kind = kind
        self._assign(arrival_runway=end.ident, approach=Approach(kind, end.ident).display)
        return Approach(kind, end.ident)

    def _turn_request(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        """"Left turn after departure": tower approves it, or asks for runway heading."""
        turn = interp.values.get("turn", "left")
        st = self.state
        if facility.controller != "tower" or st.phase is None or P(st.phase) not in (P.RUNWAY_HOLD, P.TAXI_OUT, P.TAKEOFF,
                                                                                      P.DEPARTURE):
            self._schedule(t, "common.unable", {}, facility)
            return
        approved = self._random().random() < 0.85
        self._schedule(t, "tower.turn_approved" if approved else "tower.turn_unable", {"turn": turn}, facility,
                       expects_readback=False)

    def _runway_request(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        st = self.state
        runway, kind = interp.values.get("runway"), interp.values.get("approach")
        if facility.controller == "tower" and st.phase is not None and P(st.phase) is P.RUNWAY_HOLD:
            # "Tower, holding short 25L, we'd like the departure": a departure request, not a runway change.
            self._on_request(replace(interp, intent="ready_for_departure"), facility, t)
            return
        if own is not None and own.on_ground and st.phase in (P.PARKED, P.TAXI_OUT):
            geo = self.geometry(st.flight.origin)
            end = geo.end(runway) if geo is not None and runway else None
            weather = self.weather.surface(st.flight.origin or "", self.tracker.context_builder.airports)
            if end is None or "takeoff" in st.clearances:
                self._schedule(t, "common.unable", {}, facility)
                return
            if weather is not None and components(end, weather)[0] < -MAX_TAILWIND_REQUEST_KT:
                self._schedule(t, "common.unable_runway", {"runway": end.ident, "wind": weather.wind}, facility)
                return
            self._departure_override = end.ident
            if "taxi" in st.clearances and facility.controller == "ground":
                self._taxi_out(t, facility, own, atis=st.assignments.atis)  # a new route to the new runway
            else:
                self._schedule(t, "common.expect_runway", {"runway": end.ident}, facility,
                               on_issue=lambda: self._assign(departure_runway=end.ident))
            return
        if facility.controller == "tower" and own is not None and not own.on_ground and runway:
            self._tower_runway(runway, facility, t, own)  # "request runway 01" on final: cleared to land there
            return
        if not self._airborne_controller(facility, own) or "approach" in st.clearances:
            self._schedule(t, "common.unable", {}, facility)
            return
        approach = self._set_arrival(runway, kind, own)
        if approach is None:
            weather = self.weather.surface(st.flight.destination or "", self.tracker.context_builder.airports)
            geo = self.geometry(st.flight.destination)
            if runway and weather is not None and geo is not None and geo.end(runway) is not None:  # too much tailwind
                self._schedule(t, "common.unable_runway", {"runway": runway, "wind": weather.wind}, facility)
            else:
                self._schedule(t, "common.unable", {}, facility)
            return
        self._schedule(t, "common.expect_approach", {"approach": approach}, facility)

    def _tower_runway(self, runway: str, facility: Facility, t: float, own: OwnshipState) -> None:
        st = self.state
        geo = self.geometry(st.flight.destination)
        end = geo.end(runway) if geo is not None else None
        weather = self.weather.surface(st.flight.destination or "", self.tracker.context_builder.airports)
        if end is None:
            self._schedule(t, "common.unable", {}, facility)
        elif weather is not None and components(end, weather)[0] < -MAX_TAILWIND_REQUEST_KT:
            self._schedule(t, "common.unable_runway", {"runway": end.ident, "wind": weather.wind}, facility)
        else:
            self._assign(arrival_runway=end.ident)
            st.clearances.pop("landing", None)
            self._clear_to_land(t, own, facility, delay=True, runway=end.ident)

    def _return(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        """Back to the departure airport: it becomes the destination, and the arrival flow starts over."""
        st = self.state
        origin = st.flight.origin
        if not self._airborne_controller(facility, own) or origin is None or self.geometry(origin) is None:
            self._schedule(t, "common.unable", {}, facility)
            return
        st.flight.destination = origin
        self.tracker.context_builder.destination = origin
        self._assign(arrival_runway=None, approach=None, arrival_atis=None)
        self._approach_kind = None
        for flag in ("descend", "handoff_approach", "handoff_center"):
            st.flags.discard(flag)
        for kind in ("approach", "landing"):
            st.clearances.pop(kind, None)
        self._rebuild_facilities()
        plan = self._arrival_plan(own)
        if plan is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        altitude, _ = self._arrival_altitude(plan["arrival_alt"], own, "center")
        self._schedule(t, "common.return", {"destination": self._airport_name(origin), "altitude": altitude,
                                            "approach": plan["approach"]}, facility,
                       on_issue=lambda: self._assign(altitude_ft=altitude, approach=plan["approach"].display,
                                                     arrival_runway=plan["approach"].runway),
                       note=self._altimeter_note(origin))
        st.flags.add("descend")  # the return clearance is the descent

    def _go_around(self, interp: Interpretation | None, facility: Facility, t: float, own: OwnshipState | None) -> None:
        """Runway heading, climb, and back to approach for another try."""
        st = self.state
        if own is None or self._going_around:
            return
        self._going_around = True  # until approach clears the next approach
        for kind in ("approach", "landing"):
            st.clearances.pop(kind, None)
        st.pending = None
        plan = self._arrival_plan(own)
        geo = self.geometry(st.flight.destination)
        altitude = plan["approach_alt"] if plan else int(math.ceil(((geo.airport.elev_ft if geo else 0) + 2000) / 100) * 100)
        radar = self.facility("approach") or self._center()
        if facility.controller == "tower" and radar is not None:
            self._schedule(t, "tower.go_around", {"altitude": altitude, "station": radar.station, "frequency": radar.mhz},
                           facility, handoff_to=radar, on_issue=lambda: self._assign(altitude_ft=altitude))
        else:
            self._schedule(t, "approach.missed", {"altitude": altitude}, facility, on_issue=lambda: self._assign(altitude_ft=altitude))

    def _pirep(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        self._schedule(t, "common.pirep", {}, facility)

    def _traffic_reply(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        if interp.values.get("traffic") == "in_sight" or "sight" in interp.text.lower():
            self._schedule(t, "common.roger", {}, facility)  # "looking" and "negative contact" need no answer

    # --- non-routine: emergencies, questions, requests without a procedure --------------------------------

    def _emergency(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        """An emergency declared, and everything that follows from it.

        The first call gets the questions; once the details are in, the flight is given priority and
        the shortest way to the ground. After that ``_emergency_handling`` keeps it: the approach is
        cleared early, tower clears the landing whatever the sequence, and nobody chases readbacks.
        """
        st = self.state
        own = st.aircraft
        out: list[BusEvent] = []
        if "emergency" not in st.flags:
            st.flags.add("emergency")
            out.append(self._alert(t, "emergency", interp.text))
        details = dict(interp.values)
        if details and "fuel" not in details and (endurance := self._endurance()) is not None:
            details["fuel"] = endurance  # the sim knows; no need to make the pilot work it out
        if details:
            parts = [details.get("emergency", ""), f"{details['souls']} souls" if "souls" in details else "",
                     f"{details['fuel']} of fuel" if "fuel" in details else ""]
            text = ", ".join(p for p in parts if p)
            self._schedule(t, "common.emergency_copied", {"message": Phrase(text, text)}, facility)
        elif "emergency_asked" in st.flags:
            self._schedule(t, "common.emergency_intentions", {}, facility, expects_readback=False)
        else:
            st.flags.add("emergency_asked")
            self._schedule(t, "common.emergency", {}, facility)
        if "emergency_priority" not in st.flags and own is not None and not own.on_ground \
                and facility.controller in ("departure", "center", "approach"):
            st.flags.add("emergency_priority")
            destination = self._airport_name(st.flight.destination)
            # Told, not asked: a crew dealing with an emergency is not chased for a readback.
            self._schedule(t, "common.emergency_priority", {"destination": destination}, facility, delay=True,
                           expects_readback=False)
        return out

    def _endurance(self) -> str | None:
        """How long the fuel on board lasts at the current burn, as ATC would write it down."""
        own = self.state.aircraft
        if own is None or own.fuel_lb is None or not own.fuel_flow_pph or own.fuel_flow_pph <= 0:
            return None
        minutes = int(round(own.fuel_lb / own.fuel_flow_pph * 60))
        if minutes < 1 or minutes > MAX_ENDURANCE_MIN:
            return None
        hours, minutes = divmod(minutes, 60)
        return f"{hours} plus {minutes:02d}" if hours else f"{minutes} minutes"

    def _emergency_handling(self, own: OwnshipState) -> bool:
        """With an emergency running, get the aircraft down: clear the approach as soon as there is one
        to clear, and let tower clear the landing without waiting for a report from the pilot."""
        st, ctx, t = self.state, self.tracker.context, own.t
        tuned = st.comms.tuned
        if "emergency" not in st.flags or tuned is None or own.on_ground:
            return False
        if tuned.controller == "approach" and "approach" not in st.clearances and self._arrival_plan(own) is not None:
            self._clear_approach(t, own, tuned, delay=False)
            return True
        if tuned.controller == "tower" and "landing" not in st.clearances and ctx.destination_distance_nm is not None \
                and ctx.destination_distance_nm <= EMERGENCY_LAND_NM:
            self._clear_to_land(t, own, tuned, delay=False)
            return True
        return False

    def _answer(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        """Information the engine has (altimeter, wind, runway, ...) is answered from sim data; the rest is
        worded by the language model from the same facts, or "unable"."""
        st, own = self.state, self.state.aircraft
        topic = interp.values.get("topic", "other")
        if topic == "frequency":
            named = next((CONTROLLER_WORDS[w] for w in interp.text.lower().replace("?", " ").replace(".", " ").split()
                          if w in CONTROLLER_WORDS and CONTROLLER_WORDS[w] != facility.controller), None)
            target = self.facility(named) if named else st.comms.expected
            if target is not None and target != facility:
                if named == "tower" and facility.controller == "ground":
                    st.flags.add("handoff_tower")  # the pilot has it now; no second "contact tower"
                self._handoff(t, "common.contact", facility, target, delay=True)
                return []
        parts: list[tuple[str, dict[str, Any]]] = []
        altimeter = round(own.altimeter_setting_inhg, 2) if own and 25 < own.altimeter_setting_inhg < 33 else None
        if topic in ("altimeter", "weather", "atis") and altimeter is not None:
            parts.append(("altimeter {altimeter}", {"altimeter": altimeter}))
        if topic in ("wind", "weather") and own is not None:
            parts.insert(0, ("wind {wind}", {"wind": self._wind(own)}))
        arriving = st.phase is not None and P(st.phase) not in DEPARTURE_PHASES
        info = self.current_atis(st.flight.destination if arriving else st.flight.origin)
        if topic == "atis" and info is not None:
            parts.insert(0, ("information {atis} is current", {"atis": info.letter}))
        if topic == "runway" and (runway := self._runway_in_use(own)) is not None:
            parts.append(("runway {runway}", {"runway": runway}))
        if topic == "squawk" and st.assignments.squawk:
            parts.append(("squawk {squawk}", {"squawk": st.assignments.squawk}))
        if topic == "altitude" and st.assignments.altitude_ft:
            parts.append(("maintain {altitude}", {"altitude": st.assignments.altitude_ft}))
        if parts:
            phrases = [Phrase(*self.library.fill(text, slots, context=f"answer {topic}")) for text, slots in parts]
            self._schedule(t, "common.info", {"message": sum(phrases[1:], phrases[0])}, facility)
            return []
        return self._phrase(interp, facility, t, "answer")

    def _decline(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        return self._phrase(interp, facility, t, "decline")

    def _phrase(self, interp: Interpretation, facility: Facility, t: float, decision: str) -> list[BusEvent]:
        if self.phraser is None:
            self._schedule(t, "common.unable", {}, facility)
            return []
        callsign = self._callsign()
        callsigns = tuple({speech.callsign_display(callsign), speech.callsign_display(callsign.short), callsign.ident})
        message, exchanges = self.phraser.reply(
            pilot=interp.text, decision=decision, facts=self._facts(), callsigns=callsigns, t=t,
            trigger="question" if decision == "answer" else "unsupported_request",
        )
        if message is None:
            self._schedule(t, "common.unable", {}, facility)
        else:
            self._schedule(t, "common.info", {"message": message}, facility)
        return list(exchanges)

    def _altitude_request(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        st = self.state
        wanted = interp.values.get("altitude")
        if facility.controller not in ("departure", "center", "approach") or own is None or own.on_ground:
            self._schedule(t, "common.unable", {}, facility)
            return
        if not isinstance(wanted, int) and (direction := interp.values.get("direction")) is not None:
            wanted = self._next_level(direction, own)  # "request climb": the controller picks the altitude
        if not isinstance(wanted, int):
            self._schedule(t, "common.say_altitude", {}, facility)
            return
        assigned = st.assignments.altitude_ft or int(round(own.alt_indicated_ft, -2))
        arriving = st.phase in (P.APPROACH, P.LANDING)
        if not 1000 <= wanted <= 45000 or st.phase is P.LANDING or (arriving and wanted > assigned):
            self._schedule(t, "common.unable", {}, facility)
            return
        climbing = wanted > own.alt_indicated_ft
        if abs(wanted - own.alt_indicated_ft) < 200:  # "request to maintain 1,500" at 1,500
            self._schedule(t, "common.maintain", {"altitude": wanted}, facility, on_issue=lambda: self._assign(altitude_ft=wanted))
            return

        def approve() -> None:
            self._assign(altitude_ft=wanted)
            if st.phase in (P.DEPARTURE, P.CRUISE) and climbing:
                st.flight.cruise_ft = wanted
                self._assign(cruise_ft=wanted)
                self.tracker.detector.cruise_ft = wanted  # cruise is wherever the pilot now levels off

        self._schedule(t, "common.climb" if climbing else "common.descend", {"altitude": wanted}, facility, on_issue=approve)

    def _next_level(self, direction: str, own: OwnshipState) -> int | None:
        """The altitude a controller gives for "request climb" or "request lower" with no number: the filed
        level while still below it, otherwise the next one along (2,000 ft, the usual step)."""
        st = self.state
        assigned = st.assignments.altitude_ft or int(round(own.alt_indicated_ft, -2))
        cruise = st.assignments.cruise_ft or st.flight.cruise_ft
        if direction == "up":
            if own.alt_indicated_ft < assigned - 500:
                return assigned  # still climbing to what it has: the answer is that clearance again
            if cruise and assigned < cruise - 100:
                return cruise
            return min(assigned + 2000, MAX_OFFERED_FT)
        if st.phase is not None and P(st.phase) is P.ARRIVAL and (plan := self._arrival_plan(own)) is not None:
            return plan["arrival_alt"]
        return max(assigned - 2000, 3000)

    def _facts(self) -> dict[str, str]:
        """What the phrasing model may use, in display form. Nothing here is an instruction to fly."""
        st, own = self.state, self.state.aircraft
        facts: dict[str, str] = {}
        dest = self.geometry(st.flight.destination)
        if dest is not None:
            facts["destination"] = speech.airport_name(dest.airport.name, dest.airport.icao)
        if own is not None:
            facts["wind"] = speech.wind_display(self._wind(own))
            if 25 < own.altimeter_setting_inhg < 33:
                facts["altimeter"] = f"{own.altimeter_setting_inhg:.2f}"
        if st.assignments.departure_runway:
            facts["departure runway"] = st.assignments.departure_runway
        if st.assignments.arrival_runway:
            facts["arrival runway"] = st.assignments.arrival_runway
        if st.phase:
            facts["phase"] = st.phase.lower().replace("_", " ")
        return facts

    def _runway_in_use(self, own: OwnshipState | None) -> str | None:
        st = self.state
        if st.phase is not None and P(st.phase) not in DEPARTURE_PHASES:
            if st.assignments.arrival_runway:
                return st.assignments.arrival_runway
            plan = self._arrival_plan(own) if own is not None else None
            return plan["approach"].runway if plan else None
        return st.assignments.departure_runway or self._departure_runway(own)

    @staticmethod
    def _wind(own: OwnshipState) -> Wind:
        magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180  # east positive; sim MAGVAR sign conventions vary
        return Wind(direction_mag=int(round((own.wind_dir_true - magvar) / 10) * 10) % 360 or 360, speed_kt=int(round(own.wind_kt)))

    # --- instructions ----------------------------------------------------------------------------------

    def _ifr_clearance(self, t: float, facility: Facility, interp: Interpretation) -> None:
        st = self.state
        dest = st.flight.destination
        if dest is None:
            self._schedule(t, "clearance.say_destination", {}, facility)
            return
        if interp.values.get("atis"):
            self._assign(atis=interp.values["atis"])
        cruise = st.flight.cruise_ft or 5000
        initial = min(cruise, 5000)
        departure = self.facility("departure") or self._center()
        dest_geo = self.geometry(dest)
        destination = speech.airport_name(dest_geo.airport.name, dest) if dest_geo else dest
        squawk = self._squawk()
        if any(item.instruction_id.startswith("clearance.ifr") for item in self._scheduled):
            return  # asked again while we're getting it
        if self.cfg.unscripted and random.Random(zlib.crc32(f"standby{self._callsign().ident}{self.cfg.seed}".encode())).random() < STANDBY_CHANCE:
            self._schedule(t, "clearance.standby", {}, facility)
            t += random.Random(self.cfg.seed + 1).uniform(12, 25)  # the clearance comes a little later
        slots: dict[str, Any] = {"destination": destination, "altitude": initial, "cruise": cruise,
                                 "frequency": departure.mhz, "squawk": squawk,
                                 "minutes": self.route.minutes_to_cruise(cruise)}
        instruction = "clearance.ifr" if cruise > initial else "clearance.ifr_at_cruise"
        if self.cfg.sid:  # the flight plan files a SID: ATC clears the flight on it by name
            slots["procedure"] = self.cfg.sid
            instruction = "clearance.ifr_sid" if cruise > initial else "clearance.ifr_sid_at_cruise"
        self._schedule(
            t, instruction, slots, facility, clearance="ifr",
            on_issue=lambda: self._assign(squawk=squawk, altitude_ft=initial, cruise_ft=cruise, departure_mhz=departure.mhz),
        )

    def _departure_runway(self, own: OwnshipState | None) -> str | None:
        geo = self.geometry(self.state.flight.origin)
        if geo is None or own is None:
            return None
        end = self._runway_end(self.state.flight.origin, own)
        return end.ident if end else None

    def _pushback(self, t: float, facility: Facility, own: OwnshipState | None) -> None:
        """Push and start, with the tail sent the way the aircraft is about to taxi.

        A pushback goes tail first, so the tail is told to swing towards the taxi route and the nose
        comes round already pointing at it. Straight back when the route leads off the nose or the
        airport's taxiways aren't known.
        """
        turn = self._pushback_turn(own)
        if turn is None:
            self._schedule(t, "ground.pushback_straight", {}, facility, clearance="pushback")
        else:
            self._schedule(t, "ground.pushback", {"turn": turn}, facility, clearance="pushback")

    def _pushback_turn(self, own: OwnshipState | None) -> str | None:
        geo = self.geometry(self.state.flight.origin)
        end = self._runway_end(self.state.flight.origin, own) if own is not None else None
        if geo is None or own is None or end is None:
            return None
        route = TaxiGraph(geo).departure_route(own.lat, own.lon, end)
        if route is None:
            return None
        here = geo.xy(own.lat, own.lon)
        graph = TaxiGraph(geo)
        for node in route.nodes:  # the first point far enough along the route to give a direction
            position = graph.positions.get(node)
            if position is None:
                continue
            east, north = position[0] - here[0], position[1] - here[1]
            if math.hypot(east, north) < PUSHBACK_LOOK_M:
                continue
            relative = (math.degrees(math.atan2(east, north)) - own.hdg_true + 540) % 360 - 180
            if abs(relative) < PUSHBACK_STRAIGHT_DEG:
                return None
            return "right" if relative > 0 else "left"
        return None

    def _taxi_out(self, t: float, facility: Facility, own: OwnshipState | None, atis: str | None = None) -> None:
        geo = self.geometry(self.state.flight.origin)
        if geo is None or own is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        end = self._runway_end(self.state.flight.origin, own)
        note = self._atis_note(self.state.flight.origin, atis)
        route = TaxiGraph(geo).departure_route(own.lat, own.lon, end) if end else None
        if route is not None and end is not None and self.state.assignments.departure_runway == end.ident \
                and self.state.assignments.taxi_route:
            route = replace(route, taxiways=self.state.assignments.taxi_route)  # asked twice: the same route again
        if end is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        if route is None or not route.taxiways:
            self._schedule(t, "ground.taxi_out_no_route", {"runway": end.ident}, facility, clearance="taxi",
                           on_issue=lambda: self._assign(departure_runway=end.ident), note=note)
            return
        slots: dict[str, Any] = {"runway": end.ident, "taxi_route": route.taxiways}
        instruction = "ground.taxi_out"
        if route.crossings:
            instruction = "ground.taxi_out_hold_short"
            slots["hold_short"] = route.crossings[0].split("/")[0]
            self._crossings = route.crossings
        elif route.hold_point:  # "runway 25L at Delta, taxi via Charlie, Delta"
            instruction = "ground.taxi_out_at"
            slots["hold_point"] = route.hold_point
        self._schedule(t, instruction, slots, facility, clearance="taxi",
                       on_issue=lambda: self._assign(departure_runway=end.ident, taxi_route=route.taxiways), note=note)

    def _checkin(self, t: float, facility: Facility, own: OwnshipState | None) -> None:
        st = self.state
        self._checked_in.add(facility.station)
        if facility.controller == "departure":
            cruise = st.assignments.cruise_ft or st.flight.cruise_ft
            if cruise:
                self._schedule(t, "departure.radar_contact", {"station": facility.station, "altitude": cruise}, facility,
                               on_issue=lambda: self._assign(altitude_ft=cruise))
                return
        elif facility.controller == "center":
            # A handoff carries the flight with it: the next centre already has what it was assigned,
            # and says so, rather than starting the conversation again from nothing. Still climbing
            # below the filed level, though, the first thing a centre does is let it carry on up:
            # "maintain 5,000" to an aircraft passing 15,500 is a clearance nobody can fly.
            assigned = st.assignments.altitude_ft
            cruise = st.assignments.cruise_ft or st.flight.cruise_ft
            if own is not None and cruise and (assigned is None or assigned < cruise) \
                    and P(st.phase) in (P.DEPARTURE, P.CRUISE) and own.alt_indicated_ft < cruise - 500:
                self._schedule(t, "center.radar_contact", {"station": facility.station, "altitude": cruise}, facility,
                               on_issue=lambda: self._assign(altitude_ft=cruise))
                return
            if assigned is not None:
                self._schedule(t, "center.checkin_level", {"station": facility.station, "altitude": assigned},
                               facility, expects_readback=False)  # confirming what is already assigned
            else:
                self._schedule(t, "center.checkin", {"station": facility.station}, facility)
            return
        elif facility.controller == "approach" and "approach" not in st.clearances and own is not None:
            plan = self._arrival_plan(own)
            if plan is not None:
                altitude, instruction = self._arrival_altitude(plan["approach_alt"], own, "approach")
                self._schedule(
                    t, instruction, {"station": facility.station, "altitude": altitude, "approach": plan["approach"]},
                    facility, on_issue=lambda: self._assign(altitude_ft=altitude, approach=plan["approach"].display,
                                                            arrival_runway=plan["approach"].runway),
                    note=self._atis_note(st.flight.destination, st.assignments.arrival_atis)
                    or self._altimeter_note(st.flight.destination),
                )
                return
        elif facility.controller == "tower" and st.phase in (P.ARRIVAL, P.APPROACH, P.LANDING) \
                and "landing" not in st.clearances:
            self._tower_inbound(t, own, facility)
            return
        self._schedule(t, "common.roger", {}, facility)

    def _clear_approach(self, t: float, own: OwnshipState, facility: Facility, *, delay: bool) -> None:
        plan = self._arrival_plan(own)
        tower = self.facility("tower")
        if plan is None or tower is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        self._schedule(
            t, "approach.cleared", {"approach": plan["approach"], "station": tower.station, "frequency": tower.mhz},
            facility, delay=delay, clearance="approach", handoff_to=tower,
            on_issue=lambda: (self._assign(approach=plan["approach"].display, arrival_runway=plan["approach"].runway),
                              setattr(self, "_going_around", False)),
        )

    def _arrival_altitude(self, planned: int, own: OwnshipState, controller: str) -> tuple[int, str]:
        """An arrival altitude and the instruction for it. Above ``planned``: descend to it. Below it and still
        climbing toward a higher assigned altitude (a short hop): maintain ``planned``, which stops the climb
        there. Assigned lower than planned (a low cruise): maintain that. Arrivals are never told to climb."""
        assigned = self.state.assignments.altitude_ft
        flying = int(round(own.alt_indicated_ft, -2))
        if flying > planned + 200 and (assigned is None or assigned > planned):
            return planned, f"{controller}.descend"
        if assigned is not None and assigned < planned:
            return assigned, f"{controller}.maintain"
        return planned, f"{controller}.maintain"

    def _descent_due(self, own: OwnshipState) -> bool:
        """Time to clear the descent: DESCENT_LEAD_MIN before the top of descent.

        Waiting for the aircraft to start down means it starts down without a clearance: an FMS works out
        its own top of descent from the arrival's restrictions, and on the San Diego to Phoenix flight it
        began 23 nm before the one SimBrief planned. Given early, at pilot's discretion (or "descend via"
        the arrival), the crew starts down when their numbers say to.
        """
        st = self.state
        geo = self.geometry(st.flight.destination)
        if geo is None:
            return False
        level = st.assignments.altitude_ft or int(own.alt_indicated_ft)
        start_nm = self.route.descent_distance_nm(geo.airport.lat, geo.airport.lon, level, geo.airport.elev_ft)
        return geo.distance_nm(own.lat, own.lon) <= start_nm + own.gs_kt / 60 * DESCENT_LEAD_MIN

    def _clear_descent(self, t: float, own: OwnshipState, facility: Facility, plan: dict[str, Any]) -> None:
        """The descent from cruise, ahead of the top of descent: "descend via" the filed arrival when there is
        one, otherwise at pilot's discretion to the arrival altitude."""
        st = self.state
        st.flags.add("descend")
        approach = plan["approach"]

        def assigned(altitude: int, via: bool) -> Callable[[], None]:
            def apply() -> None:
                self._via_floor = altitude if via else None
                self._assign(altitude_ft=altitude, approach=approach.display, arrival_runway=approach.runway)
            return apply

        if self.cfg.star:
            floor = self.route.arrival_floor_ft or plan["arrival_alt"]
            self._schedule(t, "center.descend_via", {"procedure": self.cfg.star, "approach": approach}, facility,
                           delay=False, on_issue=assigned(floor, True), note=self._altimeter_note(st.flight.destination))
            return
        altitude, _ = self._arrival_altitude(plan["arrival_alt"], own, "center")
        self._schedule(t, "center.descend_pd", {"altitude": altitude, "approach": approach}, facility, delay=False,
                       on_issue=assigned(altitude, False), note=self._altimeter_note(st.flight.destination))

    def _joining_final(self, own: OwnshipState) -> bool:
        """About to join the final approach course: the moment approach clears the approach.

        Being near the airport isn't it: an arrival from the west to Phoenix's 26 passes the field on a
        downwind and was cleared there, long before the turn in. Within 3 nm of the extended centreline,
        20 nm or less out and pointing inbound (a base leg, an intercept, or already lined up) is."""
        ctx = self.tracker.context
        geo = self.geometry(self.state.flight.destination)
        if geo is None or not geo.runways:
            return ctx.destination_distance_nm is not None and ctx.destination_distance_nm <= 10.0
        final = geo.final_approach(own.lat, own.lon, own.hdg_true, max_distance_nm=JOIN_FINAL_NM,
                                   max_lateral_m=JOIN_LATERAL_NM * 1852.0, heading_tolerance=JOIN_HEADING_DEG)
        runway = self.state.assignments.arrival_runway
        return final is not None and final.distance_nm >= 1.0 and (runway is None or final.end.ident == runway)

    def _tower_inbound(self, t: float, own: OwnshipState | None, facility: Facility, *, runway: str | None = None) -> None:
        """An arrival calling tower. Cleared to land once on final with the runway seen empty; before then,
        "continue": a clearance given ten miles out promises a runway nobody has looked at yet. Tower
        clears it itself on the way in (_monitor), so the pilot needn't ask again."""
        st, ctx = self.state, self.tracker.context
        on_final = ctx.final is not None and ctx.final.distance_nm <= LANDING_CLEARANCE_NM
        landing = runway or (self._landing_runway(own) if own is not None else None)
        if "emergency" in st.flags or (on_final and own is not None and (
                landing is None or self._traffic_on_runway(own, landing) is None)):
            self._clear_to_land(t, own, facility, delay=True, runway=runway)
            return
        if landing is None:
            self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return
        self._schedule(t, "tower.continue", {"runway": landing}, facility, expects_readback=False)

    def _clear_to_land(self, t: float, own: OwnshipState | None, facility: Facility, *, delay: bool, runway: str | None = None) -> None:
        st, ctx = self.state, self.tracker.context
        geo = self.geometry(st.flight.destination)
        if runway is not None and geo is not None and geo.end(runway) is None:
            runway = None  # the pilot named a runway the airport doesn't have ("08 left" at KPHX): use the real one
        # Lined up close in: that runway. Further out (maneuvering onto the approach, maybe across the other
        # end's centerline): the runway of the approach ATC cleared.
        close_final = ctx.final.end.ident if ctx.final is not None and ctx.final.distance_nm <= LINED_UP_NM else None
        cleared = st.assignments.arrival_runway if "approach" in st.clearances else None
        runway = runway or close_final or cleared or (ctx.final.end.ident if ctx.final else None) or st.assignments.arrival_runway
        if runway is None or own is None:
            if delay:  # the pilot asked, and we can't tell which runway; an automatic call just waits
                self._schedule(t, "common.say_again", {}, facility)
            return
        self._schedule(t, "tower.land", {"runway": runway, "wind": self._wind(own)}, facility, delay=delay, clearance="landing",
                       on_issue=lambda: self._assign(arrival_runway=runway), note=self._caution_note(st.flight.destination))

    def _taxi_in(self, t: float, facility: Facility, own: OwnshipState | None) -> None:
        ctx = self.tracker.context
        geo = ctx.airport
        # Asked again, ATC repeats the route it gave, it doesn't invent a new one: the search starts from
        # where the aircraft is, so a few metres of rollout would otherwise pick different exits each time.
        taxiways = self.state.assignments.taxi_route if "taxi_in" in self.state.clearances else None
        if taxiways is None:
            route = TaxiGraph(geo).parking_route(own.lat, own.lon) if geo is not None and own is not None else None
            taxiways = route.taxiways if route is not None else None
        if taxiways:
            self._schedule(t, "ground.taxi_in", {"taxi_route": taxiways}, facility, clearance="taxi_in",
                           on_issue=lambda: self._assign(taxi_route=taxiways))
        else:
            self._schedule(t, "ground.taxi_in_no_route", {}, facility, clearance="taxi_in")

    def _handoff(self, t: float, instruction_id: str, from_facility: Facility, to_facility: Facility, *, delay: bool = False) -> None:
        self._schedule(t, instruction_id, {"station": to_facility.station, "frequency": to_facility.mhz}, from_facility,
                       delay=delay, handoff_to=to_facility)

    def _approach_for(self, runway: str, *, has_ils: bool, requested: str | None = None) -> str:
        """What to expect for a runway: what the destination publishes, what the weather allows, and what this
        aircraft can fly (``[flight] approach`` forces one)."""
        st = self.state
        geo = self.geometry(st.flight.destination)
        weather = self.weather.surface(st.flight.destination or "", self.tracker.context_builder.airports)
        own = st.aircraft
        return select_approach(
            geo.airport if geo is not None else None, runway, has_ils=has_ils,
            visibility_sm=weather.visibility_sm if weather is not None else None,
            in_cloud=own.in_cloud if own is not None else False,
            aircraft_type=st.flight.aircraft_type, requested=requested, override=self.cfg.approach,
        )

    def approaches_at(self, icao: str | None, runway: str) -> tuple[str, ...]:
        """What the airport publishes for a runway (the app and tests show this)."""
        geo = self.geometry(icao)
        return published(geo.airport, runway) if geo is not None else ()

    def _arrival_plan(self, own: OwnshipState) -> dict[str, Any] | None:
        geo = self.geometry(self.state.flight.destination)
        if geo is None:
            return None
        # Once an arrival runway is assigned, stick with it; later wind samples shouldn't flip the plan.
        assigned = self.state.assignments.arrival_runway
        end = geo.end(assigned) if assigned else self._runway_end(self.state.flight.destination, own)
        if end is None:
            return None
        elev = geo.airport.elev_ft
        cruise = self.state.flight.cruise_ft or 10000
        kind = self._approach_kind or self._approach_for(end.ident, has_ils=end.has_ils)
        return {
            "approach": Approach(kind, end.ident),
            "arrival_alt": int(min(cruise, max(3000, math.ceil((elev + 2500) / 1000) * 1000))),
            "approach_alt": int(math.ceil((elev + 2000) / 100) * 100),
        }

    # --- scheduling & issuing ------------------------------------------------------------------------------

    def _schedule(
        self,
        t: float,
        instruction_id: str,
        slots: dict[str, Any],
        facility: Facility,
        *,
        delay: bool = True,
        clearance: str | None = None,
        handoff_to: Facility | None = None,
        expects_readback: bool = True,
        on_issue: Callable[[], None] | None = None,
        note: Phrase | None = None,
    ) -> None:
        due = t + (self._random().uniform(*self.cfg.response_delay_s) if delay else 0.0)
        if self._scheduled:
            # Replies decided together go out in the order they were decided: "equipment standing by" to the
            # problem the pilot reported, then the answer to the rest of the call, never the other way round.
            due = max(due, max(item.due for item in self._scheduled))
        self._scheduled.append(_Scheduled(due, instruction_id, slots, facility, clearance, handoff_to, expects_readback, on_issue,
                                          note))

    def _transmitting(self, t: float) -> bool:
        """True while the pilot holds push-to-talk.

        The COM TRANSMIT simvar means "this radio is selected to transmit on", which is true for the
        whole flight, so it says nothing about whether the mic is keyed.
        """
        if self._stt_since is not None:
            if t - self._stt_since <= STT_WAIT_S:
                return True
            self._stt_since = None  # the transcript never came; don't wait forever
        if self._ptt_since is None:
            return False
        if t - self._ptt_since > PTT_TIMEOUT_S:  # a missed release must not mute ATC forever
            self._ptt_since = None
            return False
        return True

    def _speech_s(self, text: str) -> float:
        return 0.5 + len(text) * self.cfg.speech_s_per_char if self.cfg.speech_s_per_char > 0 else 0.0

    def _flush(self, t: float) -> list[BusEvent]:
        if self._transmitting(t) or t < self._radio_busy_until:
            return []  # never step on the pilot, or on ourselves
        out: list[BusEvent] = []
        due = sorted((s for s in self._scheduled if s.due <= t), key=lambda s: s.due)
        for item in due:
            self._scheduled.remove(item)
            out.append(self._issue(item, t))
        return out

    def _issue(self, item: _Scheduled, t: float) -> AtcTransmission:
        st = self.state
        facility = item.facility
        callsign = self._callsign()
        if facility.controller in st.comms.contacted and not callsign.is_airline:
            callsign = callsign.short
        slots = {**item.slots, "callsign": callsign}
        rendered = self.library.render(item.instruction_id, slots, rng=self._random(), controller=facility.controller)
        if item.note is not None:
            rendered = replace(rendered, text=f"{rendered.text.rstrip('.')}, {item.note.display}.",
                               spoken=f"{rendered.spoken.rstrip('.')}, {item.note.spoken}.")
        st.comms.contacted.add(facility.controller)
        self._radio_busy_until = t + self._speech_s(rendered.spoken)
        st.comms.last_atc_t = max(t, self._radio_busy_until)
        st.exchanges.append(Exchange(t, "atc", facility.controller, rendered.text))
        issued = IssuedInstruction(item.instruction_id, slots, facility, t)
        st.issued[item.instruction_id] = issued
        if item.expects_readback and item.instruction_id not in ("common.say_again", "common.roger", "common.readback_correct"):
            st.last_issued = issued
        if rendered.expected:
            self._expected[item.instruction_id] = dict(rendered.expected)
        if item.expects_readback and (rendered.required or rendered.optional):
            st.pending = PendingReadback(
                item.instruction_id, facility.controller, rendered.expected, rendered.required, rendered.optional, issued_t=t
            )
        if item.clearance:
            st.clearances[item.clearance] = Clearance(
                kind=item.clearance, instruction_id=item.instruction_id, controller=facility.controller, issued_t=t,
                readback="pending" if rendered.required else "none",
            )
        if item.handoff_to is not None:
            st.comms.expected = item.handoff_to
            self._last_handoff = (t, facility, item.handoff_to)
        if item.on_issue is not None:
            item.on_issue()
        return AtcTransmission(
            t=t, station=facility.station, frequency_mhz=facility.mhz, text=rendered.text, controller=facility.controller,
            instruction_id=item.instruction_id, spoken=rendered.spoken,
        )

    # --- helpers ------------------------------------------------------------------------------------------------

    def _rebuild_facilities(self) -> None:
        airports = self.tracker.context_builder.airports
        facilities: list[Facility] = []
        origin, dest = self.state.flight.origin, self.state.flight.destination
        if origin and origin in airports:
            facilities += airport_facilities(airports[origin].airport, role="departure")
        if dest and dest in airports and dest != origin:
            facilities += airport_facilities(airports[dest].airport, role="arrival")
        elif dest and dest == origin and dest in airports:  # returning: the departure airport's approach controller
            facilities += [f for f in airport_facilities(airports[dest].airport, role="arrival") if f.controller == "approach"]
        ends = [airports[icao].airport for icao in (origin, dest) if icao and icao in airports]
        facilities.append(self._sector or self._first_center(ends))
        self.facilities = facilities

    def _first_center(self, ends: list) -> Facility:
        """The centre that takes the flight from departure: the one whose airspace the departure airport is
        in. San Diego is Los Angeles Center, whatever [atc] center_name says; that is only for a flight from
        somewhere the airspace data doesn't cover."""
        home = ends[0] if ends else None
        area = self.airspace.center_at(home.lat, home.lon) if home is not None else None
        if area is None:
            return center_facility(ends, self.cfg.center_name, self.cfg.center_mhz)
        return area_center(area.name, ends, self.cfg.center_mhz)

    def _center(self) -> Facility:
        if self._sector is not None:
            return self._sector
        return next(f for f in self.facilities if f.controller == "center") if self.facilities else center_facility(
            [], self.cfg.center_name, self.cfg.center_mhz
        )

    def _crossing_due(self, own: OwnshipState) -> str | None:
        """A runway the taxi route goes across, with the aircraft nearly at it.

        Being routed over a runway and never cleared across it leaves the pilot either stopping for a
        clearance that never comes or being blamed for crossing. The clearance comes as they reach it.
        """
        ctx = self.tracker.context
        if not self._crossings or ctx.hold_short is None or ctx.hold_short_distance_m is None:
            return None
        if ctx.hold_short_distance_m > CROSSING_CLEARANCE_M or "taxi" not in self.state.clearances:
            return None
        runway = ctx.hold_short.runway.name
        if runway not in self._crossings or runway in self._crossed:
            return None
        return runway

    def _leaving_departure(self, own: OwnshipState, phase: FlightPhase) -> bool:
        """Departure has finished with the flight and centre takes it.

        Waiting for the cruise means a jet climbing to FL350 spends twenty minutes on a terminal
        frequency it left behind long ago. A departure controller's airspace ends within a few tens of
        miles of the field and a few thousand feet, whichever the flight reaches first.
        """
        if phase not in (P.DEPARTURE, P.CRUISE):
            return False
        above = own.alt_agl_ft >= DEPARTURE_ENDS_FT
        area = self.terminal_area(self.state.flight.origin, "departure")
        if area is not None:  # the real outline: departure works the flight until it leaves it
            away = not self._in_area(area, own)
        else:
            origin = self.geometry(self.state.flight.origin)
            away = origin is not None and origin.distance_nm(own.lat, own.lon) >= DEPARTURE_ENDS_NM
        if phase is P.CRUISE and area is None:
            return own.t - self.state.phase_since_t >= 30  # level, with nothing to say where departure ends
        return above or away

    def terminal_area(self, icao: str | None, role: str) -> Area | None:
        """The approach (or departure) area working ``icao``, if the airspace data has one around it."""
        geo = self.geometry(icao)
        if geo is None:
            return None
        key = f"{role}:{icao}"
        if key not in self._area_cache:
            area = self.airspace.approach_for(geo.icao, geo.airport.lat, geo.airport.lon, role=role)
            # An area named for the airport but drawn somewhere else would hand the flight off on the ground.
            self._area_cache[key] = (0.0, area if area is not None and area.contains(geo.airport.lat, geo.airport.lon)
                                     else None)
        return self._area_cache[key][1]

    def _in_area(self, area: Area, own: OwnshipState) -> bool:
        key = f"in:{area.id}"
        cached = self._area_cache.get(key)
        if cached is None or own.t - cached[0] >= AREA_RECHECK_S or own.t < cached[0]:
            self._area_cache[key] = (own.t, area.contains(own.lat, own.lon))
        return self._area_cache[key][1]

    def center_area(self, own: OwnshipState) -> Area | None:
        """The enroute centre whose airspace the aircraft is in."""
        cached = self._area_cache.get("center")
        if cached is None or own.t - cached[0] >= AREA_RECHECK_S or own.t < cached[0]:
            self._area_cache["center"] = (own.t, self.airspace.center_at(own.lat, own.lon))
        return self._area_cache["center"][1]

    def _arriving_in_area(self, own: OwnshipState) -> bool:
        """Centre hands an arrival to approach: inside the destination's approach area and down below its
        ceiling, or, where there is no area to go by, within DEPARTURE_ENDS_NM of the field."""
        area = self.terminal_area(self.state.flight.destination, "approach")
        if area is None:
            distance = self.tracker.context.destination_distance_nm
            return distance is not None and distance <= DEPARTURE_ENDS_NM
        return self._in_area(area, own) and own.alt_indicated_ft <= APPROACH_CEILING_FT

    def _sector_candidate(self, own: OwnshipState) -> Facility | None:
        """The enroute centre for where the aircraft is now, from the nearest airport big enough to name
        one. Over the ocean, or anywhere the sim has sent nothing sizeable, there is no candidate and the
        flight stays with the centre it is on."""
        taken = tuple(f.mhz for f in self.facilities if f.controller != "center")
        if (area := self.center_area(own)) is not None:
            nearby = [g.airport for g in self.tracker.context_builder.airports.values()
                      if g.distance_nm(own.lat, own.lon) < SECTOR_NEAREST_NM]
            return area_center(area.name, nearby, None, taken)
        # Without airspace data: named after the biggest field in range, not the closest. Centres take
        # their name from the major airport of a region, and a small field next door shouldn't outrank
        # the international airport beside it.
        in_range = [g.airport for g in self.tracker.context_builder.airports.values()
                    if is_sector_airport(g.airport) and g.distance_nm(own.lat, own.lon) < SECTOR_NEAREST_NM]
        best = max(in_range, key=lambda a: (max(r.length_m for r in a.runways), a.icao), default=None)
        if best is None:
            return None
        return sector_center(best, taken)

    def _sector_crossing(self, own: OwnshipState) -> Facility | None:
        """A new centre to be handed to, or None to stay put. Sectors are wide: a handoff needs a
        genuinely different centre and a decent stretch of flying since the last one, so that passing
        an airport doesn't set off a string of frequency changes."""
        if self.center_area(own) is not None:
            # A real boundary: handed over on crossing it, once the flight is clearly across (not skimming it).
            candidate = self._sector_candidate(own)
            if candidate is None or self._sector is None or candidate.station == self._sector.station:
                self._sector_next = None
                return None
            if self._sector_next is None or self._sector_next[0] != candidate.station:
                self._sector_next = (candidate.station, own.t)
                return None
            return candidate if own.t - self._sector_next[1] >= SECTOR_DWELL_S else None
        if own.t - self._sector_since < SECTOR_MIN_S:
            return None
        candidate = self._sector_candidate(own)
        if candidate is None or self._sector is None or candidate.station == self._sector.station:
            return None
        # Close to the destination the arrival takes over; no point changing centre first.
        destination_nm = self.tracker.context.destination_distance_nm
        if destination_nm is not None and destination_nm <= SECTOR_LAST_NM:
            return None
        return candidate

    def _facility_for(self, mhz: float | None) -> Facility | None:
        if mhz is None:
            return None
        matches = [f for f in self.facilities if f.matches(mhz)]
        if not matches:
            return self._center_here(mhz)
        if len(matches) == 1:
            return matches[0]
        expected = self.state.comms.expected
        if expected is not None and expected in matches:
            handed_from = self._last_handoff[1] if self._last_handoff is not None else None
            if handed_from in matches and channel_khz(mhz) != channel_khz(expected.mhz):
                return handed_from  # a frequency both work, and not the one ATC sent the pilot to: not switched yet
            return expected
        arriving = self.state.phase is not None and P(self.state.phase) not in DEPARTURE_PHASES
        preferred_airport = self.state.flight.destination if arriving else self.state.flight.origin
        ranked = sorted(matches, key=lambda f: (f.airport != preferred_airport, f.controller == ("departure" if arriving else "approach")))
        return ranked[0]

    def _center_here(self, mhz: float) -> Facility | None:
        """The centre whose airspace the aircraft is in, if the pilot has tuned its frequency without being
        sent there: a crew changing over early, or one that knows the airspace. That centre answers, and it
        is the flight's centre from now on."""
        own = self.state.aircraft
        if own is None or own.on_ground or self.center_area(own) is None:
            return None
        here = self._sector_candidate(own)
        if here is None or not here.matches(mhz):
            return None
        self._sector, self._sector_since, self._sector_next = here, own.t, None
        self._rebuild_facilities()
        return here

    def _callsign(self) -> Callsign:
        return self.state.flight.callsign or Callsign("UNKNOWN")

    def _random(self) -> random.Random:
        if self._rng is None:
            seed = self.cfg.seed or zlib.crc32(self._callsign().ident.encode())
            self._rng = random.Random(seed)
        return self._rng

    def _squawk(self) -> str:
        rng = random.Random(self.cfg.seed or zlib.crc32(("squawk" + self._callsign().ident).encode()))
        while True:
            code = str(rng.randint(1, 6)) + "".join(rng.choice("01234567") for _ in range(3))
            if code not in RESERVED_SQUAWKS:
                return code

    def _assign(self, **values: Any) -> None:
        for key, value in values.items():
            setattr(self.state.assignments, key, value)

    def _alert(self, t: float, kind: str, detail: str) -> AtcAlert:
        alert = AtcAlert(t=t, kind=kind, detail=detail)
        self.state.alerts.append(alert)
        return alert

    def _fragments(self, elements: list[str], slots: dict[str, Any]) -> Phrase:
        parts = []
        for element in elements:
            try:
                parts.append(self.library.fragment(element, slots))
            except Exception:
                parts.append(Phrase(element.replace("_", " "), element.replace("_", " ")))
        return sum(parts[1:], parts[0]) if parts else Phrase("", "")


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon) * 3440.065


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlon = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.degrees(math.atan2(dlon, math.radians(lat2 - lat1))) % 360


def _display(value: Any) -> str:
    if isinstance(value, float):
        return speech.frequency_display(value)
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, Approach):
        return value.display
    return str(value)
