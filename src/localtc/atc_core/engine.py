"""``AtcEngine``: the deterministic IFR dialogue.

Synchronous and driven purely by events: ``handle(event) -> list[BusEvent]``.
Given the same events, config and seed it produces the same transmissions,
which is what makes replay-based scenario tests possible. Replies are
scheduled a short, seeded delay after the pilot's transmission and go out on
the first event at or after their due time.
"""

import itertools
import logging
import math
import random
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import msgspec

from localtc.atc_core import chatter, personality, procedures, radio_range, vectors
from localtc.atc_core import region as regions
from localtc.atc_core.airport import (
    AirportGeometry,
    TaxiGraph,
    published,
    select_runway,
)
from localtc.atc_core.airport import fixes as airport_fixes
from localtc.atc_core.airport import gates as stands
from localtc.atc_core.airport import real_gates
from localtc.atc_core.airport.approaches import LOCALIZER as LOCALIZER_KINDS
from localtc.atc_core.airport.approaches import (
    Aircraft,
    Outages,
    choose_approach,
    circle_to,
)
from localtc.atc_core.airport.approaches import options as approach_options
from localtc.atc_core.airspace import Airspace, Area
from localtc.atc_core.diversion import DiversionMixin, DiversionState
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
from localtc.atc_core.llm.phrase import facts_problem, spoken_as
from localtc.atc_core.llm.triggers import is_question, question_topic
from localtc.atc_core.phase import FlightPhase, PhaseThresholds, PhaseTracker
from localtc.atc_core.phraseology import TemplateLibrary, speech
from localtc.atc_core.readback import (
    ChainInterpreter,
    GrammarInterpreter,
    Interpretation,
    InterpretContext,
    Interpreter,
    PendingReadback,
    SayAgainInterpreter,
)
from localtc.atc_core.readback.interpreter import reading
from localtc.atc_core.readback.callsign_check import judge as judge_callsign
from localtc.atc_core.readback.extract import frequencies, same_approach, without_callsign
from localtc.atc_core.readback.intents import start_only, tail_side
from localtc.atc_core.readback.intents import match_intents
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.readback.questions import asks
from localtc.atc_core.route import Route, RouteFix
from localtc.atc_core.session import (
    Clearance,
    Exchange,
    IssuedInstruction,
    SessionSnapshot,
    SessionState,
    snapshot,
)
from localtc.atc_core.values import (
    APPROACH_KINDS,
    Approach,
    Callsign,
    Phrase,
    Wind,
    clean_sim_name,
)
from localtc.atc_core.atis import real as real_atis
from localtc.atc_core.atis.observation import from_report
from localtc.atc_core.atis.real import with_magvar
from localtc.atc_core.vfr import VfrMixin, VfrState
from localtc.atc_core.weather import (
    AtisBoard,
    AtisInfo,
    WeatherTracker,
    components,
    magnetic_wind,
)
from localtc.sim_api import (
    AircraftIdentity,
    Airport,
    AirportData,
    AtisReport,
    WeatherReport,
    AtcAlert,
    AtcDecision,
    AtcThinking,
    AtcTransmission,
    AtisBroadcast,
    BusEvent,
    FlightArrived,
    LlmExchange,
    NearbyAirports,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    RadioChatter,
    RadioTuned,
    ReadbackEvaluated,
    SimLifecycle,
    TrafficSnapshot,
    TrafficTarget,
    Transcript,
)

P = FlightPhase
DEPARTURE_PHASES = {P.PARKED, P.PUSHBACK, P.TAXI_OUT, P.RUNWAY_HOLD, P.TAKEOFF, P.DEPARTURE, P.CRUISE}
PHONETIC_LETTERS = {"alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F",
                    "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L",
                    "mike": "M", "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S",
                    "tango": "T", "uniform": "U", "victor": "V", "whiskey": "W", "xray": "X", "yankee": "Y", "zulu": "Z"}
FREQUENCY_CHANGE = re.compile(r"\b(?:request(?:ing)?|can we|could we|like|want|ready)\b.*\b(?:frequency change|switch(?:ing)?|"
                              r"change|radio|frequency|over|go|contact)\b(?: over)? to (?:the )?"
                              r"(tower|ground|approach|departure|center)\b", re.I)
WEATHER_WORDS = {"weather", "wind", "winds", "metar", "conditions", "visibility", "altimeter", "temperature"}
SIM_FRESH_S = 1800.0  # the sim's own weather at an airport, observed this recently, beats any real-world report
ON_COURSE_MIN_NM = 5.0  # a route fix closer than this is behind or abeam already: departure sends it to the next
RESERVED_SQUAWKS = {"1200", "1202", "1255", "1276", "1277", "2000", "4000", "0000"}
PTT_TIMEOUT_S = 30.0  # a PttPressed without a release can't silence ATC forever
NM_M = 1852.0
AIRBORNE_PHASES = {P.DEPARTURE, P.CRUISE, P.ARRIVAL, P.APPROACH}
TAXI_MAP_PHASES = {P.PARKED, P.PUSHBACK, P.TAXI_OUT, P.RUNWAY_HOLD, P.TAXI_IN}  # the taxi route is on the map
SILENT_PILOT_S = 30.0  # an instruction unanswered this long: "how do you read?", then once more, then give up
NUDGE_REPLY_S = 4.0  # after the pilot answers "how do you read", the instruction follows this soon
STALE_READBACK_S = 45.0  # answered with something else and then quiet this long: repeat it once, then stop waiting
MISSED_CHECKIN_S = 45.0  # on the new frequency but quiet this long: the controller calls first
NOT_SWITCHED_S = 45.0  # still on the old frequency this long after reading back a handoff: say it again
TRAFFIC_NM, TRAFFIC_ALT_FT, TRAFFIC_REPEAT_S = 5.0, 1200.0, 300.0
TRAFFIC_MIN_KT = 40.0  # slower than this and not on the ground: a sim glitch, not something flying
TRAFFIC_FIELD_NM, TRAFFIC_FIELD_AGL_FT = 5.0, 1000.0  # this low this close to an airport: the tower's, no call
STANDBY_S = (40.0, 90.0)  # after "stand by for your clearance", the clearance this long later
INITIAL_MIN_FT = 4000  # the initial altitude of an IFR clearance: at least this ...
INITIAL_STEPS_FT = (0, 1000, 2000, 4000, 5000)  # ... a few steps above 3,000 ft over the field ...
INITIAL_MAX_FT = 10000  # ... and no higher than this (unless the field itself is that high)
STANDBY_CHANCE = 0.3  # clearance delivery sometimes has to go get it
MAX_TAILWIND_REQUEST_KT = 10.0  # a pilot's runway request is granted up to this much tailwind
MAX_READBACK_ATTEMPTS = 3  # then ATC repeats the instruction once more and stops asking
STT_WAIT_S = 8.0  # with voice input: how long ATC waits after push-to-talk for the transcript
VECTOR_FROM_NM = 45.0  # approach starts vectoring within this of the field
VECTOR_GAP_S = 45.0  # quiet between one vector or speed instruction and the next
DESCEND_GAP_S = 120.0
INTERCEPT_GAP_S = 15.0  # the intercept waits no longer than this after the last instruction
STAR_CLEAR_NM = 25.0  # on a STAR that runs onto the final: cleared for the approach this far (along it) from the join
STAR_END_NM = 1.0  # on a STAR that ends off the final: vectors start this close to its last fix
TOWER_AT_NM = 6.0  # cleared on the intercept, the arrival goes to tower about this far out (plus a margin)  # an altitude step on its own no more often than this
CROSSING_CLEARANCE_M = 90.0  # how close to a hold-short point the clearance to cross comes (200 m came while still
# taxiing out of the gate area: "I wasn't even near it")
GIVE_WAY_NM = 0.15  # traffic this close on the ground is close enough to wait for
GIVE_WAY_MOVING_KT = 3.0  # both have to be moving for one to be in the other's way
GIVE_WAY_AHEAD_DEG = 60.0  # how far off the nose it can be and still be in front
GIVE_WAY_CROSSING_DEG = 45.0  # anything straighter than this is going our way, not across us
GIVE_WAY_GAP_S = 90.0  # quiet between one of these and the next
GIVE_WAY_RUNWAY_M = 30.0  # traffic on a runway, or this close to its edge, is taking off or landing: not in the way
GIVE_WAY_MEET_S = 45.0  # both reach the point where their ways cross within this long ...
GIVE_WAY_MEET_GAP_S = 20.0  # ... and within this long of each other
GIVE_WAY_CLOSEST_M = 80.0  # ... passing closer than this (wing tips 35 m out each side)
GIVE_WAY_TURNING_DEG_S = 3.0  # turning faster than this on the ground: the nose isn't where it's going
GIVE_WAY_SURE_KT = 6.0  # taxiing at least this fast, square across: then the direction is said
HANDOFF_LISTENING_S = 120.0  # the controller who handed a flight off still hears it on the old frequency this long
AT_HOLD_M = 80.0  # this close to a runway's hold line is holding short of it
AT_RUNWAY_END_M = 250.0  # ... or this close to its threshold
FINAL_OWNED_NM = 10.0  # this flight on final this close: tower isn't sending anybody else onto that runway
MOVING_KT = 2.0  # traffic seen going this fast has somebody flying it
AIRLINE_CALLSIGN = re.compile(r"[A-Z]{3}\d{1,4}[A-Z]{0,2}")  # "SKW5775": an AI flight, even before it moves
REGISTRATION = re.compile(r"N\d[0-9A-Z]{1,4}|[A-Z]{1,2}-?[A-Z]{3,4}|C-?[FG][A-Z]{3}")  # N172LT, G-ABCD, C-FABC
STOPPED_AHEAD_M = (30.0, 250.0)  # an aircraft stopped on the path this far ahead gets a caution ...
STOPPED_AHEAD_WIDTH_M = 30.0  # ... when it is this close to the line the aircraft is taxiing along
STOPPED_AHEAD_OFF_ROUTE_M = 120.0  # with no route to follow, only this far along the nose
PARKED_AHEAD_WIDTH_M = 10.0  # a parked aircraft this close to straight ahead of the nose ...
PARKED_AHEAD_S = 4.0  # ... for this long is one the taxi is heading into
# The model missed a call for one of these, and the grammar had nothing: ask it again with time to spare before
# "say again" (which is for when the model couldn't make it out either).
PLAIN_QUESTION_WORDS = 6  # more than this, besides the call-up and courtesy, is asking more than a topic
COURTESY = {"please", "thanks", "thank", "you", "very", "much", "hey", "hi", "hello", "good", "morning", "afternoon",
            "evening", "day", "and", "uh", "um", "er", "sir", "ma'am"}
# Words that ask for more about the topic than its word: "how long is runway 24R" is not "which runway".
QUALIFIERS = {"long", "length", "wide", "width", "far", "distance", "why", "when", "elevation", "surface", "closed", "open",
              "until", "delay", "delays", "busy", "whether", "else", "other", "another"}
HOW_QUALIFIERS = {"many", "much", "big", "high", "old"}  # after "how" ("how many", never "thank you very much")
# Replies the model doesn't reword: nothing to them but the callsign and a word, or already the model's or the data's.
NO_REWORD = {"common.say_again", "common.station_say_again", "common.roger", "common.readback_correct", "common.info",
             "common.unable"}
READ_YOU = re.compile(r"\b(?:loud and clear|five by five|5 by 5|read(?:ing)? you|have you|got you|hear you)\b", re.I)
GA_PARKING = "ramp_ga"  # the scenery's GA parking kinds (ramp_ga_small, _medium, _large)
TAXI_IN_IDS = ("ground.taxi_to_gate", "ground.taxi_in")  # the taxi to the gate: done once parked at one
ARRIVED_S = 5.0  # stopped at a gate at the destination this long: the flight is over (``_arrived_at_gate``)
DESTINATION_NM = 3.0  # this close to the destination airport's reference point is at it
PLACE_WORDS = {"at", "in", "near", "around", "over"}  # "... at <a place>": here only if the place is
REFUSED = re.compile(r"\W*(?:no|negative|nope)\b", re.IGNORECASE)  # a call that starts by saying no to ATC
CANCEL_WORDS = {"disregard", "cancel", "scratch", "abort", "withdraw", "belay"}  # "disregard the request for the descent"
CANCEL_FILLER = {"this", "that", "transmission", "last", "call", "my", "the", "sorry", "current", "previous", "one", "it"}
CANCEL_WINDOW_S = 180.0  # an altitude change given for the pilot's request can be taken back this long after
CUT_OFF = re.compile(r"\w[-\u2014\u2013]\s*$")  # speech-to-text's mark for a word cut off at the end: "but sh-"
NEARBY_TRAFFIC_NM = 0.5  # "any traffic around?" on the ground: what's taxiing this close
# The facts the phrasing model is given only when the pilot's words are about them (``_facts``).
FACT_TOPICS = {
    "lengths": {"long", "length", "wide", "width", "feet", "size", "longest", "shortest"},
    "notices": {"closed", "closure", "closures", "open", "notam", "notams", "notice", "notices", "construction",
                "restricted", "restriction", "works", "maintenance", "affecting", "available", "unavailable"},
    "elevation": {"elevation", "high"},
    "runways": {"landing", "departing", "departures", "arrivals", "parallel", "runways", "simultaneous", "using"},
    "traffic": {"traffic", "aircraft", "plane", "planes", "busy", "ahead", "queue", "line", "behind"},
    "pushback": {"tail", "tails", "tailing", "push", "pushback", "pushing"},
    "squawk": {"squawk", "code", "transponder"},
}
NEARBY_NM = 20.0  # the departure or destination airport this close: the sim's weather here is its weather
CALLSIGN_ASKED_S = 60.0  # "say again your callsign" -- the callsign, this soon after: the earlier call was this flight's
CONVERSATION_S = 180.0  # this flight and this controller talked this recently: still the same conversation
CONVERSE_WORDS = 4  # a clear call this long that no procedure fits: in the model's modes, the model replies to it
CONVERSE_CONFIDENCE = 0.6  # ... when speech-to-text was this sure of the words (below: "say again" is the honest reply)
SAID = {"common.say_again": "say again", "common.roger": "roger", "common.pleasantry": "the script's small talk",
        "common.unable": "unable"}
WHAT_WORDED = {"answer": "answer", "reply": "reply", "decline": "decline"}
UNAVAILABLE = Phrase("unable, that information is not available", "unable, that information is not available")
# Words that put a question somewhere else, or later, than the sim's data here and now.
ELSEWHERE = {"route", "enroute", "en", "along", "ahead", "destination", "forecast", "forecasted", "tomorrow", "later",
             "there", "at", "in", "near", "around", "over", "arrival", "arriving"}
CONVERSATIONAL_TRIGGERS = {"question", "out_of_grammar", "parser_failure", "low_confidence", "ambiguous", "compound",
                           "self_correction", "hesitation", "callsign", "non_numeric", "out_of_phase", "digits_unsure"}
LONG_STATEMENT_WORDS = 8  # a call this long that nothing understood is the pilot talking, not a garbled readback
CHATTER_QUIET_S = 12.0  # the frequency quiet this long before somebody else talks
CHATTER_TURN_S = 1.5  # between the controller's call and the other aircraft's readback
RANGE_TOLD_S = 60.0  # "out of range" said no more often than this per station
# What the language model is shown of the recent past: ATC's last line to this flight on this frequency, and the
# traffic ATC called, only this recent. Older is another moment (a Center sector revisited an hour later).
MODEL_LAST_ATC_S = 300.0
MODEL_RECENT = 4  # exchanges on this frequency the model is shown
MODEL_TRAFFIC_S = 180.0
CHECKIN_ACK_S = 120.0  # an altitude report this soon after the controller spoke is only acknowledging it
HANDOFF_ACK_WORDS = {"roger", "wilco", "switching", "over", "good", "day", "night", "bye", "goodbye", "cheers", "thanks",
                     "later", "long", "contact", "contacting"}  # "so long", "good day", "over to Toronto"
TAXI_SETTLE_S = 20.0  # the first seconds of the taxi out of a pushback: heading still swinging
STAND_CLEAR_M = 25.0  # this close to a parking spot, the aircraft is still at (or just off) its gate
GO_AROUND_NM = 1.5  # short final: an aircraft still on the runway means going around
# Landing traffic this close on final and a departure waits for it: 4 nm (about 90 s at approach speed)
# with the runway empty, 6 nm when it first has to be vacated, 2.5 nm once lined up and ready to roll.
DEPARTURE_ARRIVAL_NM, OCCUPIED_ARRIVAL_NM, LINED_UP_ARRIVAL_NM = 4.0, 6.0, 2.5
FLOW_KT = 50.0  # traffic this fast along a runway, low over it or on it, is taking off or landing on it
FLOW_WINDOW_S = 1200.0  # the traffic's runway direction from the takeoffs and landings seen in this long ...
FLOW_MOVES = 2  # ... at least this many of them, and twice as many that way as the other
CLIMBING_FPM = 300.0  # traffic climbing faster than this low over a runway has just taken off
OVER_RUNWAY_FT = 500.0  # airborne this low over a runway: landing on it (or just off it), the runway isn't free
LANDING_ZONE_M = 1800.0  # the part of the runway a landing uses: traffic further on and moving is off it in time ...
RUNWAY_VACATING_KT = 15.0  # ... at this speed or more
CROSSING_ARRIVAL_NM = 2.5  # traffic this close in on a runway that crosses the departure runway goes first
ROLLING_KT = 20.0  # on the ground at this speed or more: rolling along a runway, not taxiing
ROLLING_ONTO_S = 30.0  # rolling onto the departure runway within this long: it isn't free
STOP_KT = 80.0  # on the takeoff roll below this, tower stops it for an aircraft about to be on the runway ahead
LANDING_BEHIND_M = 1500.0  # landed traffic ahead this far down the runway as this one crosses the threshold: spaced
# (about 5,000 ft: between the FAA's 4,500 and 6,000 ft for jets landing behind each other; the positions are seconds old)
KT_TO_MS = 0.514444
RUNWAY_CLEAR_KT = 40.0  # faster than this on the runway and it is getting off it
SEQUENCE_NM = 12.0  # where the landing order is given
SEQUENCE_ALT_FT = 3000.0  # traffic within this of our altitude counts as being on the same final
MAX_ENDURANCE_MIN = 20 * 60  # beyond this the numbers are not telling us anything useful
EMERGENCY_LAND_NM = 25.0  # with an emergency running, tower clears the landing from this far out
MIN_TURN_DEG = 12.0  # a smaller correction isn't worth a transmission
REVECTOR_DEG = 20.0  # a new vector has to differ from the one already given by at least this much
SPEED_CONTROL_MIN_KT = 200.0  # slower than this and there is nothing to manage
SPEED_GATES = ((30.0, 250, "speed250"), (18.0, 210, "speed210"), (10.0, 180, "speed180"))
# Speeds are the pilot's own unless there's a reason: 250 knots below 10,000 feet (the rule) and the slower ones for
# traffic landing ahead (another aircraft this close to the airport, nearer it and lower). Slowing every arrival to
# 210 and 180 with nobody around was "unrealistic".
SPEED_LIMIT_BELOW_FT = 10000.0
SPEED_TRAFFIC_NM, SPEED_TRAFFIC_AGL_FT = 25.0, 8000.0
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
PUSHBACK_BEHIND_DEG = 155.0  # a route point this far round is behind the tail: it doesn't say left or right ...
PUSHBACK_LOOK_FAR_M = 150.0  # ... unless nothing nearer does
PUSHBACK_STRAIGHT_DEG = 25.0  # the route this close to straight ahead: push straight back
HEADING_TURN_S = 40.0  # time to turn onto a heading given before it's checked
HEADING_OFF_DEG = 30.0  # further off it than this ...
HEADING_OFF_S = 20.0  # ... for this long: "check heading"
HEADING_CHECKS = 2  # said this often for one heading, then left
ALTITUDE_CHECKS = 2  # "check altitude" this many times for one assigned altitude, then something else
MAX_OFFERED_FT = 41000  # the highest level ATC gives a flight asking for higher
DEPARTURE_TOP_FT = 17000  # departure climbs a flight this high and no higher: the centres above do the rest
# ... in steps: on radar contact a few thousand feet above the clearance's altitude, and the next as the flight nears
# each one (a flight still climbing to 5,000 was given 17,000 straight away, the same every time). Each controller and
# flight its own, so no two departures climb alike.
DEPARTURE_STEPS_FT = (6000, 8000, 10000, 12000)  # (3,000 at a time: "too small, would really congest the ATC")
DEPARTURE_REACHING_FT = 2000  # this close below its altitude (climbing), a departure gets the next step at once
CENTER_STEPS = (23000, 24000, 26000, 28000)  # a centre's usual intermediate level on the way up (one each)
CENTER_STEP_GAP_FT = 4000  # ... when the cruise is at least this far above it
CLIMB_REACHING_FT = 1000  # within this of the level given, the next climb is on its way ...
CLIMB_WAIT_S = (10.0, 60.0)  # ... after this long (each centre its own pace), so it doesn't have to level off
DEPARTURE_ENDS_FT = 15000.0  # above this the departure controller hands the climb to a centre
DEPARTURE_ENDS_NM = 40.0  # or this far from the field, whichever comes first
# Calls that never open with "good afternoon": repeats, corrections, alerts, handoffs.
NO_GREETING = ("say_again", "how_read", "read_back", "negative", "confirm", "traffic", "handoff", "contact", "unable",
               "stopped_ahead", "give_way", "readback_correct", "did_you_copy", "common.info", "common.roger")
TOWER_SWITCH_S = 60.0  # a flight sent to tower that read it back has this long to switch before being sent around
ESTABLISHED_M = 600.0  # this close to a final's centreline, pointing down it, is established on it
SIDESTEP_NM = 2.0  # lined up on a parallel runway closer in than this: that's the runway it's landing on
DESCENT_ASK_MIN = 15.0  # a pilot asking for the descent this long before the top of it gets the descent clearance
SECTOR_NEAREST_NM = 150.0  # an airport further away than this names no centre for where the flight is
SECTOR_MIN_S = 1200.0  # shortest time on one enroute centre before being handed to the next
SECTOR_LAST_NM = 250.0  # inside this of the destination the arrival takes over; no more sector changes
SECTOR_DWELL_S = 30.0  # across a real centre boundary this long before the handoff: not skimming it
CROSSING_TREND_S = 2.0  # how far apart two looks at the distance to a runway's hold short line are ...
CROSSING_CLOSING_M = 3.0  # ... and how much closer the second has to be: heading for it
SECTOR_BACK_S = 1800.0  # back into the centre left less than this long ago ...
SECTOR_BACK_DWELL_S = 600.0  # ... takes this long in there before it's handed back to
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
    standby_s: tuple[float, float] = STANDBY_S  # after "stand by for your clearance", the clearance this long later
    destination: str | None = None
    cruise_ft: int | None = None
    callsign: str | None = None  # overrides the sim's ATC ID
    rules: str = "IFR"
    center_name: str = "Seattle"
    center_mhz: float = 125.1
    strict_callsign: bool = False
    chatter: bool = False  # other flights on the frequency now and then (atc_core/chatter.py); the app turns it on
    radio_range: bool = True  # an airport's frequencies reach only so far (atc_core/radio_range.py)
    callsign_check: bool = True  # another aircraft's callsign (or a near miss on a new call): "say again your callsign"
    # Each station a controller of their own (personality.py): their greetings, acknowledgements, corrections, their
    # manner in the model's words and their pace on the voice. Off: the plain greetings and sign-offs only.
    personalities: bool = True
    shift: int = 0  # which shift is on ([atc] shift): other controllers at every station after a 5-hour break
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
    dep_runway: str | None = None  # the flight plan's runways: given only with enforce_fpln_runways
    arr_runway: str | None = None
    enforce_fpln_runways: bool = False  # off: the runway in use (ATIS, else wind); on: the plan's
    traffic_runways: bool = True  # the runway in use the way the sim's own traffic is going (``_watch_runway_flow``)
    airport_fixes: str | None = None  # the pilot's own airport_fixes.toml, over the shipped one
    route: tuple[RouteFix, ...] = ()  # the plan's fixes: when the climb ends, where the descent begins
    transition_ft: int = 0  # 0 = the region's transition altitude (atc_core.region); otherwise this everywhere
    phraseology: str = "auto"  # "auto": FAA or ICAO by where the controller is; or "faa" / "icao" always
    notams: bool = False  # notices on the ATIS, which ATC works to (the app's default is on: [atc] notams)
    squawk_per_flight: bool = True  # the squawk differs each flight; off: from the callsign alone (older recordings)
    atis_source: str = "hybrid"  # sim, real, hybrid (``_atis_inputs``)
    destination_weather: bool = True  # a far airport's runway from its weather or calm; off: the wind here (older ones)
    gate_radius_m: float = 30.0  # this close to a gate or parking spot at the destination is parked at it (``arrived``)


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
    reply: bool = False  # an answer to the pilot (goes out with the sim paused; ATC's own calls wait)
    worded_by: str = "template"  # "model": ``worded`` is the language model's words for it (checked), said instead
    worded: Phrase | None = None  # ... without the callsign, which the template gives
    lead: Phrase | None = None  # said first, after the callsign: "sorry, Gate 403 is occupied"


class AtcEngine(VfrMixin, DiversionMixin):
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
        self.libraries = {"faa": library or TemplateLibrary.load(), "icao": TemplateLibrary.load(style="icao")}
        self.region = regions.FAA  # the phraseology region of the current step (see _region)
        self.vfr = VfrState()  # what a VFR flight asked for (atc_core/vfr.py)
        self.diversion = DiversionState()  # the airports around, and a diversion (atc_core/diversion.py)
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
        if self.cfg.airport_fixes:  # the pilot's own corrections to the sim's airport data
            self.tracker.context_builder.fixes = airport_fixes.load(self.cfg.airport_fixes)
        self.facilities: list[Facility] = []
        self.airport_requests: list[str] = []  # airports the engine needs; the service fetches them
        # The real gates of an airport (``localtc.gate_data``), or None while unknown; None: the scenery's names only.
        self.gate_source: Callable[[str], real_gates.GateData | None] | None = None
        self._taxiways_named: set[str] = set()  # airports whose unnamed taxiways were named from the real ones
        self._taxi_in_route: tuple[str, ...] | None = None  # the route to the gate ground gave, said again if asked
        self._gates_taken: set[int] = set()  # gates the pilot said (or the traffic showed) are taken: not given again
        self._atis_told: set[tuple[str | None, str]] = set()  # (airport, letter) ATC has told the pilot is current
        self._requested: set[str] = set()
        self._scheduled: list[_Scheduled] = []
        self._rng: random.Random | None = None
        self._was_on_runway: bool | None = None  # None until the first tick
        self._ptt_since: float | None = None  # set while the pilot holds push-to-talk
        self._stt_since: float | None = None  # set from push-to-talk release until its transcript arrives
        self._tx_mhz: float | None = None  # the frequency the pilot keyed the mic on
        self._radio_busy_until = -math.inf  # voice out: until ATC (or the copilot) has finished speaking
        self._departure_override: str | None = None  # a departure runway the pilot asked for
        self._approach_kind: Approach | None = None  # an approach the pilot asked for (and got)
        self._going_around = False
        self._traffic: dict[int, TrafficTarget] = {}
        self._traffic_nm: dict[int, float] = {}  # distance at the previous snapshot, to see who's closing
        self._traffic_called: dict[int, float] = {}  # object id -> when ATC last called it
        self._last_traffic: tuple[float, str] | None = None  # (when, "2 o'clock, 3 miles, ...") of the last call
        self._tuned_since = 0.0
        self._last_handoff: tuple[float, Facility, Facility] | None = None  # (when, from, to) of the last handoff
        self._rehanded: set[tuple[str, str]] = set()  # handoffs already said a second time
        self._crossings: tuple[str, ...] = ()  # runways the taxi route goes across
        self._crossing_seen: dict[str, tuple[float, float]] = {}  # runway: (t, metres to its hold short line) lately
        self._crossed: set[str] = set()  # ... and the ones already cleared to cross (full names, "09/27")
        self._crossing: str | None = None  # the runway cleared across and not yet left behind
        self._via_floor: int | None = None  # the altitude a "descend via" ends at, while it is the clearance
        self._takeoff_wait: str | None = None  # the runway a departure is being held for
        self._takeoff_hold: str | None = None  # why: "arrival" (holding short) or "occupied" (lined up)
        self._expected: dict[str, dict[str, Any]] = {}  # every value each instruction gave, by instruction id
        self._was_crossing = False
        self._sector: Facility | None = None  # the enroute centre working the airspace the flight is in
        self._gave_way_t = -math.inf  # when ground last held this aircraft for another
        self._cautioned: set[int] = set()  # aircraft already called as stopped ahead
        self._gave_way_to: set[int] = set()  # aircraft this one was told to give way to (once each)
        self._last_heading: tuple[float, float] | None = None  # (t, true heading) at the last ground check
        self._seen_moving: set[int] = set()  # traffic seen under way at least once: not parked scenery
        self._dead_ahead: dict[int, float] = {}  # parked aircraft off the nose, and since when
        self._seen_airborne: set[int] = set()  # ... and seen flying: on the ground again, it's taxiing in
        self._traffic_t = 0.0
        self._flow_moves: list[tuple[float, str, float]] = []  # (when, airport, true heading) of AI takeoffs and landings
        self._flow_seen: set[tuple[str, int]] = set()  # (airport, aircraft) counted once each
        self._traffic_before: dict[int, TrafficTarget] = {}  # the snapshot before, for who's climbing and descending
        self._traffic_before_t = 0.0
        self._chatter_used: set[tuple[str, str]] = set()  # (callsign, station): each is heard once per controller
        self._greeted: set[str] = set()  # stations that have had their first word with the flight
        self._squawk_code: str | None = None  # the flight's code, once given (_squawk)
        self._reports: dict[str, WeatherReport] = {}  # each airport's latest METAR (WeatherReport)
        self._real_atis: dict[tuple[str, str], real_atis.RealAtis] = {}  # (airport, kind): its real ATIS, read
        # Greetings and sign-offs draw from their own generator: they never shift which wording a template gets.
        self._voice_rng = random.Random(self.cfg.seed or 0)
        self._persona_rng = random.Random((self.cfg.seed or 0) + 7919)  # the controllers' own choices: their own stream
        self._said_on: list[tuple[float, str]] = []  # (when, station): every transmission heard, for the workload
        self._met: set[str] = set()  # stations whose controller has been logged
        self._vector_t = -math.inf  # when approach last gave a vector or a speed
        self._vector_leg: str | None = None  # the leg of the pattern approach last gave (vectors.py)
        self._vector_side: float | None = None  # ... and the side of the final it is flown on
        self._vectors_named = False  # "vectors ILS 32" said: the headings after it are just headings
        self._descend_t = -math.inf  # when approach last stepped the altitude down
        self._sector_since = -math.inf
        self._sector_next: tuple[str, float] | None = None  # a new centre's area entered, and since when
        self._sector_left: tuple[str, float] | None = None  # the centre handed away from, and when
        self._repeated: set[str] = set()  # instructions already said a second time for a silent pilot
        self._nudged: set[str] = set()  # ... and the ones already asked "how do you read" about
        self._deviation_since: float | None = None
        self._granted: tuple[float, int, int | None] | None = None  # (when, altitude before, cruise before) of a request granted
        self._heading_given: tuple[float, int, int] | None = None  # (when, heading, "check heading" said how often)
        self._heading_off_since: float | None = None
        self._altitude_checked_t = -math.inf
        self._altitude_checks: dict[int, int] = {}  # "check altitude" calls made, by assigned altitude
        self._checked_in: set[str] = set()  # stations the pilot has checked in with (or that called first)
        self._pending_alerts: list[BusEvent] = []  # raised inside a check that only answers yes or no
        self.weather = WeatherTracker()
        self.atis = AtisBoard(self.cfg.seed, notams=self.cfg.notams,
                              region=regions.forced(self.cfg.phraseology, self.cfg.transition_ft))
        self._atis_checked_t = -math.inf
        self._atis_tuned: str | None = None  # airport whose ATIS COM1 is on
        self._t = 0.0
        self._answering = False  # handling a pilot's transmission: what's scheduled is a reply
        self._chatter: chatter.Chatter | None = None
        self._chatter_due: float | None = None  # when somebody else may next be heard on the frequency
        self._chatter_station: str | None = None
        self._routes_cache: dict[str, list[tuple[str, ...]]] = {}
        self._range_told: dict[str, float] = {}  # station -> when the pilot was last told it's out of range
        self._reaching_t: float | None = None  # when the climb got near the level a centre gave
        self._taxi_path: tuple[str, list[tuple[float, float]]] | None = None  # (airport, points) of the route ground gave
        self._deferred: Transcript | None = None  # the model missed it once: it gets a second, longer look
        self._deferred_facility: Facility | None = None  # ... asked of this controller
        self._patient = False  # answering that one now
        self._interp: Interpretation | None = None  # what the pilot's call was read as (for ``AtcDecision``)
        self._not_used: list[str] = []  # what of the model's wasn't used on this call, and why
        self._arrived_since: float | None = None  # stopped at a gate at the destination since then
        self._arrived = False  # ... and said so (once a flight)
        self._landed = False  # landed and taxiing in, this session
        self._callsign_asked: tuple[Transcript, Facility] | None = None  # the call that got "say again your callsign"

    # --- public ---------------------------------------------------------------------------

    @property
    def _altimeter_word(self) -> str:
        return "QNH" if self.region.icao else "altimeter"

    @property
    def library(self) -> TemplateLibrary:
        """The templates in the current region's wording, FAA or ICAO."""
        return self.libraries[self.region.style]

    def handle(self, event: BusEvent) -> list[BusEvent]:
        # Everything said in this step is worded for where the controller the pilot is talking to sits.
        self.region = self._region()
        with regions.speaking(self.region):
            return self._handle(event)

    def _region(self) -> regions.Region:
        """FAA or ICAO, and the transition altitude: from the facility tuned (its airport, or for a centre
        the FIR the aircraft is in), or else from where the aircraft is."""
        forced = regions.forced(self.cfg.phraseology, self.cfg.transition_ft)
        if forced is not None:
            return forced
        st = self.state
        tuned = st.comms.tuned
        own = st.aircraft
        code: str | None = None
        if tuned is not None and tuned.airport:
            code = tuned.airport
        elif own is not None and not own.on_ground and (area := self.center_area(own)) is not None:
            code = area.id
        elif own is not None and own.on_ground:
            code = st.flight.origin if st.phase in DEPARTURE_PHASES else st.flight.destination
        code = code or st.flight.origin or st.flight.destination
        found = regions.region_for(code)
        if self.cfg.transition_ft:
            found = regions.Region(found.style, self.cfg.transition_ft)
        return found

    def _handle(self, event: BusEvent) -> list[BusEvent]:
        self._t = max(self._t, event.t)
        out: list[BusEvent] = []
        if isinstance(event, AirportData):
            self.tracker.handle(event)
            self._rebuild_facilities()
        elif isinstance(event, AtisReport):
            if self.cfg.atis_source in ("real", "hybrid") and (parsed := real_atis.parse(event.text, event.kind)) is not None:
                self._real_atis[(event.icao, event.kind)] = parsed
        elif isinstance(event, WeatherReport) and self.cfg.atis_source != "hybrid":
            pass  # the METAR is used in hybrid only ([atc] atis_source)
        elif isinstance(event, WeatherReport):
            geo = self.geometry(event.icao)
            self.weather.report(event.icao, from_report(event, geo.airport.magvar if geo is not None else 0.0, event.t))
            self._reports[event.icao] = event
        elif isinstance(event, AircraftIdentity):
            if not self.cfg.callsign and event.atc_id:
                self.state.flight.callsign = Callsign.from_sim(event.atc_id, event.airline, event.flight_number, event.atc_type)
            elif self.cfg.callsign and self.state.flight.callsign is not None:
                # Keep the type for abbreviated callsigns ("Boeing 38B") even when the ident is overridden.
                self.state.flight.callsign = replace(self.state.flight.callsign, type_name=clean_sim_name(event.atc_type))
            self.state.flight.aircraft_type = clean_sim_name(event.atc_model)
        elif isinstance(event, SimLifecycle):
            self.tracker.handle(event)
        elif isinstance(event, NearbyAirports):
            self._on_nearby(event)
        elif isinstance(event, TrafficSnapshot):
            # Aircraft on the ground are kept too: they are the ones occupying a runway or crossing in
            # front of a taxiing aircraft. The advisory call is the only thing that wants them left out.
            if self._traffic:
                self._traffic_before, self._traffic_before_t = self._traffic, self._traffic_t
            self._traffic, self._traffic_t = {target.object_id: target for target in event.targets}, event.t
            self._seen_moving.update(target.object_id for target in event.targets if target.gs_kt >= MOVING_KT)
            if self.cfg.traffic_runways:
                self._watch_runway_flow(event.t)
            self._seen_airborne.update(target.object_id for target in event.targets if not target.on_ground)
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

    def taxi_path(self) -> dict[str, Any] | None:
        """The taxi route ground gave, for the map: the airport, where it goes ("runway 36R", "Gate A10") and its
        points as (lat, lon). None before one is given, or once the aircraft is off the ground."""
        st = self.state
        geo = self.geometry(self._taxi_path[0]) if self._taxi_path is not None else None
        if geo is None or st.phase is None or P(st.phase) not in TAXI_MAP_PHASES:
            return None
        arriving = "taxi_in" in st.clearances or P(st.phase) is P.TAXI_IN
        to = (st.assignments.gate or "parking") if arriving else f"runway {st.assignments.departure_runway or ''}".strip()
        return {"icao": geo.icao, "to": to, "points": [list(geo.frame.to_latlon(x, y)) for x, y in self._taxi_path[1]],
                "taxiways": list(st.assignments.taxi_route or ())}

    @property
    def radio_busy_until(self) -> float:
        """When ATC (or the copilot) finishes what it's saying now."""
        return self._radio_busy_until

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
        """The airport whose ATIS (or AWOS/ASOS) broadcasts on ``mhz``, if any: the nearest, if several do."""
        own = self.state.aircraft
        found = [(geo.distance_nm(own.lat, own.lon) if own is not None else 0.0, icao)
                 for icao, geo in self.tracker.context_builder.airports.items()
                 if any(f.kind in ATIS_KINDS and channel_khz(f.mhz) == channel_khz(mhz) for f in geo.airport.frequencies)]
        return min(found)[1] if found else None

    def _working_airport(self) -> str | None:
        """The airport whose controllers the flight is talking to: the origin until the arrival, then the destination."""
        arriving = self.state.phase is not None and P(self.state.phase) not in DEPARTURE_PHASES
        return self.state.flight.destination if arriving else self.state.flight.origin

    def _closed(self, icao: str | None, runway: str) -> bool:
        """The runway is closed (the airport's ATIS notices)."""
        info = self.current_atis(icao)
        return info is not None and info.closed(runway)

    def _taxi_graph(self, geo: AirportGeometry) -> TaxiGraph:
        """The airport's taxiways, routed around any its ATIS says are closed."""
        geo = self._with_taxiway_names(geo)
        info = self.current_atis(geo.airport.icao)
        closed = info.operations.closed_taxiways if info is not None and info.operations is not None else frozenset()
        return TaxiGraph(geo, closed=closed)

    @staticmethod
    def _atis_broadcasts(geo: AirportGeometry) -> dict[str, float]:
        """An airport's ATIS broadcasts: {"both": mhz}, or {"arrival": ..., "departure": ...} where a US airport has
        two (Denver 125.6 and 134.025, Los Angeles 133.8 and 135.65). The sim doesn't name them; a name that says
        ARR or DEP is taken at its word, otherwise the lower frequency is the arrival one. (Two elsewhere are
        usually the same ATIS in two languages, as at Montreal.)"""
        freqs = [f for f in geo.airport.frequencies if f.kind in ATIS_KINDS]
        if not freqs:
            return {}
        named = {("arrival" if "ARR" in f.name.upper() else "departure"): f.mhz for f in freqs
                 if "ARR" in f.name.upper() or "DEP" in f.name.upper()}
        if len(named) == 2:
            return named
        distinct = sorted({f.mhz for f in freqs if f.kind == "atis"})
        if len(distinct) == 2 and not regions.region_for(geo.airport.icao).icao and geo.airport.icao.startswith("K"):
            return {"arrival": distinct[0], "departure": distinct[1]}
        return {"both": freqs[0].mhz}

    def current_atis(self, icao: str | None, kind: str | None = None) -> AtisInfo | None:
        """The airport's current ATIS; where it has separate ones, the one for this flight there (arriving at its
        destination, departing its origin), or ``kind``."""
        if not icao:
            return None
        if (info := self.atis.current.get(icao)) is not None:
            return info
        if kind is None:
            arriving = self.state.phase is not None and P(self.state.phase) not in DEPARTURE_PHASES
            kind = "arrival" if icao == self.state.flight.destination and (arriving or icao != self.state.flight.origin) \
                else "departure"
        return self.atis.current.get(f"{icao}/{kind}")

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
            if geo is None:
                continue
            broadcasts = self._atis_broadcasts(geo) or {"both": 0.0}
            for kind, mhz in broadcasts.items():
                key = icao if kind == "both" else f"{icao}/{kind}"
                inputs = self._atis_inputs(icao, geo, kind, own)
                if inputs is None:
                    continue
                weather, real, source = inputs
                info = self.atis.update(icao, geo, weather, own.zulu_s, self._airport_name(icao), own.t, kind=kind,
                                        real=real, source=source)
                atis_reach = self._reach(Facility("atis", "ATIS", 0.0, icao), own)
                tuned_here = icao == self._atis_tuned and (kind == "both" or channel_khz(mhz) == channel_khz(own.com1_mhz))
                if tuned_here and (info is not None or force) and (atis_reach is None or atis_reach.in_range):
                    current = self.atis.current[key]
                    out.append(AtisBroadcast(t=own.t, airport=icao, station=current.name, frequency_mhz=mhz or own.com1_mhz,
                                             letter=current.letter, text=current.text, spoken=current.spoken,
                                             variants=current.variants, locale=regions.accent(icao),
                                             source=self.atis_source_text(current, own)))
        return out

    def _runway_end(self, icao: str | None, own: OwnshipState):
        """The runway ATC gives at an airport. One the pilot asked for; with "enforce FPLN runway assignments"
        on, the flight plan's (if the airport has it); otherwise the runway in use: its ATIS runway, or the
        best one for the wind."""
        geo = self.geometry(icao)
        if geo is None:
            return None
        if icao == self.state.flight.origin and self._departure_override and (end := geo.end(self._departure_override)):
            return end
        if self.cfg.enforce_fpln_runways:
            planned = self.cfg.dep_runway if icao == self.state.flight.origin else \
                self.cfg.arr_runway if icao == self.state.flight.destination else None
            if planned and (end := geo.end(planned)) is not None and not self._closed(icao, planned):
                return end
        if self.current_atis(icao) is None and own is not None:
            self._atis_now(icao, geo, own)  # not heard yet: the runway still comes from the ATIS it will say
        info = self.current_atis(icao)
        if info is not None and (end := geo.end(info.runway)) is not None:
            return end
        if own is not None and (own.on_ground or geo.distance_nm(own.lat, own.lon) <= NEARBY_NM
                                or not self.cfg.destination_weather):
            return select_runway(geo, own.wind_dir_true, own.wind_kt)
        # Far from it, with no weather for it: the calm-wind runway (an ILS, the longest), never the wind where the
        # aircraft is. Joplin's runway was chosen from the wind at FL300 ("expect RNAV runway 31").
        return select_runway(geo, 0.0, 0.0)

    def _atis_now(self, icao: str, geo, own: OwnshipState) -> None:
        for kind in self._atis_broadcasts(geo) or ("both",):
            if (inputs := self._atis_inputs(icao, geo, kind, own)) is not None:
                weather, real, source = inputs
                self.atis.update(icao, geo, weather, own.zulu_s, self._airport_name(icao), own.t, kind=kind,
                                 real=real, source=source)

    def _atis_inputs(self, icao: str, geo, kind: str, own: OwnshipState):
        """What an airport's ATIS is made from, by ``[atc] atis_source``: (weather, the real ATIS or None, where it's
        from). None: nothing known of it.

        - "sim": the simulator's weather, as LocalTC observes it.
        - "real": the airport's real ATIS (its letter, runways and weather) where it has one; else the simulator's.
        - "hybrid": the real ATIS, else LocalTC's own from the real METAR, else from the simulator.

        Never the real one over fresher conditions in the sim: once the aircraft has observed the airport itself
        (``SIM_FRESH_S``), the sim's weather is what the pilot is flying in, and the ATIS gives it, saying when the
        real report differs."""
        airports = self.tracker.context_builder.airports
        mode = self.cfg.atis_source
        sampled = self.weather.samples.get(icao)
        fresh = sampled is not None and own.t - sampled.t <= SIM_FRESH_S
        real = None
        if mode in ("real", "hybrid"):
            arriving = icao == self.state.flight.destination and icao != self.state.flight.origin
            wanted = (kind,) if kind != "both" else ("both", "arrival" if arriving else "departure")
            real = next((self._real_atis[(icao, k)] for k in wanted if (icao, k) in self._real_atis), None)
        if fresh:
            weather = self.weather.surface(icao, airports, reports=False)
            differs = _differs(weather, real.weather if real is not None else None) or (
                mode == "hybrid" and _differs(weather, self.weather.reports.get(icao)))
            note = " (the real-world report differs)" if differs else ""
            return weather, (real if real is not None and not differs else None), ("simulator" + note, sampled.t, "")
        if real is not None and real.weather is not None:
            weather = with_magvar(real.weather, geo.airport.magvar)
            weather = replace(weather, t=own.t, elevation_ft=geo.airport.elev_ft,
                              altimeter_inhg=weather.altimeter_inhg or self.weather.altimeter_at(icao))
            return weather, real, ("real ATIS", None, real.zulu)
        if mode == "hybrid" and icao in self.weather.reports:
            report = self._reports.get(icao)
            return self.weather.surface(icao, airports), None, ("METAR", None, (report.observed if report else "").rstrip("Z"))
        weather = self.weather.surface(icao, airports, reports=False)
        if weather is None:
            return None
        return weather, None, ("simulator", weather.t, "")

    def atis_source_text(self, info: AtisInfo, own: OwnshipState | None) -> str:
        """Where an ATIS is from and how old: "real ATIS 1756Z, 12 min old", "simulator, observed 3 min ago"."""
        label, observed_t, zulu = info.source
        if zulu and own is not None and own.zulu_s is not None and zulu.isdigit():
            age = (int(own.zulu_s // 60) - (int(zulu[:2]) * 60 + int(zulu[2:]))) % 1440
            return f"{label} {zulu}Z, {age} min old"
        if observed_t is not None and own is not None:
            return f"{label}, observed {max(0, round((own.t - observed_t) / 60))} min ago"
        return label

    def _atis_note(self, icao: str | None, reported: str | None) -> Phrase | None:
        """"information Charlie is current, altimeter 29.92" when the pilot didn't report the current ATIS: once (FAA
        7110.65 2-9-3; ICAO the same), after which the pilot has it. The second taxi clearance said it all again."""
        info = self.current_atis(icao)
        if info is None or (reported or "").upper() == info.letter or (icao, info.letter) in self._atis_told:
            return None
        self._atis_told.add((icao, info.letter))
        parts = [("information {atis} is current", {"atis": info.letter})]
        if info.weather.altimeter_inhg is not None:
            parts.append((f"{self._altimeter_word} {{altimeter}}", {"altimeter": info.weather.altimeter_inhg}))
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
        altimeter = self.weather.altimeter_at(icao)
        if altimeter is None or icao is None:
            return None
        # Above the transition altitude everyone is on the standard setting and a local altimeter means
        # nothing yet; it comes with the descent through the transition level.
        own = self.state.aircraft
        if own is not None and own.alt_indicated_ft >= self.region.transition_ft:
            return None
        return self._phrases([(f"{_place(self._airport_name(icao))} {self._altimeter_word} {{altimeter}}", {"altimeter": altimeter})])

    def _phrases(self, parts: list[tuple[str, dict[str, Any]]]) -> Phrase:
        phrases = [Phrase(*self.library.fill(text, slots, context="note")) for text, slots in parts]
        return sum(phrases[1:], phrases[0])

    # --- telemetry ------------------------------------------------------------------------------

    def _flight_altitude(self, own: OwnshipState) -> OwnshipState:
        """The aircraft with the altitude ATC reads on its radar: up in the flight levels, the pressure altitude.

        Above the transition altitude everyone flies on the standard setting (29.92, 1013). The sim's own
        altimeter setting is often wrong up there: a pilot who left the local setting in, or an airliner whose
        avionics are on STD while the sim's altimeter setting still says 30.42 (Air Canada's CS300 at "36,447"
        at FL360). The pressure altitude comes from what the sim reads and the setting it reads with, and it
        is the level whatever the pilot set.
        """
        setting = own.altimeter_inhg
        if not 25.0 < setting < 33.0:
            return own
        pressure = own.alt_indicated_ft - (setting - 29.92) * 1000.0
        if pressure < self.region.transition_ft or abs(pressure - own.alt_indicated_ft) < 1.0:
            return own
        return msgspec.structs.replace(own, alt_indicated_ft=round(pressure))

    def _on_ownship(self, raw: OwnshipState) -> list[BusEvent]:
        st = self.state
        own = self._flight_altitude(raw)
        st.aircraft = own
        change = self.tracker.handle(raw)
        ctx = self.tracker.context
        out: list[BusEvent] = []

        if st.flight.origin is None and own.on_ground and ctx.airport is not None:
            st.flight.origin = ctx.airport.icao
            self._rebuild_facilities()
        dest = st.flight.destination
        if dest and dest not in self.tracker.context_builder.airports and dest not in self._requested:
            self._requested.add(dest)
            self.airport_requests.append(dest)
            self._real_gates(dest)  # asks for its real gates now: fetched long before the taxi in

        self.weather.update(raw, self.tracker.context_builder.airports)
        previous = (st.comms.tuned_mhz, st.comms.tuned, self._atis_tuned)
        st.comms.tuned_mhz = own.com1_mhz
        st.comms.tuned = self._facility_for(own.com1_mhz)
        atis = self._atis_airport(own.com1_mhz)
        if atis is not None and st.comms.tuned is not None and st.comms.tuned.airport not in (None, atis) \
                and st.comms.tuned.airport != self._working_airport():
            # Denver's ATIS is on 125.6, and so is one of Seattle Approach's frequencies: parked at Denver, it's the ATIS.
            st.comms.tuned = None
        self._atis_tuned = atis if st.comms.tuned is None else None
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
        if not self.tracker.paused:
            out += self._monitor(own)  # paused, the world stands still: no calls of ATC's own
        out += self._arrived_at_gate(own)
        out += self._pending_alerts
        self._pending_alerts = []
        return out

    def _arrived_at_gate(self, own: OwnshipState) -> list[BusEvent]:
        """Parked at a gate or stand at the destination after landing, the taxi in over: ``FlightArrived``, once a flight
        (the session stops the flight on it when [session] auto_stop_at_gate says to). Near the airport,
        on a runway or taxiway, at a gate at another airport, still rolling or with a taxi instruction still going:
        not yet."""
        st, t = self.state, own.t
        if self._arrived or not self._landed or st.flight.destination is None or st.phase != P.PARKED.value:
            self._arrived_since = None
            return []
        geo = self.geometry(st.flight.destination)
        stopped = own.on_ground and own.gs_kt < 1.0 and not own.on_runway and not self.tracker.context.on_runway
        # A taxi instruction going: one still to be said, or a ground instruction still to be read back. Not the taxi
        # in itself, at its end at the gate (LAX's gate 49: the readback of it was taken for a request, ATC said it
        # again, and the flight never stopped).
        owed = st.pending is not None and st.pending.instruction_id.startswith("ground.") \
            and not st.pending.instruction_id.startswith(TAXI_IN_IDS)
        taxiing = owed or any(item.instruction_id.startswith("ground.") for item in self._scheduled)
        gate = self._gate_at(geo, own) if geo is not None and geo.distance_nm(own.lat, own.lon) <= DESTINATION_NM else None
        if gate is None or not stopped or taxiing:
            self._arrived_since = None
            return []
        if self._arrived_since is None:
            self._arrived_since = t
        if t - self._arrived_since < ARRIVED_S:
            return []
        self._arrived = True
        return [FlightArrived(t=t, airport=st.flight.destination, gate=gate)]

    def _gate_at(self, geo: AirportGeometry, own: OwnshipState) -> str | None:
        """The gate or parking spot the aircraft is at (within [session] gate_radius_m of it, or its own size), or None."""
        here = geo.xy(own.lat, own.lon)
        near = [(math.dist(here, geo.xy(g.spot.lat, g.spot.lon)), g) for g in stands.gates(geo, self._real_gates(geo.icao))]
        near = [(d, g) for d, g in near if d <= max(g.spot.radius_m, self.cfg.gate_radius_m)]
        if near:
            return min(near, key=lambda dg: dg[0])[1].display
        spots = [p for p in geo.airport.parking
                 if math.dist(here, geo.xy(p.lat, p.lon)) <= max(p.radius_m, self.cfg.gate_radius_m)]
        return "parking" if spots else None

    def _on_phase_change(self, change: PhaseChanged, own: OwnshipState) -> list[BusEvent]:
        st, t = self.state, change.t
        out: list[BusEvent] = []
        phase = P(change.phase)
        if phase is P.TAXI_IN and change.previous == P.LANDING.value:
            self._landed = True  # down at the end of a flight: a gate now is the end of it
            # Down: whatever was still said to the aircraft in the air is over (Las Vegas repeated "go around" to a
            # flight already rolling out, then asked how it read).
            airborne = ("tower.go_around", "approach.", "center.", "departure.", "tower.land", "tower.continue",
                        "tower.sequence", "common.traffic", "common.climb", "common.descend")
            if st.pending is not None and st.pending.instruction_id.startswith(airborne):
                st.pending = None
            self._scheduled = [s for s in self._scheduled if not s.instruction_id.startswith(airborne)]
            self._going_around = False
        elif phase is P.DEPARTURE and change.previous == P.TAKEOFF.value:
            # Off the ground: what was still to be said about the runway is over (a takeoff clearance said again,
            # after a "did you copy?", with the aircraft already climbing out).
            on_ground = ("tower.takeoff", "tower.luaw", "tower.hold_short", "tower.continue_hold_short", "tower.stop_takeoff",
                         "ground.")
            if st.pending is not None and st.pending.instruction_id.startswith(on_ground):
                st.pending = None
            self._scheduled = [s for s in self._scheduled if not s.instruction_id.startswith(on_ground)]
        elif phase is P.TAKEOFF:
            self._landed = False  # off again (a touch and go, another leg)
            # The taxi-out's runway crossings are behind: the destination's runway of the same name ("13/31") was
            # "crossed" again just off it after landing, with no taxi-in given yet.
            self._crossings, self._crossed = (), set()
            self._crossing_seen.clear()
        if change.previous is not None and P(change.previous) is P.PARKED and st.assignments.departure_gate is None:
            geo = self.geometry(st.flight.origin) if st.flight.origin else None
            gate = stands.parked_at(geo, own.lat, own.lon, self._real_gates(geo.icao)) if geo is not None else None
            self._assign(departure_gate=gate.display if gate is not None else None)
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
        elif phase is P.DEPARTURE and change.previous == P.LANDING and self._vfr:
            self._vfr_touch_and_go(t)  # a touch and go, or a VFR go-around: round the pattern again
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

        if (entered := self._vfr_class_b_watch(own)) is not None:
            out.append(entered)
        if self.cfg.unscripted and not self._transmitting(t) and self._stop_takeoff(own):
            return out  # an aircraft about to be on the runway ahead of the takeoff roll: that can't wait either
        if self.cfg.unscripted and st.phase is not None and P(st.phase) in (P.APPROACH, P.LANDING) \
                and not self._transmitting(t) and (self._runway_conflict(own) or self._not_with_tower(own)):
            # Short final onto an occupied runway: "go around" can't wait for a quiet moment, and the last mile
            # and a half is the landing phase (Seattle's A350 lined up on 34R with this aircraft 1.2 nm out).
            return out
        if self.cfg.unscripted and st.phase is not None and own.on_ground and self._can_call(t, own) \
                and self._ground_conflict(own):
            return out
        if self.cfg.unscripted and st.phase is not None:
            out += self._watch(own)
        if st.phase is None or not self._can_call(t, own):
            return out
        phase = P(st.phase)
        tuned = st.comms.tuned.controller if st.comms.tuned else None
        if tuned == "center" and self._center_works_approach(own):
            tuned = "approach"  # no approach controller at the destination: the centre vectors, clears and hands over
        if self.cfg.unscripted and phase in AIRBORNE_PHASES:
            ifr = not self._vfr  # vectors, and altitude and heading checks, are for IFR flights
            if self._runway_conflict(own) or self._emergency_handling(own) or self._diversion_vectors(own) \
                    or self._sequence_on_final(own) \
                    or (ifr and self._radar_vectors(own)) or self._traffic_advisory(own) \
                    or (ifr and self._altitude_check(own)) or (ifr and self._heading_check(own)):
                return out

        def once(flag: str) -> bool:
            if flag in st.flags:
                return False
            st.flags.add(flag)
            return True

        spoke_here = (st.comms.last_pilot_t or -math.inf) >= self._tuned_since  # news waits for the check-in
        # (A new ATIS letter isn't announced on its own: pilots found "information Juliett is now current" every few
        # minutes nothing like the real thing. The next clearance mentions it, or the pilot's check-in with the old
        # letter gets the new one: ``_atis_note``.)
        if phase in (P.TAXI_OUT, P.RUNWAY_HOLD, P.TAXI_IN) and tuned == "ground" and (crossing := self._crossing_due(own)) is not None:
            self._crossed.add(crossing)
            self._crossing = crossing
            hold = self.tracker.context.hold_short
            side = hold.end.ident if hold is not None and hold.runway.name == crossing else crossing.split("/")[0]
            self._schedule(t, "ground.cross_runway", {"runway": side}, st.comms.tuned, delay=False)
        elif phase is P.TAXI_IN and tuned == "ground" and st.pending is None and (taken := self._gate_now_taken(own)) is not None:
            # The sim puts parked aircraft at the gates only as the flight comes close: one given free can be taken
            # by the time it gets there (a Cessna at gate 76). Ground says so and gives another, before it's reached.
            self._gates_taken.add(taken.index)
            self._taxi_in_route = None
            st.clearances.pop("taxi_in", None)
            self._taxi_in(t, st.comms.tuned, own, busy=taken)
        elif phase in (P.RUNWAY_HOLD, P.TAXI_OUT) and tuned == "tower" and self._takeoff_wait is not None \
                and "takeoff" not in st.clearances and st.pending is None:
            self._release(t, st.comms.tuned, self._takeoff_wait, answering=False)
        elif phase is P.RUNWAY_HOLD and tuned == "ground" and "taxi" in st.clearances \
                and self._at_departure_runway(st.assignments.departure_runway or "") and once("handoff_tower"):
            if (tower := self.facility("tower")) is not None:
                self._handoff(t, "ground.handoff_tower", st.comms.tuned, tower)
        elif self._vfr and self._vfr_monitor(t, own, phase, tuned, once):
            pass  # VFR: frequency change, flight following, pattern (atc_core/vfr.py)
        elif phase is P.DEPARTURE and own.alt_agl_ft > 500 and tuned == "tower" and not self._going_around \
                and st.comms.tuned.airport in (None, st.flight.origin) and once("handoff_departure"):
            if (departure := self.facility("departure") or self._center()) is not None:
                self._handoff(t, "tower.handoff_departure", st.comms.tuned, departure)
        elif tuned == "departure" and self._leaving_departure(own, phase) and once("handoff_center"):
            center = self._center()
            if self.center_area(own) is not None and (here := self._sector_candidate(own)) is not None \
                    and here.station != center.station:
                center = here  # climbed out into the next centre's airspace already: that one takes it
            self._sector, self._sector_since = center, t  # the first sector of the cruise
            self._handoff(t, "departure.handoff_center", st.comms.tuned, center)
        elif phase in (P.DEPARTURE, P.CRUISE, P.ARRIVAL) and tuned == "center" and not self._arriving_in_area(own) \
                and (crossing := self._sector_crossing(own)) is not None:
            # Whatever the phase says: a flight climbing, level or descending across a centre boundary is
            # handed to the next centre (a detector that missed the cruise once kept one on Denver to Seattle).
            if self._sector is not None:
                self._sector_left = (self._sector.station, t)
            self._sector, self._sector_since = crossing, t
            self._handoff(t, "center.handoff_center", st.comms.tuned, crossing)
        elif phase in (P.DEPARTURE, P.CRUISE) and tuned in ("center", "departure") and (step := self._climb_due(own)) is not None:
            self._reaching_t = None
            self._schedule(t, "common.climb", {"altitude": step}, st.comms.tuned, delay=False,
                           on_issue=lambda: self._assign(altitude_ft=step))
        elif phase in (P.DEPARTURE, P.CRUISE) and tuned in ("center", "departure") and "descend" not in st.flags \
                and (phase is P.CRUISE or own.alt_indicated_ft >= 18000) and self._descent_due(own) \
                and (plan := self._arrival_plan(own)) is not None:
            self._clear_descent(t, own, st.comms.tuned, plan)
        elif phase in (P.ARRIVAL, P.APPROACH) and tuned in ("center", "departure"):
            if "descend" not in st.flags and (plan := self._arrival_plan(own)) is not None:
                self._clear_descent(t, own, st.comms.tuned, plan, late=True)  # started down (or arrived) before being cleared
            elif (
                self._arriving_in_area(own)
                and (approach := self._destination_approach()) is not None and once("handoff_approach")
            ):
                self._handoff(t, "center.handoff_approach", st.comms.tuned, approach)
        elif phase in (P.ARRIVAL, P.APPROACH, P.LANDING) and tuned == "approach" and "approach" in st.clearances \
                and st.clearances["approach"].instruction_id in ("approach.intercept_cleared", "approach.intercept_visual",
                                                                 "approach.intercept_course", "approach.cleared_star") \
                and ctx.final is not None and ctx.final.distance_nm <= TOWER_AT_NM + 4 and (tower := self.facility("tower")) is not None \
                and once("approach_tower"):
            self._handoff(t, "approach.handoff_tower", st.comms.tuned, tower)  # established: over to tower
        elif phase in (P.ARRIVAL, P.APPROACH, P.LANDING) and tuned == "approach" and "approach" not in st.clearances:
            # The approach is cleared well before the aircraft is established, not as it crosses the
            # threshold of the approach phase: a pilot flying an ILS wants the clearance before
            # intercepting, with time to brief it and change to tower.
            vectoring = self._vector_leg in ("join", "downwind", "base", "straight_in")  # the intercept will clear it
            # ... unless the pilot flew onto the final by themselves, vectors or not (San Diego's 27, the turns not
            # taken): established, it's cleared for the approach and sent to tower, not left to land unannounced.
            established = ctx.final is not None and ctx.final.distance_nm <= TOWER_AT_NM + 4 \
                and abs(ctx.final.lateral_m) <= ESTABLISHED_M
            if self._joining_final(own) and (not vectoring or established) \
                    and (spoke_here or t - self._tuned_since >= MISSED_CHECKIN_S or established):
                self._vector_leg = self._vector_side = None
                self._clear_approach(t, own, st.comms.tuned, delay=False)
        elif phase in (P.ARRIVAL, P.APPROACH, P.LANDING) and tuned == "tower" and "landing" not in st.clearances \
                and not self._going_around:
            on_final = phase is P.LANDING or (ctx.final is not None and ctx.final.distance_nm <= LANDING_CLEARANCE_NM)
            runway = self._landing_runway(own)
            # Cleared once the runway is empty and nobody's still ahead on the same final: the one in front is
            # cleared first. Cleared behind an A321 on a two mile final, then told "number two", sounded backwards.
            if on_final and (runway is None or (self._traffic_on_runway(own, runway) is None
                                                and self._traffic_ahead(own, runway) is None)):
                self._clear_to_land(t, own, st.comms.tuned, delay=False)
        elif phase is P.TAXI_IN and tuned == "tower" and once("exit_contact_ground"):
            if (ground := self.facility("ground")) is not None:
                geo = self.geometry(st.flight.destination)
                way_off = None
                if geo is not None and own.on_runway:  # still on it: which way off, by which taxiway
                    landed = geo.end(st.assignments.arrival_runway) if st.assignments.arrival_runway else None
                    heading = landed.heading_true if landed is not None else own.hdg_true  # (not mid-turn off it)
                    way_off = self._taxi_graph(geo).runway_exit(own.lat, own.lon, heading, toward=self._where_to_park(geo))
                if way_off is not None:
                    self._schedule(t, "tower.exit_vacate", {"side": way_off[0], "exit": way_off[1],
                                                            "station": ground.station, "frequency": ground.mhz},
                                   st.comms.tuned, handoff_to=ground)
                else:
                    self._handoff(t, "tower.exit_contact_ground", st.comms.tuned, ground)
        else:
            out += self._ambient(own)
        return out

    # --- LocalTC's traffic (localtc.traffic) ----------------------------------------------------------------------

    def assigned_gate_position(self) -> tuple[float, float] | None:
        """Where the gate ground gave this flight is (the traffic keeps it clear), or None."""
        a, dest = self.state.assignments, self.state.flight.destination
        geo = self.geometry(dest)
        if a.gate_index is None or geo is None:
            return None
        gate = next((g for g in stands.gates(geo, self._real_gates(geo.icao)) if g.index == a.gate_index), None)
        return (gate.spot.lat, gate.spot.lon) if gate is not None else None

    def traffic_call(self, ident: str, kind: str, icao: str, runway: str, t: float) -> list[BusEvent]:
        """Tower telling one of the real flights (LocalTC's traffic) what it does because of this one: "go_around",
        "hold_short" (for landing traffic) or "takeoff", and its readback. Heard only on that airport's tower, when
        it's the frequency tuned and nothing else is being said."""
        facility = self.state.comms.tuned
        if facility is None or facility.controller != "tower" or (facility.airport or "").upper() != icao.upper():
            return []
        callsign = Callsign.named(ident)
        if not callsign.ident:
            return []
        geo = self.geometry(icao)
        elev = geo.airport.elev_ft if geo is not None else 0.0
        iid, slots = {
            "go_around": ("tower.go_around_traffic", {"altitude": int(round((elev + 3000) / 100) * 100)}),
            "hold_short": ("tower.hold_short_traffic", {"hold_short": runway,
                                                       "message": Phrase("traffic landing", "traffic landing")}),
            "takeoff": ("tower.takeoff", {"runway": runway}),
        }[kind]
        slots = {**slots, "callsign": callsign}
        shown = callsign.telephony + " " + callsign.flight_number if callsign.is_airline else callsign.ident
        atc = self.library.render(iid, slots, rng=random.Random(zlib.crc32(f"{ident}{kind}".encode())))
        at = max(t, self._radio_busy_until)
        out: list[BusEvent] = [RadioChatter(t=round(at, 1), station=facility.station, frequency_mhz=facility.mhz,
                                            speaker="atc", callsign=shown, text=atc.text, spoken=atc.spoken,
                                            controller=facility.controller, locale=self._locale(facility))]
        at += self._speech_s(atc.spoken) + CHATTER_TURN_S
        if self.library.get(iid).pilot_readback:
            spoken = self.library.pilot_readback(iid, slots)
            out.append(RadioChatter(t=round(at, 1), station=facility.station, frequency_mhz=facility.mhz, speaker="pilot",
                                    callsign=shown, text=spoken[:1].upper() + spoken[1:], spoken=spoken,
                                    controller=facility.controller, locale=self._locale(facility)))
            at += self._speech_s(spoken) + CHATTER_TURN_S
        self._radio_busy_until = max(self._radio_busy_until, at)
        return out

    def _ambient(self, own: OwnshipState) -> list[BusEvent]:
        """The rest of the frequency (atc_core/chatter.py): now and then, when it's quiet, the controller and
        some other flight. Never while the pilot owes a readback, has just spoken or is about to be answered."""
        st, t = self.state, own.t
        facility = st.comms.tuned
        if not self.cfg.chatter or facility is None or facility.controller not in chatter.GAP_S or st.pending is not None:
            return []
        if self._chatter is None:
            self._chatter = chatter.Chatter(self.library, chatter.seed_for(self._callsign().ident, self.cfg.seed))
        if self._chatter_due is None or self._chatter_station != facility.station:
            # Tuned in: a little while before the first, then each controller's own pace.
            self._chatter_station = facility.station
            self._chatter_due = t + self._chatter.gap(facility.controller) * 0.5
            return []
        last_pilot = st.comms.last_pilot_t if st.comms.last_pilot_t is not None else -math.inf
        last_atc = st.comms.last_atc_t if st.comms.last_atc_t is not None else -math.inf
        if t < self._chatter_due or t - last_pilot < CHATTER_QUIET_S or t - last_atc < CHATTER_QUIET_S \
                or self._scheduled or self._transmitting(t) or t < self._radio_busy_until:
            return []
        if (heard := self._reach(facility, own)) is not None and not heard.in_range:
            return []
        self._chatter_due = t + self._chatter.gap(facility.controller)
        lines = self._chatter.exchange(self._scene(facility, own))
        if self._chatter.last is not None:
            self._chatter_used.add((self._chatter.last.callsign.ident, facility.station))
        out: list[BusEvent] = []
        at = t
        for line in lines:
            self._said_on.append((at, facility.station))
            out.append(RadioChatter(t=round(at, 1), station=facility.station, frequency_mhz=facility.mhz, speaker=line.speaker,
                                    callsign=line.callsign, text=line.text, spoken=line.spoken, controller=facility.controller,
                                    locale=self._locale(facility)))
            at += self._speech_s(line.spoken) + CHATTER_TURN_S
        self._radio_busy_until = max(self._radio_busy_until, at)  # ATC doesn't talk over its own other traffic
        return out

    def _chatter_routes(self, geo: AirportGeometry, end) -> list[tuple[str, ...]]:
        """Real taxi routes at this airport, from a few of its gates to the runway in use (the other way round
        for arrivals), worked out once: other flights taxi along the taxiways that are there."""
        key = f"{geo.icao}:{end.ident}"
        if key not in self._routes_cache:
            graph = self._taxi_graph(geo)
            spots = list(geo.airport.parking)
            random.Random(zlib.crc32(key.encode())).shuffle(spots)
            routes = []
            for spot in spots[:12]:
                route = graph.departure_route(spot.lat, spot.lon, end)
                if route is not None and route.taxiways and not route.crossings and len(route.taxiways) <= 4:
                    routes.append(route.taxiways)
                if len(routes) >= 6:
                    break
            self._routes_cache[key] = routes
        return self._routes_cache[key]

    def _scene(self, facility: Facility, own: OwnshipState) -> chatter.Scene:
        """What the controller can tell other flights: this airport's runway in use, wind and taxiways."""
        runway = wind = None
        geo = self.geometry(facility.airport)
        if geo is not None:
            end = self._runway_end(facility.airport, own)
            runway = end.ident if end is not None else None
            wind = self._wind(own)
            routes = self._chatter_routes(geo, end) if end is not None else []
        else:
            routes = []
        nxt = None
        if facility.controller in ("departure", "center"):
            nxt = self._center() if facility.controller == "departure" else None
        elif facility.controller == "approach":
            nxt = self.facility("tower")
        handoff = (nxt.station, nxt.mhz) if nxt is not None and nxt.station != facility.station else None
        others = []
        for oid, target in self._traffic.items():
            if (callsign := self._traffic_callsign(target)) is None or (callsign.ident, facility.station) in self._chatter_used:
                continue
            if (doing := self._doing(target, geo, facility, own)) is not None:
                lined_up = ""
                if doing == "landing" and geo is not None:
                    theirs = geo.final_approach(target.lat, target.lon, target.hdg_true)
                    if theirs is None:
                        continue  # can't tell which runway it's landing on: not cleared for a made-up one
                    lined_up = theirs.end.ident
                others.append(chatter.Other(oid, callsign, doing, int(round(target.alt_ft / 1000) * 1000), lined_up))
        return chatter.Scene(facility.controller, facility.station, runway=runway, wind=wind, routes=routes,
                             handoff=handoff, icao_region=self.region.icao, exclude=self._callsign().ident,
                             level_ft=int(round(own.alt_indicated_ft / 1000) * 1000), others=others)

    def _watch_runway_flow(self, t: float) -> None:
        """Which way the sim's own traffic is taking off and landing at the flight's airports: the ATIS takes its
        runway that way when the wind allows (``AtisBoard.flows``). MSFS picks the AI's runways itself; ATC sending
        this flight the other way ended in head-on finals and go-arounds."""
        st = self.state
        for icao in dict.fromkeys(a for a in (st.flight.origin, st.flight.destination) if a):
            geo = self.geometry(icao)
            if geo is None:
                continue
            for target in self._traffic.values():
                if target.gs_kt < FLOW_KT or target.alt_ft - geo.airport.elev_ft > OVER_RUNWAY_FT \
                        or (icao, target.object_id) in self._flow_seen:
                    continue
                runway = geo.runway_at(target.lat, target.lon, 30.0)
                if runway is None:
                    continue
                end = min(runway.ends, key=lambda e: abs(((e.heading_true - target.hdg_true + 540) % 360) - 180))
                if abs(((end.heading_true - target.hdg_true + 540) % 360) - 180) > 20:
                    continue  # across it, not along it
                self._flow_seen.add((icao, target.object_id))
                self._flow_moves.append((t, icao, end.heading_true))
        self._flow_moves = [m for m in self._flow_moves if t - m[0] <= FLOW_WINDOW_S]
        for icao in dict.fromkeys(a for a in (st.flight.origin, st.flight.destination) if a):
            headings = [h for _, i, h in self._flow_moves if i == icao]
            if len(headings) < FLOW_MOVES:
                continue
            main = max(headings, key=lambda h: sum(1 for o in headings if abs(((o - h + 540) % 360) - 180) <= 45))
            same = sum(1 for o in headings if abs(((o - main + 540) % 360) - 180) <= 45)
            if same >= FLOW_MOVES and same >= 2 * (len(headings) - same):
                self.atis.flows[icao] = main

    def _climbing(self, target: TrafficTarget) -> bool:
        """Climbing since the last traffic picture: a departure, where only the altitude tells it from an arrival."""
        before = self._traffic_before.get(target.object_id) if self._traffic_before else None
        dt = self._traffic_t - self._traffic_before_t if self._traffic_before else 0.0
        return before is not None and dt > 0 and (target.alt_ft - before.alt_ft) / dt * 60.0 > CLIMBING_FPM

    @staticmethod
    def _aircraft(target: TrafficTarget) -> Phrase | None:
        """The other aircraft's type as a controller names it ("Boeing 737", "Embraer 170"), or None if the sim
        doesn't say. Never spelled out: "B737" isn't "bravo seven three seven" on the radio."""
        display, spoken = speech.aircraft_type(clean_sim_name(target.atc_model))
        return Phrase(display, spoken) if display else None

    def _traffic_callsign(self, target: TrafficTarget) -> Callsign | None:
        """The callsign the sim's AI traffic flies under, or None for parked scenery and anything unnamed."""
        ident = target.atc_id.strip().upper()
        if AIRLINE_CALLSIGN.fullmatch(ident) and (named := Callsign.named(ident)).is_airline:
            return named
        if target.flight_number.strip() and target.airline.strip():
            found = Callsign.from_sim(ident, target.airline, target.flight_number)
            return found if found.ident else None
        if REGISTRATION.fullmatch(ident) and target.object_id in self._seen_moving:
            return Callsign.named(ident)  # a light aircraft flying about under its registration
        return None

    def _doing(self, target: TrafficTarget, geo: AirportGeometry | None, facility: Facility,
               own: OwnshipState | None = None) -> str | None:
        """What another aircraft is doing that ``facility``'s controller would be talking to it about, or None.

        With this flight on final, tower clears nobody else onto the runway (Seattle's tower cleared an Alaska 737
        for takeoff with the pilot on a two-mile final), and lands only the ones ahead of it."""
        controller = facility.controller
        mine = self.tracker.context.final if own is not None and not own.on_ground and controller == "tower" else None
        if mine is not None and mine.distance_nm > FINAL_OWNED_NM:
            mine = None
        if target.on_ground:
            if controller not in ("ground", "tower") or geo is None or geo.distance_nm(target.lat, target.lon) > 3.0:
                return None
            if controller == "ground" and target.gs_kt >= GIVE_WAY_MOVING_KT and geo.runway_at(target.lat, target.lon) is None:
                return "taxiing_in" if target.object_id in self._seen_airborne else "taxiing_out"
            if target.gs_kt >= 1.0 or self._parked(target):
                return None
            if controller == "ground" and self._at_parking(geo, target.lat, target.lon):
                return "at_gate"  # an AI flight at its gate, ready to go
            if controller == "tower" and geo.runway_at(target.lat, target.lon, 120.0) is not None \
                    and not self._at_parking(geo, target.lat, target.lon) and mine is None:
                return "holding"
            return None
        before = self._traffic_before.get(target.object_id)
        dt = self._traffic_t - self._traffic_before_t
        fpm = (target.alt_ft - before.alt_ft) / dt * 60 if before is not None and dt > 0 else 0.0
        trend = "climbing" if fpm > 300 else "descending" if fpm < -300 else "level"
        if controller == "tower":
            near = geo is not None and geo.distance_nm(target.lat, target.lon) <= 8.0 \
                and target.alt_ft - geo.airport.elev_ft < 3000
            if near and mine is not None:
                theirs = geo.final_approach(target.lat, target.lon, target.hdg_true)
                near = theirs is not None and theirs.end.ident == mine.end.ident and theirs.distance_nm < mine.distance_nm
            return "landing" if near and trend != "climbing" else None
        if controller in ("departure", "approach"):
            near = geo is None or geo.distance_nm(target.lat, target.lon) <= 40.0
            return trend if near and target.alt_ft < 18000 else None
        if controller == "center":
            return trend if target.alt_ft >= 10000 else None
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
        if pending is not None and last_pilot < pending.issued_t and t - last_atc >= SILENT_PILOT_S \
                and pending.required:
            # (Nothing required, as "readback correct, contact ground when ready": no "did you copy?" for it.)
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
        if st.comms.expected is not None and st.comms.expected != tuned:
            return False  # handed off to the next controller: this one has nothing more to say (Socal after "contact tower")
        best: tuple[float, TrafficTarget] | None = None
        fields = [g for g in (self.tracker.context.airport, self.geometry(st.flight.origin), self.geometry(st.flight.destination))
                  if g is not None]
        for oid, target in self._traffic.items():
            if target.on_ground or target.gs_kt < TRAFFIC_MIN_KT:
                continue  # traffic advisories are for aircraft in the air
            nm = _distance_nm(own.lat, own.lon, target.lat, target.lon)
            closing = nm < self._traffic_nm.get(oid, math.inf) - 0.05
            self._traffic_nm[oid] = nm
            if abs(target.alt_ft - own.alt_msl_ft) > TRAFFIC_ALT_FT or nm > TRAFFIC_NM or (not closing and nm > 2.5):
                continue
            near = [g for g in fields if g.distance_nm(target.lat, target.lon) <= TRAFFIC_FIELD_NM]
            if any(target.alt_ft - g.airport.elev_ft < TRAFFIC_FIELD_AGL_FT for g in near) or \
                    (not fields and target.alt_ft < TRAFFIC_FIELD_AGL_FT):
                # Landing, or just off the runway (the 737 that left Montreal a minute ahead, still at 500 ft):
                # the tower is sequencing it, and to the pilot it's a departure, not traffic.
                continue
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
        kind = self._aircraft(target)
        display = f"{clock} o'clock, {miles} mile{'s' if miles != 1 else ''}, {direction}, {speech.altitude_display(altitude)}"
        spoken = (f"{speech.number_words(clock)} o'clock, {speech.number_words(min(miles, 99))} "
                  f"mile{'s' if miles != 1 else ''}, {direction}, {speech.altitude(altitude)}")
        if kind is not None:
            display, spoken = display + f", {kind.display}", spoken + f", {kind.spoken}"
        self._schedule(t, "common.traffic", {"message": Phrase(display, spoken)}, tuned, delay=False, expects_readback=False)
        self._last_traffic = (t, display)
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
        last, self._last_heading = self._last_heading, (t, own.hdg_true)
        # Turning (Toronto, swinging from 220 to 190 onto the next taxiway): the nose sweeps across everything around,
        # and where it points isn't where the aircraft is going.
        turning = last is not None and 0 < t - last[0] <= 5 and \
            abs(((own.hdg_true - last[1] + 540) % 360) - 180) / (t - last[0]) > GIVE_WAY_TURNING_DEG_S
        if t - self._gave_way_t < GIVE_WAY_GAP_S:
            return False
        geo = self.tracker.context.airport
        # Just off the push (Montreal, gate 78): the nose still points across the apron at the gates opposite,
        # which is where the aircraft is turning away from, not where it's going. Denver's E170, parked with its
        # nose out over the taxilane, was dead ahead once the aircraft was on its way.
        settling = st.phase_since_t is not None and t - st.phase_since_t < TAXI_SETTLE_S and P(st.phase) is P.TAXI_OUT
        at_stand = settling or (geo is not None and self._at_parking(geo, own.lat, own.lon, STAND_CLEAR_M))
        for oid, target in self._traffic.items():
            if target.on_ground and target.gs_kt < GIVE_WAY_MOVING_KT and oid not in self._cautioned:
                if at_stand:
                    continue
                # Stopped right in the way (an AI aircraft waiting on the taxiway): say so before the pilot finds it.
                # A parked one only when the aircraft is really heading into it (Denver's E170, its nose out over the
                # taxilane): the ones at their gates swing past the nose on every turn of the taxi.
                if self._parked_in_the_way(own, target, t) if self._parked(target) else \
                        self._stopped_in_the_way(own, target, geo):
                    self._cautioned.add(oid)
                    self._gave_way_t = t
                    kind = self._aircraft(target) or Phrase("an aircraft", "an aircraft")
                    self._schedule(t, "ground.stopped_ahead", {"message": kind}, tuned, delay=False,
                                   expects_readback=False)
                    return True
                continue
            if not target.on_ground or target.gs_kt < GIVE_WAY_MOVING_KT or oid in self._gave_way_to:
                continue
            nm = _distance_nm(own.lat, own.lon, target.lat, target.lon)
            if nm > GIVE_WAY_NM:
                continue
            ahead = abs(((_bearing(own.lat, own.lon, target.lat, target.lon) - own.hdg_true + 540) % 360) - 180)
            crossing = abs(((target.hdg_true - own.hdg_true + 540) % 360) - 180)
            if turning or ahead > GIVE_WAY_AHEAD_DEG or not GIVE_WAY_CROSSING_DEG <= crossing <= 180 - GIVE_WAY_CROSSING_DEG:
                continue  # not in front, or going the same way as us, or the other way along a taxiway beside ours
            if geo is not None and geo.runway_at(target.lat, target.lon, GIVE_WAY_RUNWAY_M) is not None:
                continue  # lining up or rolling for takeoff (Seattle's 737 on 34R): not taxiing across anybody's way
            meet = self._paths_meet(own, target)
            if meet is None:
                continue  # its way and ours don't cross ahead of both, close and soon: nothing to wait for
            if self._to_our_runway(own, target, geo):
                # Seattle's line of 737s and E170s on B, all for 34R: the pilot joins the line behind the first, once;
                # the rest of the line aren't in anybody's way.
                if "followed" in st.flags:
                    continue
                st.flags.add("followed")
                self._gave_way_t = t
                self._gave_way_to.add(oid)
                kind = self._aircraft(target) or Phrase("traffic", "traffic")
                self._schedule(t, "ground.follow", {"message": kind}, tuned, delay=False, expects_readback=False)
                return True
            self._gave_way_t = t
            self._gave_way_to.add(oid)  # once per aircraft: it doesn't come round again
            kind = self._aircraft(target) or Phrase("traffic", "traffic")
            if meet:
                display, spoken = f"{kind.display} crossing {meet}", f"{kind.spoken} crossing {meet}"
            else:
                display, spoken = kind.display, kind.spoken
            self._schedule(t, "ground.give_way", {"message": Phrase(display, spoken)}, tuned, delay=False,
                           expects_readback=False)
            return True
        return False

    def _to_our_runway(self, own: OwnshipState, target: TrafficTarget, geo: AirportGeometry | None) -> bool:
        """Taxiing out, and the other aircraft is heading for the same runway end, nearer to it than this one: it's
        in the line for departure ahead."""
        if geo is None or P(self.state.phase) is not P.TAXI_OUT:
            return False
        runway = self.state.assignments.departure_runway or self._departure_runway(own)
        end = geo.end(runway) if runway else None
        if end is None:
            return False
        lat, lon = geo.frame.to_latlon(*end.threshold)
        toward = abs(((_bearing(target.lat, target.lon, lat, lon) - target.hdg_true + 540) % 360) - 180)
        return toward <= 60 and _distance_nm(target.lat, target.lon, lat, lon) < _distance_nm(own.lat, own.lon, lat, lon)

    @staticmethod
    def _paths_meet(own: OwnshipState, target: TrafficTarget) -> str | None:
        """Where the other aircraft's way crosses ours: "left to right" or "right to left" when it clearly crosses in
        front from that side, "" when the ways meet ahead but the side isn't clear, None when they don't meet close
        and soon (or it's moving away). Straight lines along both headings, at both speeds."""
        here, there = (0.0, 0.0), _east_north(own.lat, own.lon, target.lat, target.lon)
        mine, theirs = _velocity(own.hdg_true, max(own.gs_kt, GIVE_WAY_MOVING_KT)), _velocity(target.hdg_true, target.gs_kt)
        # here + mine * a == there + theirs * b
        det = mine[0] * -theirs[1] - mine[1] * -theirs[0]
        if abs(det) < 1e-6:
            return None  # parallel
        dx, dy = there[0] - here[0], there[1] - here[1]
        a = (dx * -theirs[1] - dy * -theirs[0]) / det
        b = (mine[0] * dy - mine[1] * dx) / det
        if not (-1.0 <= a <= GIVE_WAY_MEET_S and -1.0 <= b <= GIVE_WAY_MEET_S) or abs(a - b) > GIVE_WAY_MEET_GAP_S:
            # (A second's slack: one already on the crossing point is there, not past it.)
            return None
        # And they'd really come close: two taxiways side by side (Toronto's E195 passing the other way) never do.
        rel = (theirs[0] - mine[0], theirs[1] - mine[1])
        speed2 = rel[0] ** 2 + rel[1] ** 2
        when = 0.0 if speed2 < 1e-9 else max(0.0, -(dx * rel[0] + dy * rel[1]) / speed2)
        if when > GIVE_WAY_MEET_S or math.hypot(dx + rel[0] * when, dy + rel[1] * when) > GIVE_WAY_CLOSEST_M:
            return None
        # From which side: it's on our left heading right, or on our right heading left.
        bearing = (_bearing(own.lat, own.lon, target.lat, target.lon) - own.hdg_true + 540) % 360 - 180
        heading = (target.hdg_true - own.hdg_true + 540) % 360 - 180
        clearly = 60 <= abs(heading) <= 120 and target.gs_kt >= GIVE_WAY_SURE_KT and abs(bearing) >= 10
        if clearly and bearing < 0 < heading:
            return "left to right"
        if clearly and heading < 0 < bearing:
            return "right to left"
        return ""

    def _stopped_in_the_way(self, own: OwnshipState, target: TrafficTarget, geo: AirportGeometry | None) -> bool:
        """A stopped aircraft on the way ahead: on the route ground gave, 30-250 m further along it, or straight
        off the nose, not as far: taxilanes turn, and a gate at the end of a straight bit of apron isn't in
        anybody's way."""
        path = self._taxi_path[1] if self._taxi_path is not None and geo is not None and self._taxi_path[0] == geo.icao else None
        if path is not None and len(path) > 1:
            ahead = _along_path(path, geo.xy(own.lat, own.lon), geo.xy(target.lat, target.lon))
            if ahead is not None and STOPPED_AHEAD_M[0] <= ahead <= STOPPED_AHEAD_M[1]:
                return True
            if ahead is None:
                # Not on the route yet: still crossing the apron to it, turning past the gates, and whatever is off
                # the nose is parked there (Montreal's A320s at gates 83 and W4, the aircraft 80-150 m from its route).
                return False
        # On the route, or with none: close ahead off the nose is where the aircraft is going (Denver's pilot, on the
        # route ground gave, heading for the E170 with its nose out over the taxilane).
        metres = _distance_nm(own.lat, own.lon, target.lat, target.lon) * 1852.0
        off = math.radians(_bearing(own.lat, own.lon, target.lat, target.lon) - own.hdg_true)
        along, across = metres * math.cos(off), abs(metres * math.sin(off))
        return STOPPED_AHEAD_M[0] <= along <= STOPPED_AHEAD_OFF_ROUTE_M and across <= STOPPED_AHEAD_WIDTH_M

    def _parked_in_the_way(self, own: OwnshipState, target: TrafficTarget, t: float) -> bool:
        """A parked aircraft dead ahead, and still dead ahead a few seconds on: the taxi is going into it."""
        metres = _distance_nm(own.lat, own.lon, target.lat, target.lon) * 1852.0
        off = math.radians(_bearing(own.lat, own.lon, target.lat, target.lon) - own.hdg_true)
        along, across = metres * math.cos(off), abs(metres * math.sin(off))
        if not (STOPPED_AHEAD_M[0] <= along <= STOPPED_AHEAD_OFF_ROUTE_M and across <= PARKED_AHEAD_WIDTH_M):
            self._dead_ahead.pop(target.object_id, None)
            return False
        since = self._dead_ahead.setdefault(target.object_id, t)
        return t - since >= PARKED_AHEAD_S

    def _parked(self, target: TrafficTarget) -> bool:
        """Parked, not waiting: an aircraft nobody is flying. The sim's parked aircraft have no flight number and
        no airline callsign (a placeholder like "ASXGSA", a registration, or nothing), and never move; an AI
        flight has its callsign ("SKW5775") before it pushes, and anything seen moving is under way."""
        if not target.on_ground or target.object_id in self._seen_moving or target.flight_number.strip():
            return False
        return not AIRLINE_CALLSIGN.fullmatch(target.atc_id.strip().upper())

    @staticmethod
    def _at_parking(geo: AirportGeometry, lat: float, lon: float, margin_m: float = 15.0) -> bool:
        """At (or backing off) a gate or parking spot."""
        xy = geo.xy(lat, lon)
        return any(math.dist(xy, geo.xy(p.lat, p.lon)) <= max(p.radius_m, 15.0) + margin_m for p in geo.airport.parking)

    def _release(self, t: float, facility: Facility, runway: str, *, answering: bool) -> None:
        """Tower's answer to a departure: cleared for takeoff, line up and wait, or hold short for traffic.

        A departure is only cleared onto an empty runway with nobody close on final. Landing traffic
        near the threshold holds it short; a runway still occupied with nobody arriving lets it line up
        behind. Tower keeps watching and clears it the moment the runway is free (``_takeoff_wait``).
        """
        st = self.state
        if not self._at_departure_runway(runway):
            # Still on the way to it: nobody is lined up or cleared from somewhere else. Asked, "continue taxi, hold
            # short"; tower then clears it once it gets there.
            if answering:
                self._takeoff_wait, self._takeoff_hold = runway, None
                self._schedule(t, "tower.continue_hold_short", {"hold_short": runway}, facility)
            return
        if self._takeoff_hold == "occupied" and not answering and not self._lined_up(runway):
            return  # told to line up: the takeoff clearance waits until it is lined up
        blocked = self._departure_blocked(runway, lined_up=self._takeoff_hold == "occupied")
        if blocked is None:
            self._takeoff_wait = self._takeoff_hold = None
            instruction, slots = self._vfr_takeoff(runway) if self._vfr else self._ifr_takeoff(runway)
            self._schedule(t, instruction, slots, facility, clearance="takeoff", delay=answering,
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
            kind = self._aircraft(target)
            what, said = (f"{kind.display} ", f"{kind.spoken} ") if kind is not None else ("", "")
            n = max(1, round(miles))
            message = Phrase(f"traffic {what}landing", f"traffic {said}landing") if miles < 0.5 else \
                Phrase(f"traffic {what}on {n} mile final", f"traffic {said}on a {speech.number_words(n)} mile final")
            self._schedule(t, "tower.hold_short_traffic", {"hold_short": runway, "message": message}, facility,
                           delay=answering)
        else:
            self._schedule(t, "tower.luaw", {"runway": runway}, facility, clearance="line_up", delay=answering,
                           on_issue=lambda: self._assign(departure_runway=runway))

    def _ifr_takeoff(self, runway: str) -> tuple[str, dict[str, Any]]:
        """The takeoff clearance and what it says to fly (procedures.py): "RNAV to FACTS" on a US RNAV SID; the
        SID alone elsewhere; "fly runway heading" with no SID, or one flown by radar vectors."""
        origin = self.state.flight.origin
        sid = self.route.procedure(self.cfg.sid, "CLB") if self.cfg.sid else ()
        first = procedures.sid_first_fix(self.geometry(origin) if origin else None, sid)
        if self.cfg.sid and (self.region.icao or (origin or "").upper().startswith("C")):
            return "tower.takeoff_sid", {"runway": runway}  # ICAO and Canada: the SID says what to fly
        if first is not None:
            return "tower.takeoff_rnav", {"runway": runway, "fix": first.ident}
        return "tower.takeoff", {"runway": runway}

    def _departure_blocked(self, runway: str, *, lined_up: bool = False) -> tuple[str, TrafficTarget, float] | None:
        """What stops a takeoff on ``runway`` now: ("arrival", aircraft, miles) for landing traffic close in on
        either end's final, ("occupied", aircraft, 0) for anything on the runway itself; None when it's free."""
        geo = self.geometry(self.state.flight.origin)
        end = geo.end(runway) if geo is not None else None
        if geo is None or end is None:
            return None
        occupied: TrafficTarget | None = None
        arriving: list[tuple[float, int, TrafficTarget]] = []
        ours = end.runway
        for target in self._traffic.values():
            if target.on_ground:
                # On it (where two runways cross, on either), or rolling along a runway that crosses it and about to
                # be on it: San Francisco's 737s rolling out on 28R across 01R, one 500 m ahead on the takeoff roll.
                if ours.contains(geo.xy(target.lat, target.lon)) or self._rolling_onto(geo, ours, target) is not None:
                    occupied = target
                continue
            if target.alt_ft - geo.airport.elev_ft < OVER_RUNWAY_FT and (on := geo.runway_at(target.lat, target.lon, 60.0)) \
                    and on.name == end.runway.name:
                if self._climbing(target):
                    # Just lifted off (Seattle's 737 off 34R at 400 ft, climbing): a departure, not landing traffic.
                    # The next one lines up behind it.
                    occupied = occupied or target
                    continue
                # Over the threshold about to touch down (Lufthansa's 777 at 150 ft over 06L at Montreal): past
                # the final and not on the ground yet, but the runway is anything but free.
                arriving.append((0.0, target.object_id, target))
                continue
            final = geo.final_approach(target.lat, target.lon, target.hdg_true, max_distance_nm=OCCUPIED_ARRIVAL_NM)
            if final is not None and final.end.runway.name == end.runway.name and target.alt_ft - geo.airport.elev_ft < 3000:
                arriving.append((final.distance_nm, target.object_id, target))
            elif final is not None and target.alt_ft - geo.airport.elev_ft < 3000 and final.distance_nm <= CROSSING_ARRIVAL_NM \
                    and _runways_cross(final.end.runway, ours):
                arriving.append((final.distance_nm, target.object_id, target))  # landing across ours: it goes first
        closest = min(arriving, default=None)
        margin = LINED_UP_ARRIVAL_NM if lined_up else OCCUPIED_ARRIVAL_NM if occupied is not None else DEPARTURE_ARRIVAL_NM
        if closest is not None and closest[0] <= margin:
            return "arrival", closest[2], closest[0]
        return ("occupied", occupied, 0.0) if occupied is not None else None

    @staticmethod
    def _rolling_onto(geo: AirportGeometry, runway, target: TrafficTarget, horizon_s: float = ROLLING_ONTO_S) -> float | None:
        """Seconds until ``target``, rolling on the ground (a landing rollout, a takeoff) on another runway, is on
        ``runway``; None when it isn't heading onto it that soon."""
        if not target.on_ground or target.gs_kt < ROLLING_KT or runway.contains(geo.xy(target.lat, target.lon)):
            return None
        on = geo.runway_at(target.lat, target.lon)
        if on is None or on.name == runway.name:
            return None
        x, y = geo.xy(target.lat, target.lon)
        speed = target.gs_kt * KT_TO_MS
        ux, uy = math.sin(math.radians(target.hdg_true)), math.cos(math.radians(target.hdg_true))
        for step in range(1, int(horizon_s) + 1):
            if runway.contains((x + ux * speed * step, y + uy * speed * step), 10.0):
                return float(step)
        return None

    def _stop_takeoff(self, own: OwnshipState) -> bool:
        """On the takeoff roll, still slow, with an aircraft about to be on the runway ahead: "stop immediately".
        Above ``STOP_KT`` it's the pilot's to judge: stopping is the greater danger."""
        st = self.state
        tuned = st.comms.tuned
        if st.phase is None or P(st.phase) is not P.TAKEOFF or tuned is None or tuned.controller != "tower" \
                or not own.on_ground or own.gs_kt >= STOP_KT or "stop_takeoff" in st.flags:
            return False
        geo = self.geometry(st.flight.origin)
        runway = geo.runway_at(own.lat, own.lon) if geo is not None else None
        if geo is None or runway is None:
            return False
        here = runway.along_across(geo.xy(own.lat, own.lon))[0]
        ahead_sign = 1 if abs(((own.hdg_true - runway.ends[0].heading_true + 540) % 360) - 180) < 90 else -1
        for target in self._traffic.values():
            if not target.on_ground:
                continue
            xy = geo.xy(target.lat, target.lon)
            soon = 0.0 if runway.contains(xy) and target.gs_kt < RUNWAY_CLEAR_KT else self._rolling_onto(geo, runway, target)
            if soon is None:
                continue
            along = runway.along_across(xy)[0]
            if (along - here) * ahead_sign > 50.0:  # ahead of it, not behind
                st.flags.add("stop_takeoff")
                self._schedule(own.t, "tower.stop_takeoff", {}, tuned, delay=False, expects_readback=False)
                return True
        return False

    def landing_runway(self, own: OwnshipState) -> str | None:
        """The runway the aircraft is landing on: the one it's lined up with, or was cleared for."""
        return self._landing_runway(own)

    def _landing_runway(self, own: OwnshipState) -> str | None:
        ctx = self.tracker.context
        assigned = self.state.assignments.arrival_runway
        if ctx.final is not None and ctx.final.distance_nm <= LINED_UP_NM:
            ident = ctx.final.end.ident
            # Parallel runways 800 ft apart: out on the approach, still "the one it was cleared for". Only
            # short final lined up on the other one is a real sidestep.
            parallel = assigned is not None and ident != assigned and ident.rstrip("LRC") == assigned.rstrip("LRC")
            if parallel and ctx.final.distance_nm > SIDESTEP_NM:
                return assigned
            return ident
        return assigned or (ctx.final.end.ident if ctx.final else None)

    def _traffic_on_runway(self, own: OwnshipState, runway: str) -> TrafficTarget | None:
        """An aircraft sitting on, or rolling down, the runway this one is about to land on."""
        geo = self.geometry(self.state.flight.destination)
        end = geo.end(runway) if geo is not None else None
        if geo is None or end is None:
            return None
        ux, uy = math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true))
        own_xy = geo.xy(own.lat, own.lon)
        for target in self._traffic.values():
            if not target.on_ground or target.gs_kt > RUNWAY_CLEAR_KT:
                continue
            on = geo.runway_at(target.lat, target.lon)
            if on is None or on.name != end.runway.name:
                continue
            x, y = geo.xy(target.lat, target.lon)
            dx, dy = x - end.threshold[0], y - end.threshold[1]
            along, lateral = dx * ux + dy * uy, abs(dx * uy - dy * ux)
            if lateral > end.runway.half_width:
                continue  # beside the pavement (LAX's 737 at a holding point by the 25R threshold): not on it
            if along > LANDING_ZONE_M and target.gs_kt >= RUNWAY_VACATING_KT:
                continue  # rolling out well down the runway (Las Vegas's A321, 2 km on at 40 kt): off it in time
            away = abs(((target.hdg_true - end.heading_true + 540) % 360) - 180) < 45
            if away and target.gs_kt >= RUNWAY_VACATING_KT and own.gs_kt > 30:
                # Where it will be as this one crosses the threshold: landed traffic ahead rolling on, that far down
                # the runway by then, is the spacing a controller works to (FAA 3-10-3). A 737
                # 1,500 m down 26L at 40 kt, this flight 0.86 nm out, was sent around for it.
                to_threshold_s = math.hypot(own_xy[0] - end.threshold[0], own_xy[1] - end.threshold[1]) / (own.gs_kt * KT_TO_MS)
                if along + target.gs_kt * KT_TO_MS * to_threshold_s >= LANDING_BEHIND_M:
                    continue
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
        self._scheduled = [s for s in self._scheduled if s.instruction_id != "tower.land"]
        geo = self.geometry(st.flight.destination)
        altitude = int(math.ceil((((geo.airport.elev_ft if geo else 0) + 2000) / 100)) * 100)
        self._going_around = True
        self._assign(heading=None)  # runway heading now, not the intercept heading from before
        self._heading_given = None
        self._vector_leg = self._vector_side = None
        self._vectors_named = False
        st.flags.discard("approach_tower")
        for kind in ("approach", "landing"):
            st.clearances.pop(kind, None)
        st.pending = None
        radar = self.facility("approach") or self._center()
        if radar is not None:  # and back to approach for another go, as after any go-around
            self._schedule(t, "tower.go_around_traffic_contact", {"altitude": altitude, "station": radar.station,
                                                                  "frequency": radar.mhz}, tuned, delay=False,
                           handoff_to=radar, on_issue=lambda: self._assign(altitude_ft=altitude))
        else:
            self._schedule(t, "tower.go_around_traffic", {"altitude": altitude}, tuned, delay=False,
                           on_issue=lambda: self._assign(altitude_ft=altitude))
        return True

    def _not_with_tower(self, own: OwnshipState) -> bool:
        """Short final, still on approach's frequency, no landing clearance: told to contact tower and didn't, or
        never told. Approach sends the flight around rather than let it land unannounced (a flight that flew its own
        way in landed without a word from anybody), once."""
        st, ctx, t = self.state, self.tracker.context, own.t
        tuned = st.comms.tuned
        if tuned is None or not self._works_approach(own) or own.on_ground or self._going_around or "emergency" in st.flags:
            return False
        if P(st.phase) is not P.LANDING or "landing" in st.clearances or ctx.final is None \
                or ctx.final.distance_nm > GO_AROUND_NM or "not_with_tower" in st.flags:
            return False
        handoff = self._last_handoff
        done = st.read_back
        if handoff is not None and handoff[2].controller == "tower" and done is not None \
                and done.controller == tuned.controller and t - handoff[0] <= TOWER_SWITCH_S:
            return False  # sent to tower a moment ago and read it back: switching now
        st.flags.add("not_with_tower")
        geo = self.geometry(st.flight.destination)
        altitude = int(math.ceil((((geo.airport.elev_ft if geo else 0) + 2000) / 100)) * 100)
        self._going_around = True
        self._assign(heading=None)
        self._heading_given = None
        self._vector_leg = self._vector_side = None
        self._vectors_named = False
        st.flags.discard("approach_tower")
        st.clearances.pop("approach", None)
        st.pending = None
        self._schedule(t, "approach.go_around_no_clearance", {"altitude": altitude}, tuned, delay=False,
                       on_issue=lambda: self._assign(altitude_ft=altitude))
        return True

    def _traffic_ahead(self, own: OwnshipState, runway: str | None) -> tuple[float, TrafficTarget] | None:
        """The nearest aircraft still in the air on the same final, closer in than this one: (its miles out, it)."""
        ctx = self.tracker.context
        geo = self.geometry(self.state.flight.destination)
        end = geo.end(runway) if geo is not None and runway else None
        if end is None or ctx.final is None:
            return None
        ahead = []
        for target in self._traffic.values():
            if target.on_ground or target.alt_ft - geo.airport.elev_ft > own.alt_msl_ft - geo.airport.elev_ft + SEQUENCE_ALT_FT:
                continue
            their_final = geo.final_approach(target.lat, target.lon, target.hdg_true)
            if their_final is not None and their_final.end.ident == end.ident and their_final.distance_nm < ctx.final.distance_nm:
                ahead.append((their_final.distance_nm, target.object_id, target))
        if not ahead:
            return None
        distance, _, target = min(ahead)
        return distance, target

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
        kind = self._aircraft(target) or Phrase("traffic", "traffic")
        miles = max(1, round(distance))
        display = f"number two, follow the {kind.display} on a {miles} mile final"
        spoken = f"number two, follow the {kind.spoken} on a {speech.number_words(min(miles, 99))} mile final"
        if "landing" in st.clearances:
            # Cleared already, and then somebody's ahead on the same final (the sim put an A321 on a two mile final
            # after Heathrow had cleared this flight): the clearance is taken back, given again once they're off.
            st.clearances.pop("landing", None)
            display, spoken = display + ", continue", spoken + ", continue"
        self._schedule(t, "tower.sequence", {"message": Phrase(display, spoken)}, tuned, delay=False,
                       expects_readback=False)
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

    def _heading_check(self, own: OwnshipState) -> bool:
        """A heading given and not flown (San Diego: "turn left heading 100", and the aircraft kept going its own way):
        "check heading", twice at most for one heading. A pilot turning onto it, or a heading that's been replaced,
        is left alone."""
        st, t = self.state, own.t
        given = self._heading_given
        if given is None or st.assignments.heading != given[1] or "approach" in st.clearances or own.on_ground \
                or st.pending is not None or st.comms.tuned is None or st.comms.tuned.controller not in ("approach", "departure", "center"):
            self._heading_off_since = None
            return False
        since, heading, checks = given
        off = abs(((own.hdg_mag - heading + 540) % 360) - 180)
        if t - since < HEADING_TURN_S or off <= HEADING_OFF_DEG:
            self._heading_off_since = None
            return False
        if self._heading_off_since is None:
            self._heading_off_since = t
        if t - self._heading_off_since < HEADING_OFF_S or checks >= HEADING_CHECKS:
            return False
        self._heading_given = (t, heading, checks + 1)  # and the pilot gets time to turn again
        self._heading_off_since = None
        self._schedule(t, "common.check_heading", {"heading": heading}, st.comms.tuned, delay=False)
        return True

    def _can_call(self, t: float, own: OwnshipState) -> bool:
        st = self.state
        if st.pending is not None or self._scheduled or self._transmitting(t):
            return False
        last = max(x for x in (st.comms.last_atc_t, st.comms.last_pilot_t, -math.inf) if x is not None)
        return t - last >= self.cfg.min_gap_s

    # --- pilot transmissions -----------------------------------------------------------------------

    def _on_pilot(self, ev: Transcript) -> list[BusEvent]:
        self._answering = True  # whatever is scheduled now answers the pilot, and goes out even with the sim paused
        self._interp, self._not_used = None, []
        before = len(self._scheduled)
        try:
            out = self._answer_pilot(ev)
            replies = [item for item in self._scheduled[before:] if item.reply]
            out += self._word_replies(replies, ev)
        finally:
            self._answering = False
        decision = self._decision(ev, replies)
        return out + [decision] + self._model_timeouts(out, ev.t) + self._model_rejections(out, decision)

    def _model_rejections(self, out: list[BusEvent], decision: AtcDecision) -> list[BusEvent]:
        """An ``llm_rejected`` alert whenever the model's words for a reply were turned away and ATC said something
        else: never silently. A reading of the call the checks turned away, where the grammar read it instead and
        the reply was still the model's to word, is in the decision's record (``AtcDecision.fallback``) and the
        radio log, not an alert: the pilot heard the model either way. (Timeouts have their own alert.)"""
        turned_away = any(isinstance(o, LlmExchange) and o.outcome == "invalid" for o in out)
        if not turned_away or not self._not_used:
            return []
        return [self._alert(decision.t, "llm_rejected", "; ".join(self._not_used))]

    @property
    def llm_mode(self) -> str:
        """How much ATC leans on the language model (``[llm] mode``): scripted, semi, mostly_llm, llm, or off."""
        return getattr(self.interpreter, "mode", "off")

    def _word_replies(self, replies: list[_Scheduled], ev: Transcript) -> list[BusEvent]:
        """The model's words for the script's replies, where the mode has the model word them: every reply (llm), or
        the replies to a call the model read (mostly_llm). Each is checked to say what the script decided, all of it
        and nothing more (``phrase.check_reworded``); what fails is said in the template's words, and why is kept."""
        mode, interp = self.llm_mode, self._interp
        if self.phraser is None or not replies or interp is None or mode not in ("mostly_llm", "llm"):
            return []
        understood = [x for x in interp.exchanges if getattr(x, "purpose", "") == "understand"]
        if mode == "mostly_llm" and not understood:
            return []  # the script was sure it's routine, and answered it
        if understood and understood[-1].outcome in ("timeout", "error"):
            if any(item.worded_by == "template" and item.instruction_id not in NO_REWORD for item in replies):
                self._not_used.append("the script's words, not the model's: it had just failed to answer in time")
            return []  # it just ran out of time (or isn't there): another call would too, and the pilot is waiting
        callsign = self._callsign()
        callsigns = tuple({speech.callsign_display(callsign), speech.callsign_display(callsign.short), callsign.ident})
        out: list[BusEvent] = []
        for item in replies:
            if item.worded_by != "template" or item.instruction_id in NO_REWORD:
                continue
            body = self._reply_body(item)
            if body is None:
                continue
            text, exchanges = self.phraser.reword(pilot=ev.text, scripted=body, callsigns=callsigns, t=ev.t,
                                                  trigger=interp.trigger or "", persona=self._persona(item.facility))
            out += exchanges
            if text is None:
                last = exchanges[-1] if exchanges else None
                why = (f"{last.outcome}: {last.detail}" if last.detail else last.outcome) if last is not None else "no answer"
                self._not_used.append(f"the model's words for {item.instruction_id} were turned away ({why}); ATC said "
                                      "the same in the script's words")
                continue
            item.worded = Phrase(text, spoken_as(text, self._slot_forms(item.slots)))
            item.worded_by = "model"
        return out

    def _reply_body(self, item: _Scheduled) -> str | None:
        """What the template says for ``item`` after the callsign (the first of its wordings), or None when it
        doesn't start with the callsign (then it's left as it is)."""
        callsign = self._callsign()
        if item.facility.controller in self.state.comms.contacted and not callsign.is_airline:
            callsign = callsign.short
        try:
            rendered = self.library.render(item.instruction_id, {**item.slots, "callsign": callsign},
                                           controller=item.facility.controller, choose=lambda n: 0)
        except Exception:
            return None
        head = speech.callsign_display(callsign) + ", "
        if not rendered.text.startswith(head):
            return None
        return rendered.text[len(head):].rstrip(" .") or None

    @staticmethod
    def _slot_forms(slots: dict[str, Any]) -> list[tuple[str, str]]:
        """(display, spoken) for each value a reply carries: how the voice says the model's words for it."""
        from localtc.atc_core.phraseology.slots import SLOTS

        forms: list[tuple[str, str]] = []
        for name, value in slots.items():
            slot = SLOTS.get(name)
            if slot is None or name == "callsign" or not isinstance(value, slot.value_type):
                continue
            try:
                forms.append((slot.display(value), slot.spoken(value)))
                if name == "taxi_route":
                    forms += [(str(part), speech.taxi_route((part,))) for part in value]
            except Exception:
                continue
        return forms

    def _decision(self, ev: Transcript, replies: list[_Scheduled]) -> AtcDecision:
        """The record of how this call was answered (``AtcDecision``)."""
        interp = self._interp
        not_used = ([interp.fallback] if interp is not None and interp.fallback else []) + self._not_used
        model = interp.model_read if interp is not None else ""
        return AtcDecision(
            t=ev.t, pilot=ev.text, mode=self.llm_mode, trigger=(interp.trigger or "") if interp is not None else "",
            grammar=(interp.grammar_read or reading(interp)) if interp is not None else "",
            model=model, used="model" if interp is not None and interp.source == "llm" else "grammar",
            decision=", ".join(item.instruction_id for item in replies),
            wording=", ".join(item.worded_by for item in replies), fallback="; ".join(not_used),
        )

    def _model_timeouts(self, out: list[BusEvent], t: float) -> list[BusEvent]:
        """An ``llm_timeout`` alert whenever the language model ran out of time on the pilot's call: the app says so
        (the pilot should know why ATC answered the way it did, and that the timeouts can be raised)."""
        missed = [o for o in out if isinstance(o, LlmExchange) and o.outcome == "timeout"]
        if not missed:
            return []
        waited = max(o.latency_ms for o in missed) / 1000
        what = "wording a reply" if all(o.purpose in ("phrase", "reword") for o in missed) else "reading your call"
        if any(isinstance(o, AtcThinking) and o.busy for o in out):
            patience = getattr(self.interpreter, "patience_s", None)
            more = f" (up to {patience:.0f} s)" if patience else ""
            detail = f"no answer {what} in {waited:.0f} s; asking it once more{more}"
        else:
            detail = f"no answer {what} in {waited:.0f} s; ATC answered without it"
        return [self._alert(t, "llm_timeout", detail)]

    def _answer_pilot(self, ev: Transcript) -> list[BusEvent]:
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
        if pending is None and self._handed_off_from(facility) is not None \
                and not self._repeats_readback(ev.text, facility, mhz, t):  # ("Tower 119.9" once more: no reminder)
            # The pilot is still on the old frequency after reading back the handoff: send them again,
            # whatever they said. Nothing else on this frequency is theirs to ask for any more.
            new = self._handed_off_from(facility)
            st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
            self._schedule(t, "common.contact", {"station": new.station, "frequency": new.mhz}, facility,
                           handoff_to=new, expects_readback=False)  # already read back once: just the reminder
            return []
        if (heard := self._reach(facility, own)) is not None and not heard.in_range:
            # Out of radio range: nobody hears it. Said once (a minute apart) so the pilot knows why it's quiet.
            st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
            if t - self._range_told.get(facility.station, -math.inf) >= RANGE_TOLD_S:
                self._range_told[facility.station] = t
                return [self._alert(t, "out_of_range", f"{facility.station} is {heard.distance_nm:.0f} nm away; "
                                                       f"its radio reaches about {heard.range_nm:.0f} nm at this altitude")]
            return []
        if (asked := self._callsign_asked) is not None and asked[1] == facility and t - asked[0].t <= CALLSIGN_ASKED_S \
                and len(rest := without_callsign(said := normalize(ev.text), self._callsign())) < len(said) \
                and len(rest) <= 2:  # the callsign, and little or nothing else
            # "Station calling, say again your callsign" -- "United 1596": it was this flight. The call it made is
            # answered now, not the callsign on its own.
            self._callsign_asked = None
            ev = msgspec.structs.replace(ev, text=f"{ev.text.rstrip(' .')}, {asked[0].text}",
                                         confidence=asked[0].confidence)
            return self._answer_pilot(ev)
        if self.cfg.callsign_check and ev.source != "copilot":
            verdict = judge_callsign(normalize(ev.text), self._callsign())
            if verdict == "other" and self._talking_about_another_flight(ev.text, facility, t):
                verdict = "ours"
            if verdict == "close" and pending is None and not self._close_callsign_taken(ev.text):
                # One digit off ("Frontier 1649" for 1629) and nobody around flies that number: speech-to-text, not
                # another flight. Answered as this flight's, with its callsign said right.
                verdict = "ours"
            if verdict == "other" or (verdict == "close" and pending is None):
                # Somebody else's callsign ("Westjet 452", "Air Canada 452"), or one slip away from ours on a call
                # that would start something: not answered as this flight's. A readback, which the controller is
                # waiting for from this flight, is taken with a near-miss callsign.
                st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
                self._callsign_asked = (ev, facility)  # if it was this flight after all, its call is answered then
                self._schedule(t, "common.station_say_again", {"station": facility.station}, facility, expects_readback=False)
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
        if (words := set(re.findall(r"[a-z']+", ev.text.lower()))) & CANCEL_WORDS:
            if (done := self._cancel_request(ev.text, words, facility, t)) is not None:
                st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
                return done
        if CUT_OFF.search(ev.text) and pending is None:
            # "That's all we're asking but sh-": the transmission was cut off. Whatever it was going to say, ATC
            # didn't get it (the model answered the half with the runway in use and "parallel landings").
            st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
            self._schedule(t, "common.say_again", {}, facility, expects_readback=False)
            return []
        if pending is not None and pending.nudged and READ_YOU.search(ev.text):
            # "Loud and clear" to ATC's own "how do you read?": the instruction follows (``NUDGE_REPLY_S``). It got
            # "say again", three times, and the copilot answered "loud and clear" each time.
            st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, None))
            return []
        if pending is not None and (acked := self._handoff_acknowledged(ev.text, pending, t)) is not None:
            st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, acked))
            return self._on_readback(acked, facility, t)
        interp = self.interpreter.interpret(ev.text, pending, self._interpret_context(facility, t, ev.confidence))
        if interp.kind == "unknown" and (topic := question_topic(ev.text)) is not None:
            # Without the language model: "request frequency for tower" is still clearly a question.
            interp = replace(interp, kind="request", intent="question", values={"topic": topic}, needs_fallback=False)
        if interp.kind != "readback" and (asked := FREQUENCY_CHANGE.search(ev.text)) is not None \
                and CONTROLLER_WORDS.get(asked.group(1).lower()) not in (None, facility.controller):
            # "NBV on the ILS, requesting radio to the tower": a frequency change, not a request for the ILS ("roger,
            # expect ILS runway 13", and the pilot switched to tower unasked).
            interp = replace(interp, kind="request", intent="question", values={"topic": "frequency"}, needs_fallback=False)
        if self._first_call_after_landing(interp, facility, pending, ev.text):
            # "Ground, good evening, NBV on Charlie": landed, off the runway, calling ground for the first time. That
            # call is for the taxi in; it got "copy that", and the pilot had to ask for the taxi again.
            interp = replace(interp, kind="request", intent="request_taxi_parking", needs_fallback=False)
        st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, interp))
        self._interp = interp
        out: list[BusEvent] = list(interp.exchanges)
        if not self._patient and self._model_needs_time(interp, pending):
            # A question, or something off the script, and the model didn't answer in time (the sim shares the
            # machine): the app shows the controller thinking (nothing said: a "stand by" before every answer
            # was one call too many), and the service asks again with time to spare (``resolve_deferred``).
            self._deferred, self._deferred_facility = ev, facility
            return out + [AtcThinking(t=t, station=facility.station, frequency_mhz=facility.mhz)]
        if interp.kind == "request" and interp.intent == "acknowledge" and asks(ev.text):
            # A question nobody could make out ("... any idea? Thanks"): not a thank-you to leave unanswered.
            if self._model_replies():
                return out + self._phrase(interp, facility, t, "reply", otherwise=self._unplaced_fallback(ev, pending))
            self._schedule(t, "common.say_again", {}, facility)
            return out
        if interp.intent != "emergency" and (problem := self._problem(ev.text, facility, t)) is not None:
            out.append(problem)
            if interp.kind == "unknown" or (interp.kind != "readback" and interp.intent in (
                    None, "other", "question", "report_problem", "acknowledge")):
                return out  # the problem was the message: acknowledged, nothing to decline or ask again
        if pending is not None and pending.instruction_id.startswith(TAXI_IN_IDS) and REFUSED.match(ev.text) \
                and (gate := stands.requested(normalize(ev.text))) is not None and st.pending is pending:
            # "Negative, we'd like to taxi to Echo 9": the gate the pilot wants, not a wrong readback of the one given.
            st.pending = None
            st.clearances.pop("taxi_in", None)
            self._taxi_in(t, facility, own, requested=gate)
            return out
        if pending is not None and interp.kind == "request" and interp.intent not in (None, "acknowledge", "say_again",
                                                                                   "question", "emergency") \
                and REFUSED.match(ev.text) and st.pending is pending:
            # "No, no, no. Negative, we'd like to push back": not the taxi ATC gave on a misheard call. That
            # instruction is withdrawn, not asked for again a minute later ("did you copy?" at Phoenix's F4).
            st.pending = None
            self._scheduled = [s for s in self._scheduled if s.instruction_id != pending.instruction_id]
            for kind, clearance in list(st.clearances.items()):
                if clearance.instruction_id == pending.instruction_id:
                    st.clearances.pop(kind)
            pending = None
        if interp.kind == "readback":
            out += self._on_readback(interp, facility, t)
            if interp.then is not None and interp.then.intent not in ("emergency", "acknowledge", "pleasantry", "other", None):
                # (An acknowledgement riding along, "heading 230 as well as the other stuff", isn't a request: it got
                # "unable".)
                # "Cleared to land 14R, can we make it a low approach?": the readback taken, then the request.
                out += self._on_request(interp.then, facility, t)
            return out
        if interp.intent == "emergency":
            return out + self._emergency(interp, facility, t)
        if interp.kind == "request":
            return out + self._on_request(interp, facility, t)
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
        garbled = interp.kind == "unknown" and interp.source == "llm" and ev.confidence is not None             and ev.confidence < CONVERSE_CONFIDENCE
        if self._model_replies() and not garbled:
            # The model's modes: a call nothing could classify ("would you like a coffee after your shift?", "that's not
            # parallel, you'd need both 28s", a garbled readback) still gets the model's reply, not the script's. One the
            # model couldn't make out either, from words speech-to-text wasn't sure of: "say again" (it got "copy").
            return out + self._phrase(interp, facility, t, "reply", otherwise=self._unplaced_fallback(ev, pending))
        if self._patient and pending is None and len(ev.text.split()) >= LONG_STATEMENT_WORDS:
            # Gave the model its time, and still nothing to act on: a long call in the pilot's
            # own words is a statement, not a garbled instruction. "Roger", not "say again" to a paragraph.
            self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return out
        self._schedule(t, "common.say_again", {}, facility)
        return out

    def _cancel_request(self, text: str, words: set[str], facility: Facility, t: float) -> list[BusEvent] | None:
        """ "Disregard the request for the descent, we'd like to maintain FL360": the altitude change just given for
        the pilot's request is taken back ("roger, maintain FL360"), and what else they asked is answered. "Disregard
        this transmission" on its own: "roger". None when there's nothing to take back and more was said."""
        st = self.state
        granted, pending = self._granted, st.pending
        recent = granted is not None and t - granted[0] <= CANCEL_WINDOW_S
        if recent and pending is not None and pending.instruction_id in ("common.descend", "common.climb") \
                and pending.controller == facility.controller:
            _, before, cruise = granted
            st.pending, self._granted = None, None
            self._assign(altitude_ft=before)
            if cruise:
                st.flight.cruise_ft = cruise
                self._assign(cruise_ft=cruise)
            topic = question_topic(text)
            note = self._answer_message(topic) if topic is not None else None
            self._schedule(t, "common.maintain", {"altitude": before}, facility, note=note,
                           on_issue=lambda: self._assign(altitude_ft=before))
            return []
        if "taxi" in words and "taxi" in st.clearances and facility.controller == "ground":
            # "Can we disregard our taxi clearance? We were just asking for the departure runway": cancelled, and
            # what they asked answered (it was "contact Montreal Clearance").
            if pending is not None and pending.instruction_id.startswith("ground.taxi"):
                st.pending = None
            st.clearances.pop("taxi", None)
            self._assign(taxi_route=())
            topic = question_topic(text)
            note = self._answer_message(topic) if topic is not None else None
            parked = st.phase is not None and P(st.phase) in (P.PARKED, P.PUSHBACK)
            self._schedule(t, "ground.taxi_cancelled_ready" if parked else "ground.taxi_cancelled", {}, facility,
                           note=note, expects_readback=False)
            return []
        if len(words - CANCEL_WORDS - COURTESY - CANCEL_FILLER) <= 2:
            self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return []
        return None

    @property
    def deferred(self) -> Transcript | None:
        """A pilot transmission waiting on the language model (the app shows the controller thinking)."""
        return self._deferred

    def resolve_deferred(self) -> list[BusEvent]:
        """Answer the transmission the model missed, giving it its patience this time. Blocks while
        the model thinks (the service runs it on a thread)."""
        ev, self._deferred = self._deferred, None
        facility, self._deferred_facility = self._deferred_facility, None
        if ev is None:
            return []
        self._patient = True
        if self.phraser is not None:
            self.phraser.patient = True
        before = len(self._scheduled)
        try:
            out = self._on_pilot(ev)
        finally:
            self._patient = False
            if self.phraser is not None:
                self.phraser.patient = False
        answered = len(self._scheduled) > before or any(isinstance(o, AtcTransmission) for o in out)
        if not answered and facility is not None and self.state.pending is None:
            # The pilot asked something and waited: never nothing at all. (Asked about the departure runway
            # at Orlando, the model gave up and the grammar heard only "thank you": no answer, three times.)
            self._schedule(ev.t, "common.say_again", {}, facility)
        done = [AtcThinking(t=ev.t, station=facility.station, frequency_mhz=facility.mhz, busy=False)] if facility else []
        return out + done + self._flush(self._t)

    def _talking_about_another_flight(self, text: str, facility: Facility, t: float) -> bool:
        """A question about another flight in the middle of this one's conversation ("what aircraft is United 2117
        in?"): the callsign is what it's asking about, not who's calling. The same voice, still talking to the same
        controller a moment later: a controller knows who that is."""
        recent = any(e.controller == facility.controller and t - e.t <= CONVERSATION_S for e in self.state.exchanges)
        return recent and (is_question(text) or asks(text))

    def _model_replies(self) -> bool:
        """Whether a call nothing could classify still goes to the model for its reply: always, in the modes where
        the model words ATC's replies (Mostly and Fully LLM)."""
        return self.phraser is not None and self.llm_mode in ("mostly_llm", "llm")

    def _unplaced_fallback(self, ev: Transcript, pending: PendingReadback | None) -> str | Phrase:
        """What ATC says when the model's reply to a call nothing could classify fails its checks: what fits the call,
        never something unrelated. A readback still owed, or words speech-to-text wasn't sure of: "say again" (that's
        the problem). A question: that ATC hasn't the answer. Anything else, a remark: "roger"."""
        unsure = ev.confidence is not None and ev.confidence < CONVERSE_CONFIDENCE
        # Words that say something: not "uh", "the", or the callsign ("DP69 the uh thing" is one word).
        words = [t for t in without_callsign(normalize(ev.text), self._callsign()) if t.kind == "word"]
        if pending is not None or unsure or len(words) < CONVERSE_WORDS:
            return "common.say_again"
        if is_question(ev.text) or asks(ev.text):
            return UNAVAILABLE
        return "common.roger"

    def _taxi_in_given(self) -> IssuedInstruction | None:
        """The taxi in, given and read back right."""
        given = self.state.clearances.get("taxi_in")
        if given is None or given.readback != "correct":
            return None
        return self.state.issued.get(given.instruction_id)

    def _first_call_after_landing(self, interp: Interpretation, facility: Facility, pending: PendingReadback | None,
                                  text: str) -> bool:
        st, own = self.state, self.state.aircraft
        if facility.controller != "ground" or st.phase is None or P(st.phase) is not P.TAXI_IN or pending is not None \
                or own is None or not own.on_ground or "taxi_in" in st.clearances or len(text.split()) < 2:
            return False
        return interp.kind == "unknown" or (interp.kind == "request" and interp.intent in (
            None, "other", "pleasantry", "checkin", "acknowledge", "position_report"))

    @staticmethod
    def _model_needs_time(interp: Interpretation, pending: PendingReadback | None) -> bool:
        """The model was asked and ran out of time, and what's left is not enough to answer: a conversational
        call, a request riding along with a readback, or anything that would otherwise get "say again". A
        readback the grammar could read (right, wrong or partly) is answered from that: it's what ATC waits on."""
        timed_out = bool(interp.exchanges) and all(getattr(x, "outcome", "") == "timeout" for x in interp.exchanges)
        if not timed_out or interp.intent == "emergency":
            return False
        say_again = interp.kind == "unknown" or (interp.intent == "say_again" and interp.source == "fallback")
        conversational = interp.trigger in CONVERSATIONAL_TRIGGERS and (
            pending is None or interp.trigger in ("question", "compound"))
        return say_again or conversational

    def _reach(self, facility: Facility, own: OwnshipState | None) -> radio_range.Reach | None:
        """How the facility's radio reaches the aircraft (None: not limited, or nothing to measure from)."""
        if not self.cfg.radio_range or own is None or facility.airport is None:
            return None
        geo = self.geometry(facility.airport)
        if geo is None:
            return None
        runways = geo.airport.runways
        size = radio_range.airport_size(len(runways), max((r.length_m for r in runways), default=0.0))
        return radio_range.reach(facility.controller, geo.distance_nm(own.lat, own.lon),
                                 own.alt_msl_ft - geo.airport.elev_ft, size)

    def _handoff_acknowledged(self, text: str, pending: PendingReadback, t: float) -> Interpretation | None:
        """A handoff taken, in whatever words: "Montreal Centre, have a good night", "switching, Air Canada 779".

        Pilots should read the frequency back, but a controller doesn't hold a flight on frequency for
        "Toronto Centre on 135 decimal ... 55" gone wrong in speech-to-text, or for a "good day" and the
        new station's name. Only a wrong frequency is something to correct.
        """
        new = self.state.comms.expected
        if new is None or "frequency" not in pending.expected:
            return None
        if frequencies(normalize(text), pending.expected["frequency"]):
            return None  # a frequency was read back: the grammar checks it
        tokens = normalize(text)
        if match_intents(tokens):
            return None  # asking for something ("holding short, ready for departure"), not taking the handoff
        words = set(re.findall(r"[a-z']+", text.lower()))
        issued = self.state.issued.get(pending.instruction_id)
        old = set(re.findall(r"[a-z']+", issued.facility.station.lower())) if issued is not None else set()
        # The new station's own words: "Toronto" says it, "Montreal" doesn't when it's Montreal Departure to Montreal Centre.
        station = set(re.findall(r"[a-z']+", new.station.lower())) - old - {"center", "centre", "control", "radar"}
        if not (words & (HANDOFF_ACK_WORDS | station)):
            return None
        return Interpretation(kind="readback", intent=pending.instruction_id, status="correct", confidence=0.8, text=text)

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
        if again.kind != "readback" or again.mismatched:
            return False
        # "Midfield left downwind 34L" names the runway just read back, but it's a report, not a repeat.
        fresh = GrammarInterpreter().interpret(text, None, InterpretContext(callsign=self._callsign()))
        return fresh.intent not in ("position_report", "report_final")

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
            ground = self.facility("ground") if facility.controller == "clearance" else None
            if ground is not None and channel_khz(ground.mhz) != channel_khz(facility.mhz) \
                    and pending.instruction_id.startswith(("clearance.ifr", "vfr.class_b_departure")):
                # Delivery done: "readback correct, contact Denver Ground 120.15 when ready".
                self._schedule(t, "clearance.readback_correct_ground", {"station": ground.station, "frequency": ground.mhz},
                               facility, handoff_to=ground)
            elif self.library.get(pending.instruction_id).ack == "readback_correct":
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
        before = len(self._scheduled)
        out = self._request(interp, facility, t)
        if (topic := interp.values.get("asks")) and interp.intent != "question" \
                and (answer := self._answer_message(topic, interp.text or "")) is not None:
            # "Short final runway 32, and can we also get the altimeter?": the answer rides along with the reply.
            replies = self._scheduled[before:]
            if replies:
                replies[-1].note = replies[-1].note + answer if replies[-1].note is not None else answer
            else:
                self._schedule(t, "common.info", {"message": answer}, facility, expects_readback=False)
        return out

    def _request(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        st = self.state
        intent = interp.intent
        own = st.aircraft
        if intent == "ready_to_taxi" and st.phase is not None and P(st.phase) in (P.LANDING, P.TAXI_IN):
            intent = "request_taxi_parking"  # landed: "request taxi" is to the gate, never back out to a runway
        if intent == "ready_for_departure" and st.phase is not None and "taxi" not in st.clearances \
                and facility.controller in ("ground", "clearance") and P(st.phase) in (P.PARKED, P.PUSHBACK, P.TAXI_OUT):
            intent = "ready_to_taxi"  # "we'd like a taxi for the departure" from the gate: ground's taxi, not tower
        if intent == "clear_of_runway" and own is not None and not own.on_ground:
            intent = "report_final"  # "are we clear to land?" on final: never "contact ground" to an aircraft in the air
            interp = replace(interp, intent=intent)
        if intent == "request_turn" and st.phase is not None and P(st.phase) in (P.PARKED, P.PUSHBACK) \
                and facility.controller == "ground" and (tail := tail_side(normalize(interp.text or ""))) is not None:
            # "Can we get a tail right?" at the gate: the pushback, tail right (Seattle's gate A5: "unable" twice).
            interp = replace(interp, intent="request_pushback", values={**interp.values, "tail": tail})
            intent = "request_pushback"
        if intent == "request_crossing":
            return self._crossing_request(interp, facility, t, own)
        if intent == "need_time":
            self._takeoff_wait = None  # nothing more from tower until the pilot says ready
            pending = st.pending
            if pending is not None and pending.instruction_id.startswith("ground.taxi") and facility.controller == "ground":
                # "We're not quite ready" with the taxi just given: it's taken back, not asked for again ("did you
                # copy?") while they finish up at the gate.
                st.pending = None
                st.clearances.pop("taxi", None)
                self._assign(taxi_route=())
            self._schedule(t, "common.advise_ready", {}, facility, expects_readback=False)
            return []
        if (letter := interp.values.get("atis")) and st.phase is not None and P(st.phase) not in DEPARTURE_PHASES:
            self._assign(arrival_atis=letter)  # "with information Delta" on arrival: the destination's ATIS
        if intent == "radio_check":
            self._schedule(t, "common.radio_check", {"station": facility.station}, facility, expects_readback=False)
            return []
        if intent == "pleasantry":
            if self.phraser is not None and self.llm_mode in ("mostly_llm", "llm"):
                return self._phrase(interp, facility, t, "reply", otherwise="common.pleasantry")
            self._schedule(t, "common.pleasantry", {}, facility, expects_readback=False)
            return []
        if intent == "report_standard":
            # On the standard setting: up in the flight levels, as it should be (ATC reads the level either way).
            # Below the transition altitude it's worth the local setting.
            own_ft = own.alt_indicated_ft if own is not None else 0.0
            info = self.current_atis(self._working_airport())
            if own is not None and own_ft < self.region.transition_ft and info is not None \
                    and info.weather.altimeter_inhg is not None:
                parts = [(f"{self._altimeter_word} {{altimeter}}", {"altimeter": info.weather.altimeter_inhg})]
                self._schedule(t, "common.info", {"message": self._phrases(parts)}, facility, expects_readback=False)
            else:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return []
        if intent == "question":
            return self._answer(interp, facility, t)
        if intent == "request_altitude":
            self._altitude_request(interp, facility, t, own)
            return []
        if intent == "other":
            return self._decline(interp, facility, t)
        if self._vfr and self._vfr_request(interp, facility, t, own):
            return []
        unscripted = {
            "request_direct": self._direct, "request_vectors": self._vectors, "request_runway": self._runway_request,
            "request_return": self._return, "request_diversion": self._divert, "going_around": self._go_around, "report_conditions": self._pirep,
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
        elif intent == "request_pushback" and (interp.values.get("start_only") or start_only(normalize(interp.text or ""))):
            # "Requesting engine startup" from a stand the aircraft taxis out of: no push. It was taken for a
            # question and answered "read back the altitude".
            self._schedule(t, "ground.startup", {}, facility, clearance="pushback")
        elif intent == "request_pushback":
            self._pushback(t, facility, own, tail=interp.values.get("tail") or tail_side(normalize(interp.text or "")))
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
        elif intent == "checkin" and self._going_around and facility.controller == "approach" and own is not None:
            self._go_around_checkin(t, facility, own)
        elif intent == "checkin" and facility.station in self._checked_in:
            # Checked in already: this is the pilot saying the altitude again ("maintaining FL360" after
            # "radar contact, maintain FL360"). Just after the controller spoke, it's an acknowledgement,
            # and nothing more is said; later on, a "roger". Never the whole check-in again, round and round.
            if t - (st.comms.last_atc_t or -math.inf) > CHECKIN_ACK_S:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
        elif intent == "checkin":
            self._checkin(t, facility, own)
        elif intent == "report_final" and (facility.controller == "approach" or own is not None and not own.on_ground
                                           and facility == st.comms.tuned and self._works_approach(own)):
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
        elif intent in ("request_taxi_parking", "clear_of_runway") and (again := self._taxi_in_given()) is not None \
                and stands.requested(normalize(interp.text or "")) is None:
            # Asked again after reading it back right: said again, with no new readback wanted. A fresh one waited for
            # a readback the copilot had given a few seconds before, then "how do you read?".
            self._schedule(t, again.instruction_id, again.slots, facility, expects_readback=False)
        elif intent in ("request_taxi_parking", "clear_of_runway"):
            self._taxi_in(t, facility, own, requested=stands.requested(normalize(interp.text or "")))
        elif intent == "say_again":
            last = st.last_issued
            if last is not None and last.facility.controller == facility.controller:
                self._schedule(t, last.instruction_id, last.slots, facility)
            else:
                self._schedule(t, "common.say_again", {}, facility)
        # acknowledge: nothing to say
        return []

    # --- unscripted moments: requests off the standard flow ----------------------------------------------

    def _crossing_request(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> list[BusEvent]:
        """ "Request to cross runway 08R": crossed, if it's a runway the taxi goes over (or the one being held
        short of) and not the departure runway; otherwise "hold short"."""
        st, ctx = self.state, self.tracker.context
        asked = str(interp.values.get("runway") or "")
        geo = ctx.airport
        runway = None
        if geo is not None and asked:
            end = geo.end(asked)
            runway = end.runway.name if end is not None else None
        if runway is None and ctx.hold_short is not None:
            runway = ctx.hold_short.runway.name
        departure = st.assignments.departure_runway
        if runway is None or (departure and departure in runway.split("/")):
            self._schedule(t, "common.say_again" if runway is None else "tower.hold_short_runway",
                           {} if runway is None else {"hold_short": departure}, facility)
            return []
        self._crossed.add(runway)
        self._crossing = runway
        self._schedule(t, "ground.cross_runway", {"runway": asked or runway.split("/")[0]}, facility)
        return []

    def _at_departure_runway(self, runway: str) -> bool:
        """At the departure runway: on it, holding short of it, or at its end. Not at another runway's hold line on
        the way (Vancouver's 08R, crossed to get to 31)."""
        ctx, own = self.tracker.context, self.state.aircraft
        geo = self.geometry(self.state.flight.origin)
        end = geo.end(runway) if geo is not None else None
        if end is None or own is None:
            return True  # nothing to check it against
        if ctx.on_runway and ctx.runway is not None:
            return ctx.runway.name == end.runway.name
        if ctx.hold_short is not None and ctx.hold_short_distance_m is not None and ctx.hold_short_distance_m <= AT_HOLD_M:
            return ctx.hold_short.runway.name == end.runway.name
        return math.dist(geo.xy(own.lat, own.lon), end.threshold) <= AT_RUNWAY_END_M

    def _lined_up(self, runway: str) -> bool:
        ctx = self.tracker.context
        return ctx.on_runway and ctx.runway_end is not None and ctx.runway_end.ident == runway

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
        leg = self._vector(own, plan["final"]) if plan else None
        if plan is None or leg is None:
            self._schedule(t, "common.unable", {}, facility)
            return
        heading = self._magnetic(own, leg.heading_true)
        self._vector_leg, self._vector_t = leg.leg, t  # the pattern starts here; approach carries on from it
        self._vectors_named = True
        if leg.leg in ("join", "downwind", "base"):
            self._vector_side = leg.side
        self._schedule(t, "approach.vectors", {"heading": heading, "approach": plan["approach"]}, facility,
                       on_issue=lambda: self._assign(heading=heading, approach=plan["approach"].display,
                                                     arrival_runway=plan["approach"].landing_runway))

    def _radar_vectors(self, own: OwnshipState) -> bool:
        """Approach turning the aircraft onto the final and slowing it down, without being asked.

        Until now a flight was left to find its own way to the runway and only heard from approach when
        the clearance came. A radar controller turns you onto the localiser and manages your speed, and
        that is most of what talking to approach sounds like.
        """
        st, ctx, t = self.state, self.tracker.context, own.t
        tuned = st.comms.tuned
        if tuned is None or not self._works_approach(own) or "approach" in st.clearances:
            return False
        if (st.comms.last_pilot_t or -math.inf) < self._tuned_since:
            return False  # wait for the check-in
        plan = self._arrival_plan(own)
        runway = plan["final"] if plan else st.assignments.arrival_runway
        if runway is not None and plan is not None and (on_star := self._on_star(own, runway)) is not None:
            # Flying the filed arrival: no headings. Where it runs down the final, the approach clearance comes
            # on the way; where it ends off to one side, the vectors start at its end.
            joins, left_nm = on_star
            if joins and left_nm <= STAR_CLEAR_NM:
                self._schedule(t, "approach.cleared_star", {"approach": plan["approach"]}, tuned, delay=False,
                               clearance="approach",
                               on_issue=lambda: (self._assign(approach=plan["approach"].display,
                                                              arrival_runway=plan["approach"].landing_runway),
                                                 setattr(self, "_going_around", False)))
                return True
            if joins or left_nm > STAR_END_NM:
                return self._speed_control(own, t, tuned) if ctx.destination_distance_nm is not None \
                    and ctx.destination_distance_nm <= VECTOR_FROM_NM else False
        # All the way in: the downwind, base and intercept happen inside 20 miles. The intercept heading carries
        # the approach clearance; until then there is always a heading being flown.
        distance = ctx.destination_distance_nm
        if runway is None or distance is None or not 2.0 < distance <= VECTOR_FROM_NM:
            return False
        if self._speed_control(own, t, tuned):
            return True
        established = ctx.final is not None and ctx.final.end.ident == runway
        if established:
            return False  # on the final already: nothing to vector
        leg = self._vector(own, runway)
        if leg is None:
            return False
        # Room between instructions, except for the turn onto the final: that one can't wait.
        cutting_in = leg.leg == "intercept" and self._vector_leg != "intercept"
        if t - self._vector_t < (INTERCEPT_GAP_S if cutting_in else VECTOR_GAP_S):
            return False
        heading = self._magnetic(own, leg.heading_true)
        assigned = st.assignments.heading
        turn = self._turn_towards(own.hdg_mag, heading)
        if assigned is None and turn is None:  # already pointing about right: it still gets a heading to fly
            turn = "right" if ((heading - own.hdg_mag) % 360) < 180 else "left"
        new_heading = turn is not None and not (
            assigned is not None and abs(((heading - assigned + 540) % 360) - 180) < REVECTOR_DEG)  # still turning onto it
        # Down with the miles left to fly, never a climb. On its own (no turn to go with it) only a real step.
        level = st.assignments.altitude_ft or int(round(own.alt_indicated_ft, -2))
        step = self._step_altitude(own, plan) if plan else None
        gap = 1000 if new_heading else 2000
        descend = step if step is not None and step <= level - gap and own.alt_indicated_ft > step + 300 else None
        if descend is not None and not new_heading and t - self._descend_t < DESCEND_GAP_S:
            descend = None
        if not new_heading and descend is None:
            return False
        self._vector_t = t
        if descend is not None:
            self._descend_t = t
        if new_heading:
            self._vector_leg = leg.leg
            if self._vector_side is None and leg.leg in ("join", "downwind", "base"):
                self._vector_side = leg.side
        if new_heading and leg.leg == "intercept" and plan is not None:
            # "Turn left heading 190, maintain 4,000 until established on the localizer, cleared ILS 16L approach."
            altitude = min(level, self._intercept_altitude(own, plan))
            kind = plan["approach"].kind
            # On the localizer (ILS, LOC, LDA, SDF, back course), or on the final approach course (VOR, NDB, RNAV).
            template = "approach.intercept_visual" if kind == "VISUAL" else \
                "approach.intercept_cleared" if kind in LOCALIZER_KINDS else "approach.intercept_course"
            self._schedule(t, template,
                           {"heading": heading, "turn": turn, "altitude": altitude, "approach": plan["approach"]}, tuned,
                           delay=False,
                           clearance="approach",
                           on_issue=lambda: (self._assign(heading=heading, altitude_ft=altitude, approach=plan["approach"].display,
                                                          arrival_runway=plan["approach"].landing_runway),
                                             setattr(self, "_going_around", False)))
        elif new_heading and descend is not None:
            self._schedule(t, "approach.turn_descend", {"heading": heading, "turn": turn, "altitude": descend}, tuned,
                           delay=False, on_issue=lambda: self._assign(heading=heading, altitude_ft=descend))
        elif new_heading:
            instruction = {"downwind": "approach.downwind", "base": "approach.base"}.get(leg.leg, "approach.turn")
            if self._vectors_named and instruction != "approach.base":
                instruction = "approach.heading"  # what the vectors are for was said with the first of them
            self._vectors_named = True
            self._schedule(t, instruction, {"heading": heading, "turn": turn, "approach": plan["approach"],
                                            "runway": runway}, tuned, delay=False,
                           on_issue=lambda: self._assign(heading=heading))
        else:
            self._schedule(t, "common.descend", {"altitude": descend}, tuned, delay=False,
                           on_issue=lambda: self._assign(altitude_ft=descend))
        return True

    def _on_star(self, own: OwnshipState, runway: str) -> tuple[bool, float] | None:
        """On the filed STAR, not yet at the end of it: (whether it runs onto ``runway``'s final, the miles left
        to where it joins the final or, if it doesn't, to its last fix). None: no STAR, or off it (a
        shortcut, a go-around, vectors already)."""
        if self._going_around or self._vector_leg is not None or not self.cfg.star:
            return None
        geo = self.geometry(self.state.flight.destination)
        end = geo.end(runway) if geo is not None else None
        fixes = self.route.procedure(self.cfg.star, "DSC")
        if geo is None or end is None or len(fixes) < 2:
            return None
        join = procedures.star_join(geo, end, fixes)
        target = join if join is not None else fixes[-1]
        where = procedures.progress(geo, fixes[:fixes.index(target) + 1], own.lat, own.lon, target)
        if where is None or where.off_nm > procedures.ON_PATH_NM or (join is not None and where.left_nm <= 0):
            return None
        return join is not None, where.left_nm

    def _speed_control(self, own: OwnshipState, t: float, facility: Facility) -> bool:
        """ "Reduce speed to 210 knots": only worth saying to something fast enough to need it."""
        ctx = self.tracker.context
        if own.ias_kt < SPEED_CONTROL_MIN_KT or ctx.destination_distance_nm is None:
            return False
        assigned = self.state.assignments.speed_kt
        traffic = None
        for distance, speed, flag in SPEED_GATES:
            if assigned is not None and speed >= assigned:
                continue  # slowed down already: never sped up again (210, then "maintain 250", then 180)
            if speed == 250 and own.alt_indicated_ft >= SPEED_LIMIT_BELOW_FT:
                continue
            if speed < 250:
                traffic = self._landing_ahead(own) if traffic is None else traffic
                if not traffic:
                    continue
            if ctx.destination_distance_nm <= distance and own.ias_kt > speed + 20 and flag not in self.state.flags:
                self.state.flags.add(flag)
                self._vector_t = t
                self._schedule(t, "approach.speed", {"speed": speed}, facility, delay=False,
                               on_issue=lambda s=speed: self._assign(speed_kt=s))
                return True
        return False

    def _landing_ahead(self, own: OwnshipState) -> bool:
        """Another aircraft on its way down to the destination ahead of this one: airborne, low, and nearer it."""
        geo = self.geometry(self.state.flight.destination)
        if geo is None:
            return False
        mine = geo.distance_nm(own.lat, own.lon)
        elevation = geo.airport.elev_ft
        return any(not tgt.on_ground and tgt.alt_ft - elevation < SPEED_TRAFFIC_AGL_FT
                   and (theirs := geo.distance_nm(tgt.lat, tgt.lon)) < min(mine, SPEED_TRAFFIC_NM)
                   for tgt in self._traffic.values())

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
        """A magnetic heading for the next leg onto ``runway``'s final (vectors.py), and whether it joins it."""
        leg = self._vector(own, runway)
        return (self._magnetic(own, leg.heading_true), leg.leg == "intercept") if leg is not None else None

    def _vector(self, own: OwnshipState, runway: str) -> vectors.Vector | None:
        geo = self.geometry(self.state.flight.destination)
        end = geo.end(runway) if geo is not None else None
        if geo is None or end is None:
            return None
        leg = vectors.vector(geo, end, own.lat, own.lon, slow=own.gs_kt < vectors.SLOW_KT, after=self._vector_leg,
                             side=self._vector_side, heading_true=own.hdg_true)
        if leg.leg in ("straight_in", "base", "intercept") and self._vector_leg != "intercept" \
                and vectors.too_high(leg, own.alt_indicated_ft, geo.airport.elev_ft, slow=own.gs_kt < vectors.SLOW_KT):
            self._vector_side = None  # a new downwind, on the side it is on now
            return vectors.extended(geo, end, own.lat, own.lon)
        return leg

    @staticmethod
    def _magnetic(own: OwnshipState, true: float) -> int:
        """A true heading as a magnetic one to give on the radio, in tens."""
        magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180
        return int(round(((true - magvar) % 360) / 10) * 10) % 360 or 360

    def _intercept_altitude(self, own: OwnshipState, plan: dict[str, Any]) -> int:
        """Joining the final: at or under the glideslope from where the aircraft will meet it, in thousands."""
        geo = self.geometry(self.state.flight.destination)
        leg = self._vector(own, plan["final"])
        if geo is None or leg is None:
            return plan["approach_alt"]
        joins = leg.join_nm if leg.join_nm is not None else leg.track_nm
        under = geo.airport.elev_ft + joins * 0.9 * vectors.GLIDESLOPE_FT_PER_NM
        return max(plan["approach_alt"], int(under // 500 * 500))

    def _step_altitude(self, own: OwnshipState, plan: dict[str, Any]) -> int:
        """Where approach wants the arrival now: down with the miles still to fly (vectors.step_altitude)."""
        leg = self._vector(own, plan["final"])
        geo = self.geometry(self.state.flight.destination)
        if leg is None or geo is None:
            return plan["approach_alt"]
        return vectors.step_altitude(leg.track_nm, geo.airport.elev_ft, plan["approach_alt"])

    def _set_arrival(self, runway: str | None, kind: str | None, own: OwnshipState | None) -> Approach | None:
        """The pilot's choice of arrival runway and approach, if the airport and the weather allow it."""
        st = self.state
        geo = self.geometry(st.flight.destination)
        plan = self._arrival_plan(own) if own is not None else None
        runway = runway or (plan["approach"].landing_runway if plan else None)
        end = geo.end(runway) if geo is not None and runway else None
        if end is None or own is None:
            return None
        weather = self.weather.surface(st.flight.destination or "", self.tracker.context_builder.airports)
        if weather is not None and components(end, weather)[0] < -MAX_TAILWIND_REQUEST_KT:
            return None
        if kind == "VISUAL" and weather is not None and weather.visibility_sm is not None and weather.visibility_sm < 3:
            return None
        wanted = kind if kind in APPROACH_KINDS else None
        approach = self._approach_for(end.ident, has_ils=end.has_ils, requested=wanted)
        if wanted is not None and not same_approach(approach.kind, wanted):
            return None  # the airport doesn't have that approach: "unable", with the wind or as it stands
        self._approach_kind = approach
        self._assign(arrival_runway=approach.landing_runway, approach=approach.display)
        return approach

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
            if end is not None and self._closed(st.flight.origin, end.ident):
                self._schedule(t, "common.runway_closed", {"runway": end.ident}, facility)
                return
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
                # Something to expect, not a clearance: no readback owed ("did you copy?" to a "thank you" at Toronto).
                self._schedule(t, "common.expect_runway", {"runway": end.ident}, facility, expects_readback=False,
                               on_issue=lambda: self._assign(departure_runway=end.ident))
            return
        if facility.controller == "tower" and own is not None and not own.on_ground and runway:
            self._tower_runway(runway, facility, t, own)  # "request runway 01" on final: cleared to land there
            return
        if not self._airborne_controller(facility, own) or "approach" in st.clearances:
            self._schedule(t, "common.unable", {}, facility)
            return
        if runway and self._closed(st.flight.destination, runway):
            self._schedule(t, "common.runway_closed", {"runway": runway}, facility)
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
        elif self._closed(st.flight.destination, end.ident):
            self._schedule(t, "common.runway_closed", {"runway": end.ident}, facility)
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
        self._new_destination(origin)
        plan = self._arrival_plan(own)
        if plan is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        altitude, _ = self._arrival_altitude(plan["arrival_alt"], own, "center")
        self._schedule(t, "common.return", {"destination": self._airport_name(origin), "altitude": altitude,
                                            "approach": plan["approach"]}, facility,
                       on_issue=lambda: self._assign(altitude_ft=altitude, approach=plan["approach"].display,
                                                     arrival_runway=plan["approach"].landing_runway),
                       note=self._altimeter_note(origin))
        st.flags.add("descend")  # the return clearance is the descent

    def _go_around(self, interp: Interpretation | None, facility: Facility, t: float, own: OwnshipState | None) -> None:
        """Runway heading, climb, and back to approach for another try."""
        st = self.state
        if interp is not None and own is not None and self._going_around and facility.controller == "approach":
            self._go_around_checkin(t, facility, own)  # "going around" to approach, sent there by tower
            return
        if own is None or self._going_around:
            return
        self._going_around = True  # until approach clears the next approach
        self._assign(heading=None)
        self._heading_given = None
        self._vector_leg = self._vector_side = None  # vectored round again from wherever the missed approach leaves it
        self._vectors_named = False  # "vectors ILS 32" once more, on the first heading of the new approach
        st.flags.discard("approach_tower")  # the next approach is handed to tower like the first
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

    def _go_around_checkin(self, t: float, facility: Facility, own: OwnshipState) -> None:
        """Back with approach after going around ("going around, 1,000 climbing 2,200"): radar contact, the altitude
        tower gave, and vectors for another go. (Until now it got no answer at all.)"""
        plan = self._arrival_plan(own)
        altitude = self.state.assignments.altitude_ft or int(round(own.alt_indicated_ft, -2))
        self._checked_in.add(facility.station)
        if plan is None:
            self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return
        self._schedule(t, "approach.go_around_checkin", {"station": facility.station, "altitude": altitude,
                                                         "approach": plan["approach"]}, facility,
                       on_issue=lambda: self._assign(altitude_ft=altitude))

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
            self._emergency_started(t)
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
        # "Mayday, engine fire, request vectors to the nearest airport": the intentions come with the call.
        divert = next((m for m in match_intents(normalize(interp.text)) if m.intent == "request_diversion"), None)
        if divert is not None:
            self.diversion.asked, self.diversion.asked_fix = True, divert.values.get("fix")
        if "emergency_priority" not in st.flags and own is not None and not own.on_ground \
                and facility.controller in ("departure", "center", "approach"):
            if not self._priority_ready(t):
                st.flags.add("emergency_priority_due")  # the airports around are on their way: then
            elif self.diversion.asked:
                self._divert_asked(t, own, facility)
            else:
                self._emergency_priority(t, own, facility)
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
        if "emergency_priority_due" in st.flags and tuned.controller in ("departure", "center", "approach") \
                and self._priority_ready(t):
            if self.diversion.asked:
                self._divert_asked(t, own, tuned)
            else:
                self._emergency_priority(t, own, tuned)
            return True
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
        st = self.state
        topic = interp.values.get("topic", "other")
        if topic == "frequency":
            named = next((CONTROLLER_WORDS[w] for w in interp.text.lower().replace("?", " ").replace(".", " ").split()
                          if w in CONTROLLER_WORDS and CONTROLLER_WORDS[w] != facility.controller), None)
            target = self.facility(named) if named else st.comms.expected
            own = st.aircraft
            if named == "tower" and target is not None and own is not None and not own.on_ground \
                    and "approach" not in st.clearances and self._works_approach(own) and self._arrival_plan(own) is not None:
                self._clear_approach(t, own, facility, delay=True)  # "cleared ILS runway 13 approach, contact tower"
                return []
            if target is not None and target != facility:
                if named == "tower" and facility.controller == "ground":
                    st.flags.add("handoff_tower")  # the pilot has it now; no second "contact tower"
                self._handoff(t, "common.contact", facility, target, delay=True)
                return []
        text = interp.text or ""
        exact = False
        if (lengths := self._runway_lengths(text, facility)) is not None:
            message, answers_it, exact = lengths, True, True  # "how long is runway 24R?": the airport data has it
        else:
            message = self._answer_message(topic, text)
            elsewhere = topic in ("weather", "wind") and self._weather_elsewhere(text) is not None
            # The sim's data answers the question only when it asks for the topic and nothing more: "say altimeter",
            # not "how long is runway 24R" (which isn't "which runway") or "any weather en route" (not here and now).
            # ... and the pilot's own words are about the topic: the model's label alone ("weather" for "anything going
            # on at San Francisco?") doesn't make the wind and altimeter the answer.
            answers_it = message is not None and (elsewhere or self._asks_only_topic(text, facility, topic)) \
                and question_topic(text) == topic
        plain = answers_it and self._plain_question(text, facility)
        # The model's reply must give the data's answer when it is exactly the answer: a runway's length, or a plain
        # "say altimeter". To "anything going on at San Francisco?" (read as about the weather) it's only a fact.
        require = exact or plain
        if self.phraser is not None and (self.llm_mode in ("mostly_llm", "llm") or not plain):
            # The model words the answer to what was asked, from the facts (the data's answer among them when it is
            # one), and says what isn't known. Failing that, the data's answer if it answers it, else "not available":
            # never the answer to a different question.
            return self._phrase(interp, facility, t, "answer", known=message if answers_it else None, require=require)
        if answers_it:
            self._schedule(t, "common.info", {"message": message}, facility)
            return []
        self._schedule(t, "common.info", {"message": UNAVAILABLE}, facility)
        return []

    def _airport_here(self, facility: Facility | None) -> str | None:
        """The airport a question to ``facility`` is about by default: its own, else the one the flight is at or going to."""
        st = self.state
        arriving = st.phase is not None and P(st.phase) not in DEPARTURE_PHASES
        return (facility.airport if facility is not None else None) or (st.flight.destination if arriving else st.flight.origin)

    def _runway_lengths(self, text: str, facility: Facility) -> Phrase | None:
        """The answer to "how long is runway 24R?" (or "the runway": the one in use) from the airport data, or None
        when that isn't the question or the data has no such runway."""
        words = re.findall(r"[a-z']+", text.lower())
        if "runway" not in words or not ({"long", "length"} & set(words)):
            return None
        geo = self.geometry(self._airport_here(facility))
        if geo is None:
            return None
        ends = {end.ident: r for r in geo.airport.runways for end in (r.primary, r.secondary)}
        # "24 right", "two four R": (24, "R"); a number alone is either side.
        said = self._runways_said(text)
        asked = [ident for ident in ends if (m := re.fullmatch(r"(\d{1,2})([LRC]?)", ident))
                 and any(n == int(m.group(1)) and side in ("", m.group(2)) for n, side in said)]
        if not asked and (in_use := self._runway_in_use(self.state.aircraft)) in ends:
            asked = [in_use]
        if not asked:
            return None
        parts = [Phrase(*self.library.fill("runway {runway} is {length} feet", {"runway": ident, "length":
                                          int(round(ends[ident].length_m * 3.28084))}, context="runway length"))
                 for ident in asked]
        return sum(parts[1:], parts[0])

    @staticmethod
    def _runways_said(text: str) -> set[tuple[int, str]]:
        """The runways named in ``text``: (24, "R") for "24 right" or "two four R"; a number with no side counts only
        next to the word "runway" ("one last thing, how long is runway 19L" is 19L, never runway 1), and only when no
        runway with a side was named."""
        tokens = normalize(text)
        sided, bare = set(), set()
        for i, tok in enumerate(tokens):
            if tok.kind != "number" or not tok.text.isdigit() or not 1 <= int(tok.text) <= 36:
                continue
            after = tokens[i + 1].text if i + 1 < len(tokens) else ""
            if after in ("l", "r", "c", "left", "right", "center", "centre"):
                sided.add((int(tok.text), after[0].upper()))
            elif "runway" in {t.text for t in tokens[max(0, i - 2):i + 3]}:
                bare.add((int(tok.text), ""))
        return sided or bare

    def _asks_only_topic(self, text: str, facility: Facility, topic: str) -> bool:
        """A question about its topic, here and now: not how long or wide, when or why, or somewhere else ("the
        winds at Boeing" to Boeing Tower is here; "the altimeter in Quebec" from Montreal isn't). The weather's the
        sim's here and now; the runway, squawk and the rest are the flight's, arrival included."""
        words = re.findall(r"[a-z']+", text.lower())  # as said: normalizing makes "Quebec" the letter Q
        later = (ELSEWHERE - PLACE_WORDS) if topic in ("altimeter", "wind", "weather") else set()
        if set(words) & (QUALIFIERS | later) or any(a == "how" and b in HOW_QUALIFIERS for a, b in itertools.pairwise(words)):
            return False
        here = {"the", "this", "field", "airport", "here", "your", "our", "station"} | set(facility.station.lower().split())
        own = self.state.aircraft
        for icao in (facility.airport, self.state.flight.origin, self.state.flight.destination):
            geo = self.geometry(icao)
            if geo is not None and (icao == facility.airport or own is not None and geo.distance_nm(own.lat, own.lon) <= NEARBY_NM):
                here |= {icao.lower()} | set(re.findall(r"[a-z]+", self._airport_name(icao).lower()))
        return not any(w in PLACE_WORDS and i + 1 < len(words) and words[i + 1] not in here for i, w in enumerate(words))

    def _plain_question(self, text: str, facility: Facility) -> bool:
        """A question the sim's data answers by its topic alone: short ("say altimeter", "what's the wind") and
        about here, not somewhere else or later."""
        said = {w.lower() for w in (facility.station, speech.callsign_display(self._callsign()),
                                    speech.callsign_display(self._callsign().short)) for w in w.split()}
        words = [t.text for t in normalize(text) if t.kind == "word" and t.text not in said and t.text not in COURTESY]
        return len(words) <= PLAIN_QUESTION_WORDS and not set(words) & ELSEWHERE

    def _weather_airport(self, text: str) -> str | None:
        """The airport a weather question is about, when it isn't the one here: the one it names ("KJLN", "Kilo
        Juliet Lima November", "Joplin", "the destination"), else the destination once the flight is away from where
        it took off. "What is the weather at the airport?" in the cruise was given Des Moines's, left 300 miles
        behind. None: the airport here."""
        st, own = self.state, self.state.aircraft
        flight = st.flight
        words = re.findall(r"[a-z]+", text.lower())
        letters = "".join(PHONETIC_LETTERS.get(w, w.upper() if len(w) == 1 else "") for w in words)  # spelled out
        known = [a for a in (flight.destination, flight.origin) if a] + list(self.tracker.context_builder.airports)
        for icao in dict.fromkeys(known):
            if icao.upper() in letters.upper() or icao.lower() in words:
                return icao
        if {"destination", "arrival"} & set(words):
            return flight.destination
        for icao in (flight.destination, flight.origin):
            geo = self.geometry(icao)
            names = {w.lower() for w in re.findall(r"[A-Za-z]+", geo.airport.name) if len(w) > 3} if geo else set()
            if set(words) & (names - {"international", "regional", "intl", "rgnl", "airport", "field", "muni"}):
                return icao
        if own is not None and not own.on_ground and flight.destination:
            origin = self.geometry(flight.origin)
            if origin is None or origin.distance_nm(own.lat, own.lon) > NEARBY_NM:
                return flight.destination
        return None

    def _weather_elsewhere(self, text: str) -> str | None:
        """The airport a weather question is about when the aircraft isn't at it (``_weather_airport``), else None."""
        icao, own = self._weather_airport(text), self.state.aircraft
        geo = self.geometry(icao)
        if icao is None or (own is not None and geo is not None
                            and (own.on_ground or geo.distance_nm(own.lat, own.lon) <= NEARBY_NM)):
            return None
        return icao

    def _report_message(self, icao: str, topic: str) -> Phrase | None:
        """Another airport's weather, as ATC gives it: "Joplin Regional weather, wind 190 at 7, visibility 10,
        altimeter 30.10" (the sim's own, sampled there, or its METAR). None when nothing is known of it."""
        weather = self.weather.surface(icao, self.tracker.context_builder.airports)
        if weather is None:
            return None
        geo = self.geometry(icao)
        name = speech.airport_name(geo.airport.name, icao) if geo is not None else icao
        parts: list[tuple[str, dict[str, Any]]] = [(f"{name} wind {{wind}}", {"wind": weather.wind})]
        if topic == "weather":
            if weather.visibility_sm is not None:
                parts.append((f"visibility {weather.visibility_sm:g}", {}))
            if weather.temperature_c is not None:
                parts.append((f"temperature {weather.temperature_c}", {}))
            if weather.altimeter_inhg is not None:
                parts.append((f"{self._altimeter_word} {{altimeter}}", {"altimeter": weather.altimeter_inhg}))
        phrases = [Phrase(*self.library.fill(text, slots, context=f"answer {topic}")) for text, slots in parts]
        return sum(phrases[1:], phrases[0])

    def _answer_message(self, topic: str, text: str = "") -> Phrase | None:
        """The answer to a question about ``topic`` from the sim's data ("expect runway 36R for departure",
        "altimeter 29.87"), or None when there's nothing to say from it. ``text``: the question, for the airport a
        weather question is about."""
        st, own = self.state, self.state.aircraft
        if topic in ("weather", "wind") and (elsewhere := self._weather_elsewhere(text)) is not None:
            return self._report_message(elsewhere, topic)  # not where the aircraft is: never the wind up here
        parts: list[tuple[str, dict[str, Any]]] = []
        altimeter = round(own.altimeter_setting_inhg, 2) if own and 25 < own.altimeter_setting_inhg < 33 else None
        if (airport := st.comms.tuned.airport if st.comms.tuned is not None else None) is not None:
            altimeter = self.weather.altimeter_at(airport) or altimeter  # an airport's controller gives its own
        if topic in ("altimeter", "weather", "atis") and altimeter is not None:
            parts.append((f"{self._altimeter_word} {{altimeter}}", {"altimeter": altimeter}))
        if topic in ("wind", "weather") and own is not None:
            parts.insert(0, ("wind {wind}", {"wind": self._wind(own)}))
        arriving = st.phase is not None and P(st.phase) not in DEPARTURE_PHASES
        info = self.current_atis(st.flight.destination if arriving else st.flight.origin)
        if topic == "atis" and info is not None:
            parts.insert(0, ("information {atis} is current", {"atis": info.letter}))
        plan = self._arrival_plan(own) if topic == "runway" and own is not None and not own.on_ground else None
        if plan is not None:
            # Airborne, "the runway" is the one at the other end ("an estimated arrival runway to Seattle?" in the
            # cruise was answered with Vancouver's 31): the arrival runway and the approach to expect.
            parts.append(("expect {approach} approach", {"approach": plan["approach"]}))
        elif topic == "runway" and (runway := self._runway_in_use(own)) is not None:
            given = st.assignments.arrival_runway if arriving else st.assignments.departure_runway
            if given == runway and ("taxi" in st.clearances or arriving and "approach" in st.clearances):
                parts.append(("runway {runway}", {"runway": runway}))  # the one it's cleared for already
            else:
                parts.append(("expect runway {runway}" + ("" if arriving else " for departure"), {"runway": runway}))
        if topic == "squawk" and st.assignments.squawk:
            parts.append(("squawk {squawk}", {"squawk": st.assignments.squawk}))
        if topic == "altitude" and st.assignments.altitude_ft:
            parts.append(("maintain {altitude}", {"altitude": st.assignments.altitude_ft}))
        if not parts:
            return None
        phrases = [Phrase(*self.library.fill(text, slots, context=f"answer {topic}")) for text, slots in parts]
        return sum(phrases[1:], phrases[0])

    def _decline(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        return self._phrase(interp, facility, t, "decline")

    def _phrase(self, interp: Interpretation, facility: Facility, t: float, decision: str,
                known: Phrase | None = None, *, require: bool = True, otherwise: str | Phrase | None = None) -> list[BusEvent]:
        """A reply worded by the model from the facts. ``known``: what the sim's data says to the question, one of
        the facts, and the reply when the model has none. ``otherwise``: what's said instead when the model's reply
        fails its checks (a template id, or words), chosen to fit the call; the app says it happened."""
        if self.phraser is None:
            if isinstance(otherwise, Phrase):
                self._schedule(t, "common.info", {"message": otherwise}, facility)
            else:
                self._schedule(t, otherwise or "common.unable", {}, facility, expects_readback=otherwise is None)
            return []
        callsign = self._callsign()
        callsigns = tuple({speech.callsign_display(callsign), speech.callsign_display(callsign.short), callsign.ident})
        facts = self._facts(facility, interp.text or "")
        if known is not None:
            facts["here and now"] = known.display
        more = None
        if self.rich_model:
            more = {k: v for k, v in {**self._facts(facility, everything=True), **self._flight_more(facility, t)}.items()
                    if k not in facts}
        message, exchanges = self.phraser.reply(
            pilot=interp.text, decision=decision, facts=facts, callsigns=callsigns, t=t,
            trigger={"answer": "question", "reply": "conversation"}.get(decision, "unsupported_request"),
            required=known.display if known is not None and require else "", more=more,
            persona=self._persona(facility),
        )
        if message is not None:
            self._schedule(t, "common.info", {"message": message}, facility, worded_by="model")
            return list(exchanges)
        last = exchanges[-1] if exchanges else None
        why = (f"{last.outcome}: {last.detail}" if last.detail else last.outcome) if last is not None else "no answer"
        if known is not None:
            self._schedule(t, "common.info", {"message": known}, facility)
            said = f'ATC gave the sim\'s data: "{known.display}"'
        elif isinstance(otherwise, Phrase):
            self._schedule(t, "common.info", {"message": otherwise}, facility)
            said = f'ATC said "{otherwise.display}"'
        elif otherwise is not None:
            self._schedule(t, otherwise, {}, facility, expects_readback=False)
            said = f'ATC said "{SAID.get(otherwise, otherwise)}"'
        elif decision == "answer":
            self._schedule(t, "common.info", {"message": UNAVAILABLE}, facility)
            said = f'ATC said "{UNAVAILABLE.display}"'
        else:
            self._schedule(t, "common.unable", {}, facility)
            said = 'ATC said "unable"'
        hint = " (Quick Settings → ATC → \"Let the model answer beyond the sim's data\" lets such answers through)" \
            if last is not None and facts_problem(last.detail) and not self.phraser.beyond_facts else ""
        self._not_used.append(f"the model's {WHAT_WORDED.get(decision, decision)} was turned away ({why}); {said}{hint}")
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
        if not climbing and "descend" not in st.flags and st.phase in (P.DEPARTURE, P.CRUISE) \
                and own.alt_indicated_ft >= 10000 and self._descent_due(own, lead_min=DESCENT_ASK_MIN) \
                and (plan := self._arrival_plan(own)) is not None:
            # "Request descent" (or "descent via the arrival") coming up to the top of descent: the descent
            # clearance itself, not 2,000 ft lower.
            self._clear_descent(t, own, facility, plan)
            return
        if abs(wanted - own.alt_indicated_ft) < 200:  # "request to maintain 1,500" at 1,500
            self._schedule(t, "common.maintain", {"altitude": wanted}, facility, on_issue=lambda: self._assign(altitude_ft=wanted))
            return

        cruise = st.assignments.cruise_ft or st.flight.cruise_ft

        def approve() -> None:
            self._assign(altitude_ft=wanted)
            # A new cruise only when it's above the filed one ("request FL370"): a step on the way up isn't one.
            if st.phase in (P.DEPARTURE, P.CRUISE) and climbing and (not cruise or wanted > cruise):
                st.flight.cruise_ft = wanted
                self._assign(cruise_ft=wanted)
                self.tracker.detector.cruise_ft = wanted  # cruise is wherever the pilot now levels off

        self._granted = (t, assigned, cruise)  # what it had before: "disregard the request" goes back to it
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
                # The next step of the climb: departure's top, or a centre's step, or the cruise.
                tuned = st.comms.tuned
                return max(assigned, self._climb_step(tuned, own, cruise)) if tuned is not None else cruise
            return min(assigned + 2000, MAX_OFFERED_FT)
        if st.phase is not None and P(st.phase) is P.ARRIVAL and (plan := self._arrival_plan(own)) is not None:
            return plan["arrival_alt"]
        return max(assigned - 2000, 3000)

    def _interpret_context(self, facility: Facility, t: float, confidence: float | None = None) -> InterpretContext:
        """The moment, as the language model is shown it and as its answer is checked: one snapshot of the
        engine's own state, made fresh for each transmission. The phrasing model's facts come from it too."""
        st, a = self.state, self.state.assignments
        here = [e for e in st.exchanges if e.controller == facility.controller and t - e.t <= MODEL_LAST_ATC_S]
        last_atc = next((e.text for e in reversed(here) if e.speaker == "atc"), None)
        recent = tuple(f'{"ATC" if e.speaker == "atc" else "Pilot"}: "{e.text}"' for e in here[-MODEL_RECENT:])
        traffic = self._last_traffic[1] if self._last_traffic and t - self._last_traffic[0] <= MODEL_TRAFFIC_S else None
        approach = a.approach.replace(" RWY ", " ") if a.approach else None  # given: "ILS 34R"
        if approach is None and st.phase is not None and P(st.phase) in (P.ARRIVAL, P.APPROACH) and st.aircraft is not None:
            plan = self._arrival_plan(st.aircraft)
            approach = f"{plan['approach'].kind} {plan['approach'].runway}" if plan else None
        return InterpretContext(
            callsign=self._callsign(), phase=st.phase, strict_callsign=self.cfg.strict_callsign, t=t,
            station=facility.station, station_role=facility.controller, last_atc=last_atc, confidence=confidence,
            patient=self._patient, cleared_altitude_ft=a.altitude_ft, cleared_heading=a.heading, squawk=a.squawk,
            runway=self._runway_in_use(st.aircraft), traffic=traffic, recent=recent,
            approach=approach, more=self._more_for_model(facility, t),
        )

    def _more_for_model(self, facility: Facility, t: float) -> str:
        """A cloud model's view of the whole flight, for understanding a call (empty for a local one)."""
        if not self.rich_model:
            return ""
        from localtc.atc_core.llm.phrase import more_lines

        return more_lines({**self._facts(facility, everything=True), **self._flight_more(facility, t)})

    @property
    def rich_model(self) -> bool:
        """The language model can take the whole flight (a cloud one): it gets far more than a small local one."""
        for part in (self.phraser, self.interpreter):
            if getattr(getattr(part, "backend", None), "rich", False):
                return True
        return False

    def _flight_more(self, facility: Facility | None, t: float) -> dict[str, str]:
        """The rest of the flight, for a model that can take it: the plan, the clearances, where the aircraft is and
        what it's doing, the full ATIS, and what's been said on the radio lately."""
        st, a, own = self.state, self.state.assignments, self.state.aircraft
        more: dict[str, str] = {}
        f = st.flight
        if f.callsign is not None:
            more["callsign"] = speech.callsign_display(f.callsign)
        if f.aircraft_type:
            more["aircraft"] = f.aircraft_type
        more["flight rules"] = f.rules
        for label, icao in (("origin", f.origin), ("destination", f.destination)):
            geo = self.geometry(icao)
            if icao:
                more[label] = f"{speech.airport_name(geo.airport.name, icao)} ({icao})" if geo else icao
        if self.cfg.sid:
            more["filed departure procedure"] = self.cfg.sid
        if self.cfg.star:
            more["filed arrival procedure"] = self.cfg.star
        if self.cfg.approach:
            more["filed approach"] = str(self.cfg.approach)
        if self.cfg.route:
            more["route"] = " ".join(fix.ident for fix in self.cfg.route[:80])
        for label, value in (("squawk", a.squawk), ("assigned heading", a.heading and f"{a.heading:03d}"),
                             ("assigned speed", a.speed_kt and f"{a.speed_kt} kt"), ("departure runway", a.departure_runway),
                             ("arrival runway", a.arrival_runway), ("taxi route", " ".join(a.taxi_route)),
                             ("cleared approach", a.approach), ("gate", a.gate or a.departure_gate),
                             ("cleared altitude", a.altitude_ft and speech.altitude_display(a.altitude_ft))):
            if value:
                more[label] = str(value)
        if own is not None:
            more["aircraft now"] = (f"{'on the ground' if own.on_ground else 'airborne'}, {int(round(own.alt_indicated_ft, -1))} ft, "
                                    f"{int(own.gs_kt)} kt ground speed, heading {int(own.hdg_mag) % 360:03d}, "
                                    f"{int(round(own.vs_fpm, -1))} ft/min")
            dest = self.geometry(f.destination)
            if dest is not None:
                more["to destination"] = f"{dest.distance_nm(own.lat, own.lon):.0f} nm"
            orig = self.geometry(f.origin)
            if orig is not None:
                more["from origin"] = f"{orig.distance_nm(own.lat, own.lon):.0f} nm"
        for icao in dict.fromkeys(x for x in (f.origin, f.destination) if x):
            info = self.current_atis(icao)
            if info is not None:
                more[f"{icao} ATIS"] = info.text
        said = [f'{"ATC" if e.speaker == "atc" else "Pilot"} ({e.controller or "?"}): "{e.text}"'
                for e in st.exchanges if t - e.t <= 1800][-12:]
        if said:
            more["radio, last half hour"] = " | ".join(said)
        if (tuned := st.comms.tuned) is not None:
            more["tuned"] = f"{tuned.station} {tuned.mhz:.3f}"
        if st.comms.expected is not None and st.comms.expected is not st.comms.tuned:
            more["sent to"] = f"{st.comms.expected.station} {st.comms.expected.mhz:.3f}"
        return more

    def _facts(self, facility: Facility | None = None, text: str = "", *, everything: bool = False) -> dict[str, str]:
        """What the phrasing model may use, in display form. Nothing here is an instruction to fly.

        What matters for any reply: who's talking, the time, where the flight is and is going, the weather and runway
        where it is (or the approach to expect where it's going). The rest only when the pilot's words ask about it
        (``FACT_TOPICS``): a small model recites what it's given, and "parallel landings are not available" and
        "runway 07L/25R closed" in answer to "not sure" or a give-way left pilots wondering what was going on."""
        st, own = self.state, self.state.aircraft
        words = set(re.findall(r"[a-z']+", text.lower()))

        def about(topic: str) -> bool:
            return everything or bool(words & FACT_TOPICS[topic])

        facts: dict[str, str] = {}
        if facility is not None:
            facts["controller"] = facility.station
        if st.phase:
            facts["phase"] = st.phase.lower().replace("_", " ")
        if own is not None and own.zulu_s is not None:
            minutes = int(own.zulu_s // 60) % 1440
            facts["time"] = f"{minutes // 60:02d}{minutes % 60:02d}Z"
        dest = self.geometry(st.flight.destination)
        if dest is not None:
            facts["destination"] = speech.airport_name(dest.airport.name, dest.airport.icao)
        airborne = own is not None and not own.on_ground
        if airborne:
            if st.assignments.altitude_ft:
                facts["cleared altitude"] = speech.altitude_display(st.assignments.altitude_ft)
            if cruise := st.assignments.cruise_ft or st.flight.cruise_ft:
                facts["filed cruise altitude"] = speech.altitude_display(cruise)
        arriving = st.phase is not None and P(st.phase) not in DEPARTURE_PHASES
        here_icao = self._airport_here(facility)
        here = self.geometry(here_icao)
        near = here is not None and own is not None and (own.on_ground or here.distance_nm(own.lat, own.lon) <= NEARBY_NM)
        if near and own is not None:
            # Here's, by its plain name: given as "Seattle-Tacoma International wind" it was read out just like that.
            facts["wind"] = speech.wind_display(self._wind(own))
            if 25 < own.altimeter_setting_inhg < 33:
                facts["altimeter"] = f"{own.altimeter_setting_inhg:.2f}"
            if (runway := self._runway_in_use(own)) is not None:  # the same runway the template answers give
                facts["landing runway" if arriving else "departure runway"] = runway
        elif airborne and own is not None and (plan := self._arrival_plan(own)) is not None:
            facts["expected arrival"] = f"{plan['approach'].display} approach"  # not the runway left behind
        if (everything or words & WEATHER_WORDS) and (elsewhere := self._weather_elsewhere(text)) is not None \
                and (report := self._report_message(elsewhere, "weather")) is not None:
            # The destination's (or the airport asked about): its METAR, or the sim's own sampled there. Without it
            # the model had only the wind where the aircraft was, and said "information not available".
            facts["weather there"] = report.display
        info = self.current_atis(st.flight.destination if arriving else st.flight.origin)
        if info is not None and (near or arriving):
            facts["ATIS"] = f"information {info.letter}"
        if (pending := st.pending) is not None and facility is not None and pending.controller == facility.controller:
            from localtc.atc_core.llm.understand import _expect_line, _expected_items

            facts["waiting for the pilot to read back"] = _expect_line(_expected_items(pending))
        if here is not None and here.airport.runways:
            name = speech.airport_name(here.airport.name, here.airport.icao)
            if about("lengths"):
                # "How long is runway 24R?": the sim's airport data has it, so the answer is a fact, not a guess.
                facts[f"{name} runways"] = ", ".join(
                    f"{r.primary.ident}/{r.secondary.ident} {int(round(r.length_m * 3.28084, -2))} ft" for r in here.airport.runways)
            if about("elevation"):
                facts[f"{name} elevation"] = f"{int(round(here.airport.elev_ft))} ft"
        here_info = self.current_atis(here_icao) if here_icao else None
        if here_info is not None and (ops := here_info.operations) is not None:
            if about("notices"):
                facts["notices"] = ", ".join(n.text(here_info.icao_style) for n in ops.notices) or "none"
            if about("runways"):
                facts["landing runways"] = ", ".join(ops.landing) or "none"
                facts["departing runways"] = ", ".join(ops.departing) or "none"
        if about("pushback") and st.phase is not None and P(st.phase) in (P.PARKED, P.PUSHBACK):
            turn = self._pushback_turn(own)
            facts["pushback"] = f"tail {turn}" if turn else "straight back"
        if about("traffic"):
            facts["traffic"] = self._traffic_summary(own)
        if about("squawk") and st.assignments.squawk:
            facts["squawk"] = st.assignments.squawk
        return facts

    def _where_to_park(self, geo: AirportGeometry) -> tuple[float, float] | None:
        """Where the aircraft goes after landing: its gate if it has one, else the middle of the airport's stands
        (the terminal side)."""
        spots = list(getattr(geo.airport, "parking", ()) or ())
        a = self.state.assignments
        if a.gate_index is not None:
            spot = next((p for p in spots if getattr(p, "index", None) == a.gate_index), None)
            if spot is not None:
                return spot.lat, spot.lon
        gates = [p for p in spots if p.kind.startswith("gate")] if self._callsign().is_airline else []
        spots = gates or spots
        if not spots:
            return None
        return sum(p.lat for p in spots) / len(spots), sum(p.lon for p in spots) / len(spots)

    def _close_callsign_taken(self, text: str) -> bool:
        """A flight number heard in ``text`` that an aircraft around actually flies (the sim's traffic): then a near
        miss on ours might really be that one, and the controller asks."""
        heard = {t.text.replace(",", "").lstrip("0") for t in normalize(text) if t.kind == "number"
                 and t.text.replace(",", "").isdigit() and 1 <= len(t.text.replace(",", "")) <= 4}
        ours = self._callsign().flight_number.lstrip("0") if getattr(self._callsign(), "is_airline", False) else ""
        for target in self._traffic.values():
            number = (target.flight_number or "").lstrip("0")
            if number and number in heard and number != ours:
                return True
        return False

    def _traffic_summary(self, own: OwnshipState | None) -> str:
        """The traffic around, for "any traffic ahead of us?": on the ground, what's taxiing nearby; in the air, what
        ATC last called."""
        if own is None:
            return "none known"
        if not own.on_ground:
            called = self._last_traffic
            return called[1] if called is not None and own.t - called[0] <= MODEL_TRAFFIC_S else "none called"
        moving = [target for target in self._traffic.values() if target.on_ground and target.gs_kt >= MOVING_KT
                  and _distance_nm(own.lat, own.lon, target.lat, target.lon) <= NEARBY_TRAFFIC_NM]
        if not moving:
            return "nobody taxiing nearby"
        kinds = [k.display for k in (self._aircraft(target) for target in moving) if k is not None]
        what = f": {', '.join(dict.fromkeys(kinds))}" if kinds else ""
        return f"{'one aircraft' if len(moving) == 1 else f'{len(moving)} aircraft'} taxiing nearby{what}"

    def _runway_in_use(self, own: OwnshipState | None) -> str | None:
        st = self.state
        if st.phase is not None and P(st.phase) not in DEPARTURE_PHASES:
            if st.assignments.arrival_runway:
                return st.assignments.arrival_runway
            plan = self._arrival_plan(own) if own is not None else None
            return plan["approach"].landing_runway if plan else None
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
        initial = min(cruise, self._initial_altitude())
        departure = self.facility("departure") or self._center()
        dest_geo = self.geometry(dest)
        destination = speech.airport_name(dest_geo.airport.name, dest) if dest_geo else dest
        squawk = self._squawk()
        if any(item.instruction_id.startswith("clearance.ifr") for item in self._scheduled):
            return  # asked again while we're getting it
        if self.cfg.unscripted and random.Random(zlib.crc32(f"standby{self._callsign().ident}{self.cfg.seed}".encode())).random() < STANDBY_CHANCE:
            self._schedule(t, "clearance.standby", {}, facility)
            # The clearance comes once delivery has it: "stand by" followed a dozen seconds later sounded like
            # nobody went to get anything.
            t += random.Random(self.cfg.seed + 1).uniform(*self.cfg.standby_s)
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

    def _initial_altitude(self) -> int:
        """The altitude the clearance climbs a departure to: well clear of the field and its terrain (the sim has no
        SID altitude restrictions), and not the same 5,000 ft everywhere: one of a few steps above the field, the
        same for the same airport and SID every flight, as a published departure is."""
        geo = self.geometry(self.state.flight.origin)
        elevation = geo.airport.elev_ft if geo is not None else 0.0
        base = max(INITIAL_MIN_FT, int(math.ceil((elevation + 3000) / 1000.0)) * 1000)
        steps = [base + extra for extra in INITIAL_STEPS_FT if base + extra <= max(INITIAL_MAX_FT, base)]
        # The airport, the SID and the runway (each departure procedure tops out at its own altitude), and the flight:
        # one flight is cleared lower than another, as traffic and the time of day have it.
        runway = self.state.assignments.departure_runway or ""
        pick = zlib.crc32(f"initial{self.state.flight.origin}{self.cfg.sid}{runway}{self._callsign().ident}".encode()) % len(steps)
        return steps[pick]

    def _departure_runway(self, own: OwnshipState | None) -> str | None:
        geo = self.geometry(self.state.flight.origin)
        if geo is None or own is None:
            return None
        end = self._runway_end(self.state.flight.origin, own)
        return end.ident if end else None

    def _pushback(self, t: float, facility: Facility, own: OwnshipState | None, *, tail: str | None = None) -> None:
        """Push and start, with the nose brought round to face the way the aircraft is about to taxi.

        "Tail left" swings the tail to the pilot's left, so the nose comes round to the right: that's for a
        taxi route that leaves to the right. Straight back when the route leads off the nose or the
        airport's taxiways aren't known. A pilot who asks for the tail one way ("can we tail left?") gets it:
        they can see the gate, the stands beside it and the tug.
        """
        turn = tail if tail in ("left", "right") else self._pushback_turn(own)
        if turn is None:
            self._schedule(t, "ground.pushback_straight", {}, facility, clearance="pushback")
        else:
            self._schedule(t, "ground.pushback", {"turn": turn}, facility, clearance="pushback")

    def _pushback_turn(self, own: OwnshipState | None) -> str | None:
        geo = self.geometry(self.state.flight.origin)
        end = self._runway_end(self.state.flight.origin, own) if own is not None else None
        if geo is None or own is None or end is None:
            return None
        route = self._taxi_graph(geo).departure_route(own.lat, own.lon, end)
        if route is None:
            return None
        here = geo.xy(own.lat, own.lon)
        graph = self._taxi_graph(geo)
        for node in route.nodes:  # the first point far enough along the route to give a direction
            position = graph.positions.get(node)
            if position is None:
                continue
            east, north = position[0] - here[0], position[1] - here[1]
            far = math.hypot(east, north)
            if far < PUSHBACK_LOOK_M:
                continue
            relative = (math.degrees(math.atan2(east, north)) - own.hdg_true + 540) % 360 - 180
            if abs(relative) < PUSHBACK_STRAIGHT_DEG:
                return None
            if abs(relative) > PUSHBACK_BEHIND_DEG and far < PUSHBACK_LOOK_FAR_M:
                # Straight behind: the taxilane the push ends on, not yet which way along it the route goes (a
                # degree either side of the tail decided it). Further along the route says.
                continue
            return "left" if relative > 0 else "right"  # the route to the right: tail left, nose round to the right
        return None

    def _taxi_out(self, t: float, facility: Facility, own: OwnshipState | None, atis: str | None = None) -> None:
        geo = self.geometry(self.state.flight.origin)
        if geo is None or own is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        end = self._runway_end(self.state.flight.origin, own)
        note = self._atis_note(self.state.flight.origin, atis)
        route = self._taxi_graph(geo).departure_route(own.lat, own.lon, end) if end else None
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
            slots["hold_short"] = self._crossing_side(geo, route.crossings[0], route.nodes)
            self._crossings = route.crossings
        elif route.hold_point and route.taxiways != (route.hold_point,):  # "runway 25L at Delta, taxi via Charlie, Delta"
            # (Only one taxiway, the one with the hold line: "runway 06L, taxi via A4", not "at A4, via A4".)
            instruction = "ground.taxi_out_at"
            slots["hold_point"] = route.hold_point
        graph = self._taxi_graph(geo)
        path = (geo.icao, [graph.positions[n] for n in route.nodes if n in graph.positions])
        self._schedule(t, instruction, slots, facility, clearance="taxi",
                       on_issue=lambda: (self._assign(departure_runway=end.ident, taxi_route=route.taxiways),
                                         setattr(self, "_taxi_path", path)), note=note)

    def _on_course_fix(self, own: OwnshipState | None) -> str | None:
        """Where departure sends a flight it has on runway heading (no SID: "fly runway heading" from the tower): the
        first fix of its route still ahead, or its destination when the route has none. None when the flight isn't
        on runway heading. Nobody turned the flight on course: it flew runway heading until the pilot asked."""
        if "tower.takeoff" not in self.state.issued or own is None:
            return None
        flight = self.state.flight
        for fix in self.route.fixes:
            ident = fix.ident.upper()
            if ident in ("TOC", "TOD") or ident in (flight.origin, flight.destination) or not ident.isalpha():
                continue
            if _distance_nm(own.lat, own.lon, fix.lat, fix.lon) >= ON_COURSE_MIN_NM:
                return fix.ident
        return self._airport_name(flight.destination) if flight.destination else None

    def _checkin(self, t: float, facility: Facility, own: OwnshipState | None) -> None:
        st = self.state
        self._checked_in.add(facility.station)
        if self._vfr and self._vfr_checkin(t, facility, own):
            return
        if facility.controller == "departure":
            cruise = st.assignments.cruise_ft or st.flight.cruise_ft
            assigned = st.assignments.altitude_ft
            if cruise and own is not None and assigned and own.alt_indicated_ft < assigned - DEPARTURE_REACHING_FT \
                    and min(cruise, DEPARTURE_TOP_FT) - assigned > 4000:  # (a low cruise just above: given at once)
                # Still climbing to the clearance's altitude: radar contact, and the next step as it gets near
                # (``_climb_due``), not the whole climb at once.
                if (fix := self._on_course_fix(own)) is not None:
                    self._schedule(t, "departure.radar_contact_only_direct", {"station": facility.station, "fix": fix},
                                   facility)
                else:
                    self._schedule(t, "departure.radar_contact_only", {"station": facility.station}, facility,
                                   expects_readback=False)
                return
            if cruise:
                # The next step up, short of the top of departure's airspace: the centres above take it from there.
                top = self._climb_step(facility, own, cruise)
                if (fix := self._on_course_fix(own)) is not None:
                    self._schedule(t, "departure.radar_contact_direct", {"station": facility.station, "altitude": top,
                                                                         "fix": fix}, facility,
                                   on_issue=lambda: self._assign(altitude_ft=top))
                else:
                    self._schedule(t, "departure.radar_contact", {"station": facility.station, "altitude": top}, facility,
                                   on_issue=lambda: self._assign(altitude_ft=top))
                return
        elif facility.controller == "center":
            # A handoff carries the flight with it: the next centre already has what it was assigned,
            # and says so, rather than starting the conversation again from nothing. Still climbing
            # below the filed level, though, the first thing a centre does is let it carry on up:
            # "maintain 5,000" to an aircraft passing 15,500 is a clearance nobody can fly.
            assigned = st.assignments.altitude_ft
            cruise = st.assignments.cruise_ft or st.flight.cruise_ft
            climbing_out = P(st.phase) in (P.DEPARTURE, P.CRUISE) and "descend" not in st.flags
            if own is not None and cruise and climbing_out and (assigned is None or assigned < cruise) \
                    and own.alt_indicated_ft >= (assigned or 0) - 1500:
                # Nearly up to what departure gave, or past it: the next step of the climb (never "descend" back
                # down to departure's altitude: a flight above it on the way up is taken on up from there).
                if own.alt_indicated_ft < cruise - 500:
                    step = self._climb_step(facility, own, cruise)
                    self._schedule(t, "center.radar_contact", {"station": facility.station, "altitude": step}, facility,
                                   on_issue=lambda: self._assign(altitude_ft=step))
                else:
                    self._schedule(t, "center.checkin_level", {"station": facility.station, "altitude": cruise}, facility,
                                   expects_readback=False, on_issue=lambda: self._assign(altitude_ft=cruise))
                return
            if assigned is not None and own is not None and own.alt_indicated_ft > assigned + 500:
                # Checking in on the way down: "continue descent", not "maintain" an altitude still far below.
                via = self._via_floor is not None and self.cfg.star
                if via:
                    self._schedule(t, "center.checkin_descend_via", {"station": facility.station,
                                                                     "procedure": self.cfg.star}, facility, expects_readback=False)
                elif own.vs_fpm < -300:  # on the way down already
                    self._schedule(t, "center.checkin_descending", {"station": facility.station, "altitude": assigned},
                                   facility, expects_readback=False)
                else:  # level above it: the descent, said again
                    self._schedule(t, "center.checkin_descend", {"station": facility.station, "altitude": assigned}, facility)
            elif assigned is not None and own is not None and own.alt_indicated_ft < assigned - 500 and own.vs_fpm > 300:
                self._schedule(t, "center.checkin_climbing", {"station": facility.station, "altitude": assigned},
                               facility, expects_readback=False)  # on the way up to it: carry on
            elif assigned is not None:
                self._schedule(t, "center.checkin_level", {"station": facility.station, "altitude": assigned},
                               facility, expects_readback=False)  # confirming what is already assigned
            else:
                self._schedule(t, "center.checkin", {"station": facility.station}, facility)
            return
        elif facility.controller == "approach" and "approach" not in st.clearances and own is not None:
            plan = self._arrival_plan(own)
            # Still on the STAR it was cleared down: above its floor, or below it and still descending on it (the
            # flight plan's floor isn't always the STAR's last restriction; a "descend and maintain" and a vector
            # then made no sense with the STAR's next fix right there).
            on_star = own.alt_indicated_ft > self._via_floor + 500 or own.vs_fpm < -300 \
                if self._via_floor is not None else False
            if plan is not None and self._via_floor is not None and self.cfg.star \
                    and on_star and not self._going_around:
                # Checking in on the STAR it was cleared to descend via: it carries on down it, and the descent isn't
                # said again ("how many times do I need to tell you"); nor is the approach, if the centre gave it.
                if st.assignments.approach == plan["approach"].display:
                    self._schedule(t, "approach.checkin_roger", {"station": facility.station}, facility,
                                   expects_readback=False,
                                   note=self._atis_note(st.flight.destination, st.assignments.arrival_atis)
                                   or self._altimeter_note(st.flight.destination))
                    return
                self._schedule(t, "approach.checkin_descend_via", {"station": facility.station, "procedure": self.cfg.star,
                                                                   "approach": plan["approach"]}, facility,
                               expects_readback=False,
                               on_issue=lambda: self._assign(approach=plan["approach"].display,
                                                             arrival_runway=plan["approach"].landing_runway),
                               note=self._atis_note(st.flight.destination, st.assignments.arrival_atis)
                               or self._altimeter_note(st.flight.destination))
                return
            if plan is not None:
                # Never a climb: already lower than the step, the next thousand down.
                step = min(self._step_altitude(own, plan), max(plan["approach_alt"], int(own.alt_indicated_ft // 1000 * 1000)))
                altitude, instruction = self._arrival_altitude(step, own, "approach")
                self._schedule(
                    t, instruction, {"station": facility.station, "altitude": altitude, "approach": plan["approach"]},
                    facility, on_issue=lambda: self._assign(altitude_ft=altitude, approach=plan["approach"].display,
                                                            arrival_runway=plan["approach"].landing_runway),
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
            on_issue=lambda: (self._assign(approach=plan["approach"].display, arrival_runway=plan["approach"].landing_runway),
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
            # Already cleared lower (descending via the arrival): still well above it, that's "descend and maintain".
            return assigned, f"{controller}.descend" if flying > assigned + 500 else f"{controller}.maintain"
        return planned, f"{controller}.maintain"

    def _descent_due(self, own: OwnshipState, *, lead_min: float = 0.0) -> bool:
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
        return geo.distance_nm(own.lat, own.lon) <= start_nm + own.gs_kt / 60 * max(lead_min, DESCENT_LEAD_MIN)

    def _clear_descent(self, t: float, own: OwnshipState, facility: Facility, plan: dict[str, Any], *, late: bool = False) -> None:
        """The descent from cruise, ahead of the top of descent: "descend via" the filed arrival when there is
        one, otherwise at pilot's discretion to the arrival altitude."""
        st = self.state
        st.flags.add("descend")
        approach = plan["approach"]

        def assigned(altitude: int, via: bool) -> Callable[[], None]:
            def apply() -> None:
                self._via_floor = altitude if via else None
                self._assign(altitude_ft=altitude, approach=approach.display, arrival_runway=approach.landing_runway)
            return apply

        if self.cfg.star:
            floor = self.route.arrival_floor_ft or plan["arrival_alt"]
            self._schedule(t, "center.descend_via", {"procedure": self.cfg.star, "approach": approach}, facility,
                           delay=False, on_issue=assigned(floor, True), note=self._altimeter_note(st.flight.destination))
            return
        altitude, instruction = self._arrival_altitude(plan["center_alt"], own, "center")
        if late:  # already on the way down, or a short hop still climbing: "descend and maintain", or "maintain"
            self._schedule(t, instruction, {"altitude": altitude, "approach": approach}, facility, delay=False,
                           on_issue=assigned(altitude, False), note=self._altimeter_note(st.flight.destination))
            return
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
                landing is None or (self._traffic_on_runway(own, landing) is None
                                    and self._traffic_ahead(own, landing) is None))):
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
        landing = self._vfr_landing() if self._vfr else "tower.land"
        self._schedule(t, landing, {"runway": runway, "wind": self._wind(own)}, facility, delay=delay, clearance="landing",
                       on_issue=lambda: self._assign(arrival_runway=runway), note=self._caution_note(st.flight.destination))

    def _gate_now_taken(self, own: OwnshipState) -> "stands.Gate | None":
        """The gate ground gave, with an aircraft parked on it now (it wasn't when it was given), while there's still
        taxiing to do to it."""
        a = self.state.assignments
        if a.gate_index is None or a.gate_index in self._gates_taken or "taxi_in" not in self.state.clearances:
            return None
        geo = self.geometry(self.state.flight.destination)
        if geo is None:
            return None
        gate = next((g for g in stands.gates(geo, self._real_gates(geo.icao)) if g.index == a.gate_index), None)
        if gate is None or math.dist(geo.xy(own.lat, own.lon), geo.xy(gate.spot.lat, gate.spot.lon)) < 60:
            return None  # there already (the aircraft in it is ours)
        return gate if stands.occupied(gate, geo, self._traffic.values()) else None

    def _taxi_in(self, t: float, facility: Facility, own: OwnshipState | None, *, requested: str | None = None,
                 note: Phrase | None = None, busy: "stands.Gate | None" = None) -> None:
        """The taxi to the gate. ``requested``: the gate the pilot asked for ("we'd like gate Echo 9"): theirs if the
        scenery has it; if it doesn't, parking, never a gate number made up in its place (Las Vegas's "Gate 88")."""
        ctx = self.tracker.context
        geo = ctx.airport
        a = self.state.assignments
        # Asked again, ATC repeats the route it gave, it doesn't invent a new one: the search starts from
        # where the aircraft is, so a few metres of rollout would otherwise pick different exits each time. Only
        # the route in: the one out was at the other airport (Zurich was given Montreal's "A, F, B4, B").
        taxiways = self._taxi_in_route if "taxi_in" in self.state.clearances and requested is None else None
        if geo is not None:
            geo = self._with_taxiway_names(geo)
        gate = None
        busy_note: Phrase | None = note
        lead: Phrase | None = None
        text = (self.state.exchanges[-1].text if self.state.exchanges and self.state.exchanges[-1].speaker == "pilot"
                else "").lower()
        taken_said = bool(re.search(r"\b(?:aircraft|airplane|plane|someone|somebody|occupied|taken)\b", text)) \
            and bool(re.search(r"\b(?:gate|stand)\b", text))
        if taken_said and a.gate_index is not None:
            self._gates_taken.add(a.gate_index)  # "there's an aircraft at our gate": not sent there again
            if busy is None and geo is not None:
                busy = next((g for g in stands.gates(geo, self._real_gates(geo.icao)) if g.index == a.gate_index), None)
        if requested is not None and geo is not None:
            gate = stands.named(geo, requested, self._real_gates(geo.icao))
            if gate is not None and (gate.index in self._gates_taken or stands.occupied(gate, geo, self._traffic.values())):
                busy_note = Phrase(f"{gate.display} is occupied", f"{gate.display} is occupied")
                self._gates_taken.add(gate.index)
                gate, taxiways = None, None
        if gate is None and (taxiways is None or taken_said):
            taxiways = None
            # A gate always: the one asked for if it's there and free, else the one already given (still free), else
            # one ATC picks.
            given = None
            if geo is not None and a.gate_index is not None and requested is None and a.gate_index not in self._gates_taken:
                given = next((g for g in stands.gates(geo, self._real_gates(geo.icao)) if g.index == a.gate_index), None)
                if given is not None and stands.occupied(given, geo, self._traffic.values()):
                    busy = given
                    self._gates_taken.add(given.index)
                    given = None
            # A gate given and now taken: the free one nearest to it (the same terminal, a short way further), with
            # an apology first. Gate 236 across the airfield, "Gate 403 is occupied" tacked on the end, sent the
            # flight back the way it came when 402 was free next door.
            gate = given or self._gate(geo, near=busy)
            if busy is not None:
                lead = Phrase(f"sorry, {busy.display} is occupied", f"sorry, {busy.display} is occupied")
        ramp = False  # to the GA ramp
        route = None
        if taxiways is None:
            graph = self._taxi_graph(geo) if geo is not None and own is not None else None
            route = graph.parking_route(own.lat, own.lon, gate.index) if graph is not None and gate is not None else None
            if route is None and graph is not None:
                # No gate (GA, or none free): the nearest parking; for GA the scenery's GA ramp, not an airliner gate.
                ga = not self._callsign().is_airline
                gate, route = None, graph.parking_route(own.lat, own.lon, kind=GA_PARKING if ga else "")
                ramp = ga and route is not None and route.nodes and route.nodes[-1][0] == "parking" and any(
                    p.index == route.nodes[-1][1] and p.kind.startswith(GA_PARKING) for p in geo.airport.parking)
            taxiways = route.taxiways if route is not None else None
            if route is not None and graph is not None:
                self._taxi_path = (geo.icao, [graph.positions[n] for n in route.nodes if n in graph.positions])
                # Runways on the way in (not the one just left): cleared across each as the aircraft reaches it, as
                # on the way out (Orlando's 18L, crossed with no word from ground).
                # The runway it's still on isn't one to cross: it's leaving it. The one it landed on, once off it,
                # is (Heathrow's 27L, vacated to the south, then crossed as 09R to the north side's stands).
                on = geo.runway_at(own.lat, own.lon, 30.0) if own is not None else None
                self._crossings = tuple(r for r in route.crossings if on is None or r != on.name)
                self._crossed = set()
            self._assign(gate=gate.display if gate else None, gate_index=gate.index if gate else None)
        where = a.gate

        def keep() -> None:
            self._taxi_in_route = taxiways
            self._assign(taxi_route=tuple(taxiways or ()))  # the route in, not still the one out (the copilot checks it)

        # On the way in across a runway: "hold short runway 09R" in the taxi clearance, crossed when it's reached.
        hold_short = self._crossing_side(geo, self._crossings[0], route.nodes if route is not None else ()) \
            if self._crossings and taxiways and geo is not None else None
        if where and taxiways and hold_short:
            self._schedule(t, "ground.taxi_to_gate_hold_short", {"taxi_route": taxiways, "gate": where, "hold_short": hold_short},
                           facility, clearance="taxi_in", on_issue=keep, note=busy_note, lead=lead)
        elif taxiways and hold_short and not where:
            self._schedule(t, "ground.taxi_in_ramp_hold_short" if ramp else "ground.taxi_in_hold_short",
                           {"taxi_route": taxiways, "hold_short": hold_short}, facility,
                           clearance="taxi_in", on_issue=keep, lead=lead)
        elif where and taxiways:
            self._schedule(t, "ground.taxi_to_gate", {"taxi_route": taxiways, "gate": where}, facility, clearance="taxi_in",
                           on_issue=keep, note=busy_note, lead=lead)
        elif where:  # a way there, but no names to give it by: the gate alone
            self._schedule(t, "ground.taxi_to_gate_no_route", {"gate": where}, facility, clearance="taxi_in", on_issue=keep,
                           note=busy_note, lead=lead)
        elif taxiways:
            self._schedule(t, "ground.taxi_in_ramp" if ramp else "ground.taxi_in", {"taxi_route": taxiways}, facility,
                           clearance="taxi_in", on_issue=keep)
        else:
            self._schedule(t, "ground.taxi_in_no_route", {}, facility, clearance="taxi_in")

    def _gate(self, geo: AirportGeometry | None, near: "stands.Gate | None" = None) -> "stands.Gate | None":
        """A free gate at the destination for an airline flight. GA is sent to parking, not a numbered stand:
        "taxi to parking" is what a GA pilot hears, and the nearest ramp is where they go."""
        callsign = self._callsign()
        if geo is None or not callsign.is_airline:
            return None
        flight = self.state.flight
        return stands.assign(geo, airline=callsign.is_airline, aircraft_type=flight.aircraft_type or "",
                             traffic=self._traffic.values(), seed=f"{callsign.ident}{self.cfg.seed}",
                             exclude=self._gates_taken, near=(near.spot.lat, near.spot.lon) if near is not None else None,
                             real=self._real_gates(geo.icao),
                             international=real_gates.is_international(flight.origin, flight.destination))

    def _with_taxiway_names(self, geo: AirportGeometry) -> AirportGeometry:
        """The airport with names for its taxiways where the scenery has none (Zurich's: "taxi to the apron" was all
        ATC could say), from the real taxiways (``gate_source``). Once an airport; the same geometry otherwise."""
        icao = geo.icao
        if icao in self._taxiways_named:
            return self.tracker.context_builder.airports.get(icao, geo)
        data = self._real_gates(icao)
        if data is None:
            return geo  # not fetched (yet): asked again next time
        self._taxiways_named.add(icao)
        renamed = real_gates.name_taxiways(geo.airport, data, geo.xy)
        if renamed is None:
            return geo
        named = AirportGeometry(renamed)
        named.hold_taxiways = geo.hold_taxiways
        self.tracker.context_builder.airports[icao] = named
        logging.getLogger(__name__).info("Taxiway names for %s from OpenStreetMap: %d paths named", icao,
                                         sum(1 for p in renamed.taxi_paths if p.name))
        return named

    def _real_gates(self, icao: str | None) -> "real_gates.GateData | None":
        """The airport's real gates (``gate_source``: the app's OpenStreetMap cache), or None: the scenery's names."""
        if not icao or self.gate_source is None:
            return None
        try:
            return self.gate_source(icao)
        except Exception:  # noqa: BLE001 - a broken cache never stops ATC
            logging.getLogger(__name__).exception("real gates for %s", icao)
            return None

    def _handoff(self, t: float, instruction_id: str, from_facility: Facility, to_facility: Facility, *, delay: bool = False) -> None:
        self._schedule(t, instruction_id, {"station": to_facility.station, "frequency": to_facility.mhz}, from_facility,
                       delay=delay, handoff_to=to_facility)

    def _approach_for(self, runway: str, *, has_ils: bool, requested: str | None = None) -> Approach:
        """What to expect for a runway: what the destination publishes and has working (its ATIS's notices), what
        the weather allows, and what this aircraft can fly (``[flight] approach`` forces one). Without a request,
        the one the destination's ATIS advertises for that runway: ATIS and ATC say the same thing."""
        st = self.state
        dest = st.flight.destination
        geo = self.geometry(dest)
        info = self.current_atis(dest)
        airline = self._callsign().is_airline
        own = st.aircraft
        # In cloud counts near the destination and low, not up in the cruise over somebody else's weather.
        low = own is not None and geo is not None and geo.distance_nm(own.lat, own.lon) <= 30 \
            and own.alt_indicated_ft - geo.airport.elev_ft <= 5000
        in_cloud = bool(own.in_cloud and low) if own is not None else False
        advertised = info.approach_for(runway) if info is not None else None
        if requested is None and self.cfg.approach == "auto" and advertised is not None and (
                (advertised.kind == "VISUAL" and not in_cloud) or advertised.circling or advertised.kind in {
                    o.approach.kind for o in approach_options(geo.airport if geo is not None else None, runway,
                                                              Aircraft(st.flight.aircraft_type, airline), info.outages)}):
            # The one the ATIS advertises, if this aircraft can fly it (an RNP one isn't for a Cessna).
            return advertised
        weather = self.weather.surface(dest or "", self.tracker.context_builder.airports)
        return choose_approach(
            geo.airport if geo is not None else None, runway, has_ils=has_ils,
            visibility_sm=weather.visibility_sm if weather is not None else None,
            in_cloud=in_cloud,
            ceiling_ft=weather.ceiling_ft if weather is not None else None,
            aircraft_type=st.flight.aircraft_type, airline=airline, requested=requested,
            override=self.cfg.approach, visual_first=not regions.region_for(dest).icao,
            precip=weather.precip if weather is not None else "",
            outages=info.outages if info is not None else Outages(),
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
        given = Approach.parse(str(self.state.assignments.approach or ""))
        # The approach to expect, once given, is the one flown (unless the pilot asks for another): the weather
        # getting known on the way in doesn't turn "expect the ILS" into a visual nobody mentioned.
        if given is not None and given.kind != "VISUAL" and given.runway != end.ident and assigned == end.ident:
            given = circle_to(geo.airport, given, end.ident)  # "VOR RWY 16" given, landing on 34: the circle
        kept = given if given is not None and given.landing_runway == end.ident else None
        approach = self._approach_kind or kept or self._approach_for(end.ident, has_ils=end.has_ils)
        return {
            "approach": approach,
            "final": approach.runway or approach.landing_runway,  # the final the vectors lead onto
            "arrival_alt": int(min(cruise, max(3000, math.ceil((elev + 2500) / 1000) * 1000))),
            # Where centre takes an arrival down to before approach has it: about 10,000 ft above the field for a
            # jet up in the flight levels (Seattle: 11,000), the arrival altitude on a short, low flight.
            "center_alt": int(max(3000, math.ceil((elev + 10000) / 1000) * 1000)) if cruise >= 18000
            else int(min(cruise, max(3000, math.ceil((elev + 2500) / 1000) * 1000))),
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
        worded_by: str = "template",
        lead: Phrase | None = None,
    ) -> None:
        due = t + (self._random().uniform(*self.cfg.response_delay_s) if delay else 0.0)
        if self._scheduled:
            # Replies decided together go out in the order they were decided: "equipment standing by" to the
            # problem the pilot reported, then the answer to the rest of the call, never the other way round.
            due = max(due, max(item.due for item in self._scheduled))
        self._scheduled.append(_Scheduled(due, instruction_id, slots, facility, clearance, handoff_to, expects_readback, on_issue,
                                          note, self._answering, worded_by, lead=lead))

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
        paused = self.tracker.paused
        own = self.state.aircraft
        due = sorted((s for s in self._scheduled if s.due <= t and (s.reply or not paused)), key=lambda s: s.due)
        for item in list(due):
            if (heard := self._reach(item.facility, own)) is not None and not heard.in_range:
                due.remove(item)  # the pilot wouldn't hear it: not said (anything still due is said when it's in range)
                self._scheduled.remove(item)
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
        rendered = self.library.render(
            item.instruction_id, slots, controller=facility.controller,
            choose=lambda n: personality.wording(facility.station, item.instruction_id, n, self._voice_rng))
        if item.worded is not None:
            # The model's words for it (checked to say the same). What's read back is still the template's.
            rendered = replace(rendered, text=f"{speech.callsign_display(callsign)}, {item.worded.display}.",
                               spoken=f"{speech.callsign(callsign)}, {item.worded.spoken}.")
        if item.note is not None:
            rendered = replace(rendered, text=f"{rendered.text.rstrip('.')}, {item.note.display}.",
                               spoken=f"{rendered.spoken.rstrip('.')}, {item.note.spoken}.")
        if item.lead is not None:
            head, spoken_head = speech.callsign_display(callsign) + ", ", speech.callsign(callsign) + ", "
            if rendered.text.startswith(head) and rendered.spoken.startswith(spoken_head):
                rendered = replace(rendered, text=head + item.lead.display + ", " + rendered.text[len(head):],
                                   spoken=spoken_head + item.lead.spoken + ", " + rendered.spoken[len(spoken_head):])
        rendered = self._personalize(rendered, item, facility, callsign)
        self._said_on.append((t, facility.station))
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
        elif item.clearance is not None and self._last_handoff is not None and self._last_handoff[1] == facility \
                and st.comms.expected == self._last_handoff[2]:
            # The controller who sent the flight on gave it a clearance after all (ground's taxi, after a "contact
            # tower" too soon): it's still theirs. Otherwise every call after it got "contact tower" again.
            st.comms.expected, self._last_handoff = None, None
        if item.on_issue is not None:
            item.on_issue()
        return AtcTransmission(
            t=t, station=facility.station, frequency_mhz=facility.mhz, text=rendered.text, controller=facility.controller,
            instruction_id=item.instruction_id, spoken=rendered.spoken,
            worded_by="model" if item.worded_by == "model" else "template",
            manner=self._manner(facility), locale=self._locale(facility),
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

    def current_center(self) -> Facility:
        """The enroute centre working the flight now, or the one it will be handed to first."""
        return self._center()

    def _center(self) -> Facility:
        if self._sector is not None:
            return self._sector
        return next(f for f in self.facilities if f.controller == "center") if self.facilities else center_facility(
            [], self.cfg.center_name, self.cfg.center_mhz
        )

    @staticmethod
    def _crossing_side(geo: AirportGeometry, runway: str, nodes) -> str:
        """The name a runway to cross goes by where the route reaches it: the end its holding point is nearest
        ("hold short runway 01L" on the 01L side; "19R" there confused the pilot). The first end without it."""
        on_route = {n[1] for n in nodes if n[0] == "point"}
        hold = next((h for h in geo.hold_shorts if h.runway.name == runway and h.point.index in on_route), None)
        return hold.end.ident if hold is not None else runway.split("/")[0]

    def _crossing_due(self, own: OwnshipState) -> str | None:
        """A runway the taxi route goes across, with the aircraft nearly at it.

        Being routed over a runway and never cleared across it leaves the pilot either stopping for a
        clearance that never comes or being blamed for crossing. The clearance comes as they reach it.
        """
        ctx = self.tracker.context
        if not self._crossings or ctx.hold_short is None or ctx.hold_short_distance_m is None:
            return None
        if ctx.hold_short_distance_m > CROSSING_CLEARANCE_M or not ({"taxi", "taxi_in"} & set(self.state.clearances)):
            return None
        runway = ctx.hold_short.runway.name
        if runway not in self._crossings or runway in self._crossed:
            return None
        # Closing on it, not taxiing away from it: just off the runway landed on, the same runway further along the
        # way in is to be crossed later (Heathrow's 09R, cleared across at the 27L exit, three minutes early).
        seen = self._crossing_seen.get(runway)
        now = (own.t, ctx.hold_short_distance_m)
        if seen is None or now[0] - seen[0] > CROSSING_TREND_S * 4:
            self._crossing_seen[runway] = now
            return None
        if now[0] - seen[0] < CROSSING_TREND_S:
            return None
        self._crossing_seen[runway] = now
        return runway if seen[1] - now[1] >= CROSSING_CLOSING_M else None

    def _climb_step(self, facility: Facility, own: OwnshipState | None, cruise: int) -> int:
        """The next altitude of the climb. Departure's is the top of its airspace; a centre's is its usual step
        (each has its own, FL230 to FL280) when the cruise is well above it, else the cruise itself."""
        if facility.controller == "departure":
            top = min(cruise, DEPARTURE_TOP_FT)
            assigned = self.state.assignments.altitude_ft or 0
            here = max(assigned, own.alt_indicated_ft if own is not None else 0.0)
            pick = zlib.crc32(f"{facility.station}{self._callsign().ident}{assigned}".encode()) % len(DEPARTURE_STEPS_FT)
            step = int(math.ceil((here + DEPARTURE_STEPS_FT[pick]) / 1000.0)) * 1000
            return top if top - step < 4000 else step  # the last bit of the way in one go
        step = CENTER_STEPS[zlib.crc32(facility.station.encode()) % len(CENTER_STEPS)]
        here = own.alt_indicated_ft if own is not None else 0.0
        assigned = self.state.assignments.altitude_ft or 0
        if cruise - step >= CENTER_STEP_GAP_FT and step > max(here, assigned) + 2000:
            return step
        return cruise

    def _climb_due(self, own: OwnshipState) -> int | None:
        """Reaching the step a centre gave (or level at it for a while): the next one, up to the cruise."""
        st = self.state
        tuned = st.comms.tuned
        cruise = st.assignments.cruise_ft or st.flight.cruise_ft
        assigned = st.assignments.altitude_ft
        if tuned is None or tuned.controller not in ("center", "departure") or not cruise or assigned is None \
                or assigned >= cruise or (tuned.controller == "departure" and assigned >= min(cruise, DEPARTURE_TOP_FT)):
            return None
        if tuned.controller == "departure":
            if "descend" in st.flags or "emergency" in st.flags or own.vs_fpm < 300:
                return None  # only a flight on its way up gets the next step
            if own.alt_agl_ft >= DEPARTURE_ENDS_FT - 3000:
                return None  # nearly out of departure's airspace: the centre gives the next one
            if own.alt_indicated_ft < assigned - DEPARTURE_REACHING_FT or self.state.pending is not None:
                return None
            step = self._climb_step(tuned, own, cruise)  # a departure keeps a climb going: no levelling off first
            return step if step > assigned else None
        if "descend" in st.flags or "emergency" in st.flags or own.alt_indicated_ft < assigned - CLIMB_REACHING_FT:
            self._reaching_t = None
            return None
        if self._reaching_t is None:
            self._reaching_t = own.t
        wait = CLIMB_WAIT_S[0] + (zlib.crc32(tuned.station.encode()) % 100) / 100 * (CLIMB_WAIT_S[1] - CLIMB_WAIT_S[0])
        if own.t - self._reaching_t < wait:
            return None
        step = self._climb_step(tuned, own, cruise)
        return step if step > assigned else None

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
        # Climbing to departure's top altitude with more to come: over to centre before the level-off, so the
        # climb goes on without a stop at 17,000 waiting for the handoff.
        assigned = self.state.assignments.altitude_ft
        cruise = self.state.assignments.cruise_ft or self.state.flight.cruise_ft
        near_top = assigned is not None and cruise is not None and cruise > assigned + 1000 \
            and assigned >= min(cruise, DEPARTURE_TOP_FT) and own.alt_indicated_ft >= assigned - 2500 and own.vs_fpm > 300
        # High enough that departure leaves the next step to the centre (``_climb_due``), with more climb to come:
        # over to the centre now. A flight given 15,000 sat level there for four minutes, departure waiting for it
        # to reach 15,000 above the ground and the centre for the handoff.
        given_up = assigned is not None and cruise is not None and cruise > assigned + 1000 \
            and own.alt_agl_ft >= DEPARTURE_ENDS_FT - 3000 and own.alt_indicated_ft >= assigned - 2500
        return above or away or near_top or given_up

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

    def _destination_approach(self) -> Facility | None:
        """The destination's approach controller; None where it has none (the centre works the approach then)."""
        approach = self.facility("approach")
        if approach is None or approach.airport not in (None, self.state.flight.destination):
            return None  # another airport's (the departure's): not the one for this arrival
        return approach

    def _center_works_approach(self, own: OwnshipState) -> bool:
        """The centre is working the arrival as approach would: the destination has no approach controller, and the
        flight is down into its area. Joplin has none: Kansas City Center gave one heading and nothing more (no turn
        onto the final, no approach clearance, no "contact tower"), then "check heading" as the pilot joined the
        final by themselves."""
        st, tuned = self.state, self.state.comms.tuned
        if tuned is None or tuned.controller != "center" or st.phase is None \
                or P(st.phase) not in (P.ARRIVAL, P.APPROACH, P.LANDING):
            return False
        return self._destination_approach() is None and "descend" in st.flags and self._arriving_in_area(own)

    def _works_approach(self, own: OwnshipState | None) -> bool:
        """The tuned controller does approach's work: approach itself, or a centre standing in for one."""
        tuned = self.state.comms.tuned
        return tuned is not None and (tuned.controller == "approach"
                                      or own is not None and self._center_works_approach(own))

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
        flight stays with the centre it is on. Never on the frequency of the centre the flight is talking to:
        Denver Center handing over to Los Angeles Center on its own 135.45 left the pilot "contacting" LA on the
        same frequency, and Denver answering."""
        here = self._sector_candidate_on(own, tuple(f.mhz for f in self.facilities if f.controller != "center"))
        now = self._sector
        if here is not None and now is not None and here.station != now.station and \
                channel_khz(here.mhz) == channel_khz(now.mhz):
            here = self._sector_candidate_on(own, (*(f.mhz for f in self.facilities if f.controller != "center"), now.mhz))
        return here

    def _sector_candidate_on(self, own: OwnshipState, taken: tuple[float, ...]) -> Facility | None:
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
            dwell = SECTOR_DWELL_S
            left = self._sector_left
            if left is not None and left[0] == candidate.station and own.t - left[1] < SECTOR_BACK_S:
                # Back into the centre just left: a route along the boundary (Shannon, Scottish, Shannon, Scottish
                # in eight minutes). Handed back only once the flight has stayed in there a good while.
                dwell = SECTOR_BACK_DWELL_S
            return candidate if own.t - self._sector_next[1] >= dwell else None
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
        expected, current = self.state.comms.expected, self.state.comms.tuned
        if expected is not None and expected.matches(mhz) and expected not in self.facilities:
            # The centre the pilot was just sent to, on a frequency an airport far away also uses (Gander Oceanic's
            # 120.4 is Heathrow Director's too): that centre, not the airport 1,900 miles off.
            return expected
        own = self.state.aircraft
        if current is not None and current.controller == "center" and current.matches(mhz) and current not in self.facilities \
                and own is not None and not own.on_ground:
            return current  # and still that centre until the next handoff, not the airport's when the handoff's done
        matches = [f for f in self.facilities if f.matches(mhz)]
        if not matches:
            if expected is not None and expected.matches(mhz):
                # The frequency ATC just sent the pilot to: that controller's, even a moment before the flight is
                # in its airspace (Chicago Center, handed off from Cleveland just short of the boundary).
                return expected
            if self._last_handoff is not None and self._last_handoff[1].matches(mhz) \
                    and (self.state.pending is not None or self._t - self._last_handoff[0] <= HANDOFF_LISTENING_S):
                # Still on the old centre's frequency after its handoff (the next sector is already the flight's):
                # the old controller is still there to hear the readback, and answers it.
                return self._last_handoff[1]
            return self._center_here(mhz)
        if len(matches) == 1:
            return matches[0]
        expected = self.state.comms.expected
        if expected is not None and expected in matches:
            handed_from = self._last_handoff[1] if self._last_handoff is not None else None
            if handed_from in matches and channel_khz(mhz) != channel_khz(expected.mhz):
                return handed_from  # a frequency both work, and not the one ATC sent the pilot to: not switched yet
            return expected
        # Parked again after landing is at the destination: its frequencies, not the departure airport's (San Diego's
        # 123.9 was "Seattle Departure" once parked at the gate).
        arriving = self._landed or (self.state.phase is not None and P(self.state.phase) not in DEPARTURE_PHASES)
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

    # --- the controller as a person (personality.py) ----------------------------------------------------------

    def _controller(self, facility: Facility) -> "personality.Personality":
        who = personality.profile(facility.station, facility.controller, icao=self.region.icao, shift=self.cfg.shift)
        if facility.station not in self._met:
            self._met.add(facility.station)
            logging.getLogger(__name__).info("ATC: the controller at %s is %s (%s)", facility.station, who.kind,
                                             who.traits.manner)
        return who

    def _workload(self, facility: Facility, t: float | None = None) -> str:
        """How busy this frequency is: what's been said on it in the last five minutes, and the traffic around."""
        now = t if t is not None else (self.state.aircraft.t if self.state.aircraft is not None else 0.0)
        self._said_on = [x for x in self._said_on if now - x[0] <= 300]
        said = sum(1 for _, station in self._said_on if station == facility.station)
        own = self.state.aircraft
        near = sum(1 for x in self._traffic.values()
                   if own is not None and _distance_nm(own.lat, own.lon, x.lat, x.lon) <= 15) if own is not None else 0
        return personality.workload(said, near)

    def _persona(self, facility: Facility | None) -> str:
        """The controller's manner, for the language model's wording ([atc] personalities)."""
        if facility is None or not self.cfg.personalities:
            return ""
        return self._controller(facility).describe(self._workload(facility))

    def _locale(self, facility: Facility) -> str:
        """The English of the controller's region, for the voice: its airport's, or for a centre the FIR the aircraft
        is in."""
        code = facility.airport
        own = self.state.aircraft
        if code is None and own is not None and (area := self.center_area(own)) is not None:
            code = area.id
        return regions.accent(code or self.state.flight.origin or self.state.flight.destination)

    def _manner(self, facility: Facility) -> str:
        """For the voice: the role, the controller's kind and a busy frequency ("tower:hurried:busy")."""
        if not self.cfg.personalities:
            return facility.controller
        busy = self._workload(facility) == "busy"
        return self._controller(facility).manner + (":busy" if busy else "")

    def _personalize(self, rendered: Any, item: _Scheduled, facility: Facility, callsign: Callsign) -> Any:
        """The controller's own way of saying it (personality.py): a greeting on its first real call to the flight, a
        sign-off on a handoff, their acknowledgement and "say again", a correction firmer for the same mistake again.
        Only around the instruction: the words that are read back never change."""
        who = self._controller(facility) if self.cfg.personalities else personality.profile(facility.station, icao=self.region.icao)
        traits = who.traits
        rng = self._voice_rng
        iid = item.instruction_id
        own = self.state.aircraft
        busy = self.cfg.personalities and self._workload(facility) == "busy"
        shown_cs, said_cs = speech.callsign_display(callsign), speech.callsign(callsign)

        def both(fn, *args) -> Any:
            return replace(rendered, text=fn(rendered.text, shown_cs, *args), spoken=fn(rendered.spoken, said_cs, *args))

        if self.cfg.personalities:
            mine = self._persona_rng
            if iid == "common.roger" and item.worded_by != "model":
                ack = traits.acks[0] if busy else who.ack if mine.random() > 0.2 else mine.choice(traits.acks)
                return both(personality.acknowledge, ack)
            if iid == "common.say_again":
                return both(personality.say_again, traits.say_again[0] if busy else mine.choice(traits.say_again))
            if iid in ("common.negative", "common.read_back"):
                pending = self.state.pending
                tries = pending.attempts if pending is not None else 0
                if traits.correction == "firm" or tries >= traits.patience:
                    if tries >= 1:
                        return both(personality.firmer)
                elif traits.correction == "gentle" and tries == 0 and iid == "common.negative" and not busy:
                    return both(personality.gentler)
                return rendered
        if item.handoff_to is not None:
            self._greeted.add(facility.station)
            # Going around: the flight is back with this tower in a few minutes, no "have a good flight".
            chance = who.signs_off * (0.5 if busy else 1.0)
            if "go_around" not in iid and rng.random() < chance:
                evening = own is not None and personality.part_of_day(own.zulu_s, own.lon) == "evening"
                words = "good night" if evening and rng.random() < 0.5 else who.sign_off
                if self.cfg.personalities and self._persona_rng.random() < 0.25:
                    offs = traits.icao_sign_offs if who.icao else traits.sign_offs
                    words = self._persona_rng.choice(offs)
                return replace(rendered, text=personality.sign_off(rendered.text, words),
                               spoken=personality.sign_off(rendered.spoken, words))
            return rendered
        # Only the first word a controller has with the flight can be a greeting, whatever that word was:
        # tower that held the aircraft short first doesn't say "good afternoon" with the takeoff clearance.
        first = facility.station not in self._greeted
        self._greeted.add(facility.station)
        if not first or any(word in iid for word in NO_GREETING):
            return rendered
        when = personality.part_of_day(own.zulu_s, own.lon) if own is not None else None
        if when is None or rng.random() >= who.greets * (0.3 if busy else 1.0):
            return rendered
        pattern = self._persona_rng.choice(traits.greetings) if self.cfg.personalities else "good {when}"
        words = pattern.format(when=when)  # "good evening", the dry controller's "evening"
        return replace(rendered,
                       text=personality.greet(rendered.text, shown_cs, facility.station, words),
                       spoken=personality.greet(rendered.spoken, said_cs, facility.station, words))

    def _random(self) -> random.Random:
        if self._rng is None:
            seed = self.cfg.seed or zlib.crc32(self._callsign().ident.encode())
            self._rng = random.Random(seed)
        return self._rng

    def _squawk(self) -> str:
        """The flight's code: the same for the whole flight, a different one each flight. It came from the callsign
        alone, so a pilot always flying as the same callsign got the same code every flight. Now the route and the
        sim's time of day when the code is first given go in too (a replay gets the code it got live)."""
        if self._squawk_code is not None:
            return self._squawk_code
        own, flight = self.state.aircraft, self.state.flight
        minute = int(own.zulu_s // 60) if own is not None and own.zulu_s is not None else 0
        key = f"squawk{self._callsign().ident}{flight.origin}{flight.destination}{minute}" if self.cfg.squawk_per_flight \
            else "squawk" + self._callsign().ident
        rng = random.Random(self.cfg.seed or zlib.crc32(key.encode()))
        while True:
            code = str(rng.randint(1, 6)) + "".join(rng.choice("01234567") for _ in range(3))
            if code not in RESERVED_SQUAWKS:
                self._squawk_code = code
                return code

    def _assign(self, **values: Any) -> None:
        for key, value in values.items():
            if key == "heading" and value is not None:
                self._heading_given = (self._t, value, 0)  # (when, heading, times checked)
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


def _east_north(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Metres east and north from the first point to the second (flat, for short distances)."""
    north = math.radians(lat2 - lat1) * 6371000.0
    east = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)) * 6371000.0
    return east, north


def _velocity(heading_true: float, kt: float) -> tuple[float, float]:
    """Metres per second east and north."""
    ms = kt * NM_M / 3600.0
    return ms * math.sin(math.radians(heading_true)), ms * math.cos(math.radians(heading_true))


def _along_path(path: list[tuple[float, float]], here: tuple[float, float], there: tuple[float, float],
                width_m: float = STOPPED_AHEAD_WIDTH_M, on_route_m: float = 60.0) -> float | None:
    """How far along ``path`` from ``here`` the point ``there`` lies, if it's on the path (within ``width_m``) ahead;
    -1 if it's on the path behind or not on it at all; None when ``here`` isn't on the path (the pilot went another way)."""
    def project(p: tuple[float, float]) -> tuple[float, float, float]:
        """(distance along the path, distance off it, ...) of the nearest point."""
        best = (math.inf, 0.0, 0.0)
        run = 0.0
        for (ax, ay), (bx, by) in itertools.pairwise(path):
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy)
            u = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (length * length)))
            off = math.dist(p, (ax + u * dx, ay + u * dy))
            if off < best[0]:
                best = (off, run + u * length, 0.0)
            run += length
        return best[1], best[0], 0.0

    own_along, own_off, _ = project(here)
    if own_off > on_route_m:
        return None
    their_along, their_off, _ = project(there)
    if their_off > width_m:
        return -1.0
    return their_along - own_along


def _display(value: Any) -> str:
    if isinstance(value, float):
        return speech.frequency_display(value)
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, Approach):
        return value.display
    return str(value)


GENERIC_AIRPORT_WORDS = {"international", "intl", "airport", "regional", "municipal", "field", "county", "metropolitan",
                         "national", "executive", "airfield", "aerodrome", "air", "base", "memorial"}


def _place(name: str) -> str:
    """An airport's name as said before "altimeter": "Los Angeles", not "Los" (the first word) nor the whole
    "Los Angeles International"."""
    words = name.split()
    while len(words) > 1 and words[-1].lower().strip(".,") in GENERIC_AIRPORT_WORDS:
        words.pop()
    return " ".join(words[:3])


def _runways_cross(a, b) -> bool:
    """Two runways' centrelines cross (San Francisco's 28s and 01s; parallels never do)."""
    if a.name == b.name:
        return False
    (ax, ay), (bx, by) = a.ends[0].threshold, a.ends[1].threshold
    (cx, cy), (dx, dy) = b.ends[0].threshold, b.ends[1].threshold

    def side(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)

    return side(ax, ay, bx, by, cx, cy) * side(ax, ay, bx, by, dx, dy) < 0 \
        and side(cx, cy, dx, dy, ax, ay) * side(cx, cy, dx, dy, bx, by) < 0


def _differs(sim, real) -> bool:
    """The real-world report and the sim's own observation disagree enough to matter: the wind by 60 degrees (at 10 kt
    or more) or 10 kt, or the altimeter by 0.03 inHg. ``real`` a Weather or WeatherReport, or None."""
    if real is None or sim is None:
        return False
    if hasattr(real, "wind_kt"):  # a WeatherReport
        r_dir, r_kt, r_alt = real.wind_dir_true, real.wind_kt, real.altimeter_inhg
    else:
        r_dir, r_kt, r_alt = real.wind_dir_true, real.wind.speed_kt, real.altimeter_inhg
    if abs(sim.wind.speed_kt - r_kt) >= 10:
        return True
    if r_dir is not None and max(sim.wind.speed_kt, r_kt) >= 10 and abs(((sim.wind_dir_true - r_dir) + 180) % 360 - 180) >= 60:
        return True
    return sim.altimeter_inhg is not None and r_alt is not None and abs(sim.altimeter_inhg - r_alt) >= 0.03
