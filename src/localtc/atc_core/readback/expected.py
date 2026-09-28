"""What a pilot can sensibly call about, by who they're talking to and the phase of flight.

One table serves two jobs: a call the grammar matched to an intent outside this set isn't taken on the
grammar's word alone (the language model has a look, ``llm.triggers``), and the model itself may only
answer with an intent from the set (the enum in its answer's schema), so "cleared ILS 34R" on approach
can't come back as a request for an IFR clearance.
"""

from typing import Final

ANY_ROLE: Final = frozenset({"clearance", "ground", "tower", "departure", "approach", "center"})
GROUND_PHASES: Final = frozenset({"PARKED", "PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD"})
AIR_PHASES: Final = frozenset({"TAKEOFF", "DEPARTURE", "CRUISE", "ARRIVAL", "APPROACH", "LANDING"})
ARRIVED: Final = frozenset({"LANDING", "TAXI_IN"})
ALL_PHASES: Final = GROUND_PHASES | AIR_PHASES | {"TAXI_IN"}
RADAR: Final = frozenset({"departure", "approach", "center"})

# Anywhere, to anyone: talk that isn't part of the flow.
ALWAYS: Final = ("acknowledge", "say_again", "question", "emergency", "radio_check", "pleasantry", "report_problem",
                 "need_time", "other")

# intent -> (roles it's said to, phases it's said in)
EXPECTED: Final[dict[str, tuple[frozenset[str], frozenset[str]]]] = {
    "request_ifr_clearance": (frozenset({"clearance", "ground"}), frozenset({"PARKED", "PUSHBACK", "TAXI_OUT"})),
    "request_pushback": (frozenset({"ground", "clearance"}), frozenset({"PARKED", "PUSHBACK"})),
    "ready_to_taxi": (frozenset({"ground", "clearance", "tower"}), frozenset({"PARKED", "PUSHBACK", "TAXI_OUT"})),
    "request_crossing": (frozenset({"ground", "tower"}), frozenset({"TAXI_OUT", "RUNWAY_HOLD", "TAXI_IN", "LANDING"})),
    "ready_for_departure": (frozenset({"tower", "ground"}), GROUND_PHASES),
    "request_turn": (frozenset({"tower", "ground"}), GROUND_PHASES),
    "request_flight_following": (ANY_ROLE, ALL_PHASES - ARRIVED),
    "request_class_b": (ANY_ROLE, ALL_PHASES - ARRIVED),
    "checkin": (RADAR | {"tower"}, AIR_PHASES),
    "request_altitude": (RADAR, AIR_PHASES - {"LANDING"}),
    "request_direct": (RADAR, frozenset({"DEPARTURE", "CRUISE", "ARRIVAL", "APPROACH"})),
    "request_vectors": (RADAR, frozenset({"DEPARTURE", "CRUISE", "ARRIVAL", "APPROACH"})),
    "request_runway": (ANY_ROLE, frozenset({"PARKED", "PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "CRUISE", "ARRIVAL",
                                            "APPROACH", "LANDING"})),
    "request_return": (RADAR | {"tower"}, AIR_PHASES),
    "request_diversion": (RADAR | {"tower"}, AIR_PHASES),
    "report_conditions": (RADAR | {"tower"}, AIR_PHASES),
    "traffic_report": (RADAR | {"tower"}, AIR_PHASES),
    "report_standard": (RADAR, AIR_PHASES),
    "report_final": (frozenset({"tower", "approach"}), frozenset({"ARRIVAL", "APPROACH", "LANDING"})),
    "position_report": (frozenset({"tower"}), AIR_PHASES),
    "request_option": (frozenset({"tower", "approach"}), frozenset({"APPROACH", "LANDING", "DEPARTURE"})),
    "going_around": (frozenset({"tower", "approach"}), frozenset({"TAKEOFF", "DEPARTURE", "APPROACH", "LANDING"})),
    "clear_of_runway": (frozenset({"tower", "ground"}), ARRIVED),
    "request_taxi_parking": (frozenset({"tower", "ground"}), ARRIVED),
}


def expected_intents(role: str | None, phase: str | None) -> tuple[str, ...]:
    """The intents that make sense to ``role`` (tower, center ...) in ``phase``. Unknown role or phase: every
    intent, since nothing can be ruled out."""
    fitting = [intent for intent, (roles, phases) in EXPECTED.items()
               if (role is None or role in roles) and (phase is None or phase in phases)]
    return (*fitting, *ALWAYS)


def is_expected(intent: str | None, role: str | None, phase: str | None) -> bool:
    return intent is None or intent in ALWAYS or intent in expected_intents(role, phase)
