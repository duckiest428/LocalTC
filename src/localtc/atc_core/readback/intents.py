"""Pilot-initiated requests and reports, matched by keyword phrases plus slot extraction."""

from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core.readback.extract import (
    _find_phrase,
    _has_any,
    _number,
    _runway_at,
    altitudes,
    hold_short,
    runways,
)
from localtc.atc_core.readback.normalize import Token

EMERGENCY = "emergency"


@dataclass(frozen=True)
class IntentMatch:
    intent: str
    values: dict[str, Any] = field(default_factory=dict)


def _atis(tokens: list[Token]) -> str | None:
    for phrase in (("information",), ("atis",), ("info",)):
        for i in _find_phrase(tokens, phrase):
            if i < len(tokens) and (tokens[i].kind == "letter" or (tokens[i].kind == "word" and len(tokens[i].text) == 1)):
                return tokens[i].text.upper()
    return None


def _reported_altitudes(tokens: list[Token]) -> list[int]:
    """Altitudes in check-ins: "2,000 climbing 5,000", "level five thousand", "with you at 3000"."""
    found = []
    for i, token in enumerate(tokens):
        if token.kind == "number" and token.text.isdigit() and 500 <= int(token.text) <= 60000 and int(token.text) % 100 == 0:
            found.append(int(token.text))
        if token.text == "level" and i + 1 < len(tokens) and (num := _number(tokens, i + 1)) and num.isdigit():
            value = int(num) * 100 if int(num) < 1000 else int(num)  # "level 120" is flight level 120
            if value not in found:
                found.append(value)
    return found


def _any_runway(tokens: list[Token]) -> str | None:
    """A runway mentioned in a report ("holding short runway 34L", "clear of 14R", "final runway 14R");
    after "correction", the corrected one."""
    for i in reversed(range(len(tokens))):
        if tokens[i].text == "correction":
            if found := runways(tokens[i:]) + hold_short(tokens[i:]):
                return found[0]
            if (hit := _runway_at(tokens, i + 1)) is not None:  # "..., correction 08"
                return hit[0]
    return next(iter(runways(tokens) + hold_short(tokens)), None)


REQUEST_WORDS = (("request",), ("requesting",), ("ready", "for"), ("ready", "to"), ("can", "we"), ("could", "we"),
                 ("like", "to"), ("looking", "for"))
PUSH_READBACK = (("approved",), ("discretion",), ("tail",), ("facing",), ("face",))


def _instructed(tokens: list[Token]) -> bool:
    """An altitude given as an instruction ("climb and maintain 5,000", "descend to 3,000"), which makes a
    call a readback. "1,300 feet climbing 5,000 feet" only reports altitudes: a check-in."""
    return any(t.text in ("maintain", "climb", "descend") for t in tokens) and bool(altitudes(tokens))


def _direction(tokens: list[Token]) -> str | None:
    """ "request climb" / "higher": up; "request descent" / "lower": down; else None."""
    # Only words that mean a level change: "up" and "down" turn up in everything ("people up there").
    if _has_any(tokens, ("climb",), ("higher",)):
        return "up"
    if _has_any(tokens, ("descent",), ("descend",), ("lower",)):
        return "down"
    return None


FIX_STOP = {"to", "the", "a", "direct", "please", "for", "if", "able", "possible", "when", "request", "requesting"}
CONDITION_WORDS = ("turbulence", "chop", "choppy", "icing", "ice", "bumpy", "smooth", "shear", "rough")
APPROACH_WORDS = {"ils": "ILS", "localizer": "ILS", "rnav": "RNAV", "gps": "RNAV", "visual": "VISUAL"}


def _fix(tokens: list[Token]) -> str | None:
    """What follows "direct": "request direct BLAKO" -> "BLAKO", "direct to the airport" -> "airport"."""
    for i in _find_phrase(tokens, ("direct",)):
        words = []
        for token in tokens[i:]:
            if token.text in FIX_STOP and not words:
                continue
            if token.kind != "word" or token.text in FIX_STOP or len(words) == 2:
                break
            words.append(token.text)
        if words:
            return " ".join(words).upper() if len(words[0]) <= 5 and len(words) == 1 else " ".join(words).title()
    return None


# A diversion: "request vectors to the nearest suitable airport", "we need to divert to Yuma".
DIVERT_WORDS = (("divert",), ("diverting",), ("diversion",), ("nearest",), ("closest",), ("alternate",),
                ("land", "as", "soon", "as", "possible"))
RETURN_WORDS = (("return",), ("returning",), ("back", "to"))
DIVERT_SKIP = FIX_STOP | {"nearest", "closest", "suitable", "airport", "airfield", "field", "aerodrome", "an", "alternate",
                          "vectors", "vector", "divert", "diverting", "diversion", "please", "immediate", "immediately"}
NOT_A_PLACE = {"final", "runway", "ils", "rnav", "gps", "visual", "localizer", "approach", "heading", "downwind", "base"}


def _divert_to(tokens: list[Token]) -> str | None:
    """Where a diversion is to, after the last "to": "Yuma", "Palm Springs", "KNYL" (spelled out). None for "the
    nearest suitable airport": ATC picks."""
    asked = next((i for i, t in enumerate(tokens) if t.text in ("vectors", "vector", "divert", "diverting", "diversion")), 0)
    starts = [i for i, t in enumerate(tokens) if t.text == "to" and i > asked]  # not "unable to make it"
    if not starts:
        return None
    words: list[str] = []
    for token in tokens[starts[-1] + 1:]:
        if token.text in DIVERT_SKIP or token.kind not in ("word", "letter") or token.text in NOT_A_PLACE:
            if words or token.text in NOT_A_PLACE:
                break
            continue
        words.append(token.text)
        if len(words) == 4:
            break
    if not words:
        return None
    if all(len(w) == 1 for w in words) and len(words) in (3, 4):
        return "".join(words).upper()  # an ICAO code, spelled out: "kilo november yankee lima"
    return " ".join(w for w in words if len(w) > 1).title() or None


def _turn(tokens: list[Token]) -> str | None:
    """ "we'd like a left turn after departure": a turn on departure, not a heading."""
    if not _has_any(tokens, ("after", "departure"), ("after", "takeoff"), ("on", "departure"), ("out", "of", "here")):
        return None
    if _has_any(tokens, ("left", "turn"), ("turn", "left"), ("left", "downwind"), ("left", "crosswind")):
        return "left"
    if _has_any(tokens, ("right", "turn"), ("turn", "right"), ("right", "downwind"), ("right", "crosswind")):
        return "right"
    return None


def _traffic(tokens: list[Token]) -> str | None:
    if _has_any(tokens, ("negative", "contact"), ("not", "in", "sight"), ("no", "joy")):
        return "negative"
    if _has_any(tokens, ("in", "sight"), ("have", "the", "traffic"), ("got", "the", "traffic"), ("traffic", "insight")):
        return "in_sight"
    if _has_any(tokens, ("looking",)):
        return "looking"
    return None


FOLLOWING_WORDS = (("flight", "following"), ("vfr", "advisories"), ("traffic", "advisories"), ("radar", "advisories"),
                   ("flight", "information", "service"), ("basic", "service"), ("traffic", "service"),
                   ("radar", "service"))
OPTION_WORDS = {("touch", "and", "go"): "touch_and_go", ("touch", "go"): "touch_and_go", ("the", "option"): "option",
                ("stop", "and", "go"): "option", ("low", "approach"): "option", ("full", "stop"): "full_stop"}
PATTERN_LEGS = (("midfield",), ("downwind",), ("base",), ("crosswind",), ("upwind",))
PATTERN_WORDS = (*PATTERN_LEGS, ("in", "the", "pattern"), ("in", "the", "circuit"))  # "remaining in the pattern" asks
CLOSED_WORDS = (("closed", "traffic"), ("remain", "in", "the", "pattern"), ("remaining", "in", "the", "pattern"),
                ("staying", "in", "the", "pattern"), ("pattern", "work"), ("remain", "in", "the", "circuit"),
                ("remaining", "in", "the", "circuit"), ("circuit", "work"), ("circuits",))
COMPASS = ("north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest")


def _vfr_direction(tokens: list[Token]) -> str | None:
    """A VFR departure's way out, as ATC approves it: "to the north" and "northbound" are "northbound";
    "straight out" is "straight-out"."""
    if _has_any(tokens, ("straight", "out")):
        return "straight-out"
    if _has_any(tokens, *CLOSED_WORDS):
        return "closed-traffic"
    words = [t.text for t in tokens]
    for word in words:
        for point in COMPASS:
            if word in (point, f"{point}bound"):
                return f"{point}bound"
    return None


def _option(tokens: list[Token]) -> str | None:
    """ "touch_and_go", "option" or "full_stop" when the pilot asks for one."""
    return next((kind for phrase, kind in OPTION_WORDS.items() if _has_any(tokens, phrase)), None)


def match_intents(tokens: list[Token]) -> list[IntentMatch]:
    """All intents present, most specific first."""
    matches: list[IntentMatch] = []

    def add(intent: str, **values: Any) -> None:
        matches.append(IntentMatch(intent, {k: v for k, v in values.items() if v is not None}))

    # VFR: flight following, a Class B clearance, the option and the way out ride along with the main call
    # ("ready to taxi, VFR to the north, request flight following"), or stand alone (end of this function).
    following = True if _has_any(tokens, *FOLLOWING_WORDS) else None
    class_b = True if _has_any(tokens, ("class", "b"), ("b", "airspace"), ("b", "clearance"), ("into", "b"),
                                ("b", "departure"), ("class", "bravo"), ("bravo", "airspace"), ("bravo", "clearance"),
                                ("into", "the", "bravo"), ("control", "zone")) and _has_any(tokens, *REQUEST_WORDS) else None
    option = _option(tokens)
    direction = _vfr_direction(tokens)
    vfr = {"flight_following": following, "class_b": class_b, "direction": direction}
    checkin_words = (("climbing",), ("descending",), ("level",), ("with", "you"), ("checking", "in"), ("leaving",),
                     ("passing",), ("through",), ("out", "of"))
    # "climbing 4,500, request a Class Bravo clearance": a check-in asking for Class B, not an IFR clearance
    b_in_flight = bool(class_b) and _has_any(tokens, *checkin_words)

    if _has_any(tokens, ("mayday",), ("pan", "pan"), ("emergency",)):
        add(EMERGENCY)
    if _has_any(tokens, ("radio", "check"), ("comm", "check"), ("how", "do", "you", "read"), ("radio", "test")):
        add("radio_check")
    if _has_any(tokens, ("say", "again"), ("repeat",), ("didn't", "copy"), ("did", "not", "copy"), ("say", "that", "again")):
        add("say_again")
    landing = _has_any(tokens, ("clear", "to", "land"), ("cleared", "to", "land"), ("clearance", "to", "land"),
                       ("landing", "clearance"), ("clear", "for", "landing"), ("cleared", "for", "landing"))
    if _has_any(tokens, ("clearance",), ("ifr", "to"), ("i", "f", "r", "to"), ("ready", "to", "copy"), ("request", "ifr"),
                ("requesting", "ifr")) and not _has_any(
        tokens, ("cleared",), ("taxi",)  # "Clearance, request taxi": the station's name, not an IFR request
    ) and not landing and not b_in_flight:
        add("request_ifr_clearance", atis=_atis(tokens), **vfr)
    pushing = _has_any(tokens, ("pushback",), ("push", "back"), ("push", "and", "start"), ("push", "start"),
                       ("request", "push"), ("ready", "for", "push"), ("ready", "to", "push"))
    if pushing and _has_any(tokens, *PUSH_READBACK) and not _has_any(tokens, *REQUEST_WORDS):
        add("acknowledge")  # "push back at my discretion, tail right": reading back the approval, not asking again
    elif pushing:
        add("request_pushback")
    parking = _has_any(tokens, ("to", "parking"), ("to", "the", "ramp"), ("to", "ramp"), ("to", "the", "gate"), ("to", "gate"))
    if _has_any(tokens, ("clear", "of", "runway"), ("clear", "of", "the", "runway"), ("clear", "runway"), ("clear", "of")):
        add("clear_of_runway", runway=_any_runway(tokens))
    wants_stand = _has_any(tokens, ("request", "gate"), ("request", "parking"), ("request", "stand"), ("request", "ramp"),
                           ("requesting", "gate"), ("requesting", "parking"), ("gate", "assignment"), ("which", "gate"),
                           ("request", "taxi", "to", "gate"), ("request", "taxi", "gate"), ("for", "gate"), ("to", "our", "gate"))
    if (parking and _has_any(tokens, ("taxi",))) or (wants_stand and not pushing):
        add("request_taxi_parking")  # "request taxi to the gate", "request gate", "request parking"
    elif _has_any(tokens, ("ready", "to", "taxi"), ("request", "taxi"), ("taxi", "with"), ("ready", "for", "taxi"),
                  ("request", "ifr", "taxi"), ("taxi", "to", "runway"), ("taxi", "to", "active"), ("taxi", "to", "the", "active"),
                  ("taxi", "to", "the", "runway")):
        add("ready_to_taxi", atis=_atis(tokens), **vfr)
    if _has_any(
        tokens, ("ready", "for", "departure"), ("ready", "for", "takeoff"), ("ready", "to", "go"), ("ready", "for", "take", "off"),
        ("ready", "to", "depart"), ("ready", "to", "departure"), ("ready", "for", "departures"),
        ("like", "to", "get", "the", "departure"), ("get", "the", "departure"), ("request", "departure"),
        ("for", "departure"), ("to", "depart"), ("for", "takeoff"),
        ("request", "the", "departure"), ("like", "the", "departure"), ("ready", "in", "sequence")
    ):
        add("ready_for_departure", runway=_any_runway(tokens), **vfr)
    elif (hold_short(tokens) or _has_any(tokens, ("holding", "point"), ("at", "the", "holding"))) and not _has_any(
            tokens, ("taxi",), ("via",), ("cleared",), ("hold", "position"), ("holding", "position")):
        # "Tower, holding short runway 06L" / "holding point Charlie 1" is a departure request; a taxi
        # readback names a route instead.
        add("ready_for_departure", runway=_any_runway(tokens))
    if _has_any(tokens, ("mile", "final"), ("miles", "final"), ("on", "final"), ("short", "final"), ("inbound",),
                ("on", "the", "approach"), ("on", "approach"), ("established",), ("for", "the", "visual"),
                ("field", "in", "sight"), ("runway", "in", "sight")) or landing:
        add("report_final", runway=_any_runway(tokens), option=option)  # also "are we cleared to land?"
    elif _has_any(tokens, *(PATTERN_LEGS if direction == "closed-traffic" else PATTERN_WORDS)) and not _instructed(tokens) \
            and not _has_any(tokens, ("enter",), ("join",), ("make",)):
        add("position_report", option=option)  # "midfield left downwind 27": a pattern report
    wanted = altitudes(tokens) or ([n for n in _reported_altitudes(tokens) if n >= 1000]
                                   if _has_any(tokens, ("higher",), ("lower",), ("climb",), ("descend",)) else [])
    if _has_any(tokens, ("request",), ("requesting",), ("like",), ("can", "we"), ("could", "we")) and wanted:
        add("request_altitude", altitude=wanted[0])  # "request to maintain 1,500", "request higher, 7000"
    elif _has_any(tokens, ("request",), ("requesting",), ("like",), ("can", "we"), ("could", "we")) and (
            direction := _direction(tokens)) is not None:
        add("request_altitude", direction=direction)  # "request climb": ATC picks the altitude
    asking = _has_any(tokens, ("request",), ("requesting",), ("like",), ("can", "we"), ("could", "we"), ("able", "to"),
                      ("want",), ("need",))
    if (turn := _turn(tokens)) is not None:
        add("request_turn", turn=turn)
    if _has_any(tokens, ("going", "around"), ("go", "around"), ("missed", "approach"), ("executing", "missed")):
        add("going_around")
    if (traffic := _traffic(tokens)) is not None and (_has_any(tokens, ("traffic",)) or traffic != "in_sight"):
        add("traffic_report", traffic=traffic)
    if _has_any(tokens, ("direct",)) and not _has_any(tokens, ("disregard",)) and (fix := _fix(tokens)):
        add("request_direct", fix=fix)
    approach = next((APPROACH_WORDS[t.text] for t in tokens if t.text in APPROACH_WORDS), None)
    returning = _has_any(tokens, *RETURN_WORDS)
    place = _divert_to(tokens) if _has_any(tokens, ("vectors",), ("vector",), *DIVERT_WORDS) else None
    diverting = not returning and (_has_any(tokens, *DIVERT_WORDS) or (
        place is not None and not approach and not runways(tokens)))  # "vectors to Yuma": somewhere else to land
    if diverting:
        add("request_diversion", fix=place)
    elif _has_any(tokens, ("vectors",), ("vector",)):
        add("request_vectors", runway=next(iter(runways(tokens)), None), approach=approach)
    if asking and returning and not diverting:
        add("request_return")
    if asking and (approach or runways(tokens)) and not _has_any(tokens, ("taxi",), ("final",)):
        add("request_runway", runway=next(iter(runways(tokens)), None), approach=approach)
    if any(t.text in CONDITION_WORDS for t in tokens) and not asking:
        add("report_conditions", conditions=" ".join(t.text for t in tokens if t.kind == "word" and t.text in (
            *CONDITION_WORDS, "light", "moderate", "severe", "occasional", "continuous")))
    if _has_any(tokens, *checkin_words) and not _instructed(tokens):
        reported = _reported_altitudes(tokens)
        add("checkin", altitude=reported[0] if reported else None, assigned=reported[1] if len(reported) > 1 else None,
            atis=_atis(tokens), **vfr)
    carried = {"request_ifr_clearance", "ready_to_taxi", "ready_for_departure", "checkin"}
    if not any(m.intent in carried for m in matches):
        if following:
            add("request_flight_following", direction=direction)
        elif class_b:
            add("request_class_b")
    if option and not any(m.intent in ("report_final", "position_report") for m in matches):
        add("request_option", option=option)
    if not matches and reports_problem(tokens):
        add("report_problem")  # alone; with other calls the engine hears it anyway (AtcEngine._problem)
    if not matches and _has_any(tokens, ("tail", "left"), ("tail", "right"), ("push", "approved"), ("face", "east"),
                                ("face", "west"), ("face", "north"), ("face", "south"), ("facing",)):
        add("acknowledge")  # reading back a pushback approval
    if not matches and _has_any(tokens, ("hold", "position"), ("holding", "position"), ("holding", "short", "of", "traffic")):
        add("acknowledge")  # "hold position, B737 crossing": the pilot stops, nothing to answer
    if not matches and _has_any(tokens, ("roger",), ("wilco",), ("copy",), ("will", "comply"), ("disregard",), ("thanks",),
                                ("thank", "you"), ("affirm",), ("affirmative",), ("copy", "that"), ("good", "day"),
                                ("stand", "by"), ("standby",), ("standing", "by"), ("will", "stand", "by"),
                                ("frequency", "change", "approved"), ("squawk", "vfr"), ("squawking", "vfr")):
        add("acknowledge")
    return matches


# Something wrong that isn't a mayday or pan-pan: ATC acknowledges, and rolls the trucks if asked.
EQUIPMENT_WORDS = (("trucks",), ("fire", "trucks"), ("crash", "trucks"), ("emergency", "equipment"), ("equipment",),
                   ("emergency", "services"), ("fire", "services"))
PROBLEM_WORDS = (("failure",), ("failed",), ("inoperative",), ("malfunction",), ("not", "working"), ("unreliable",))


def reports_problem(tokens: list[Token]) -> str | None:
    """ "equipment" when the pilot asks for the trucks, "problem" for a failure report, else None."""
    if _has_any(tokens, *EQUIPMENT_WORDS):
        return "equipment"
    return "problem" if _has_any(tokens, *PROBLEM_WORDS) else None


# Intents that legitimately appear together; the first one is used.
COMPATIBLE = [
    {"request_taxi_parking", "clear_of_runway"},
    {"going_around", "report_final"},  # "going around, runway 08": no longer a final report
    {"request_vectors", "request_runway"},  # "request vectors for the ILS 26"
    {"request_direct", "request_runway"},
    {"ready_for_departure", "request_turn"},  # "holding short, ready, request a left turn out"
    {"ready_for_departure", "checkin"},  # "holding short, ready" can contain "level"-like noise
    {"ready_for_departure", "request_runway"},  # "holding short runway 25L, we'd like the departure"
    {"ready_for_departure", "position_report"},  # "ready for departure, remaining in the pattern"
    {"position_report", "request_runway"},  # "midfield downwind 34, request touch and go"
    {"position_report", "request_turn"},
]


def resolve(matches: list[IntentMatch]) -> tuple[IntentMatch | None, bool]:
    """Pick the intent; returns (match, ambiguous)."""
    if not matches:
        return None, False
    emergency = next((m for m in matches if m.intent == EMERGENCY), None)
    if emergency is not None:
        return emergency, False
    intents = {m.intent for m in matches}
    if len(intents) == 1 or any(intents <= group for group in COMPATIBLE):
        preferred = next((m for m in matches if m.intent == "request_taxi_parking"), matches[0])
        return preferred, False
    return matches[0], True
