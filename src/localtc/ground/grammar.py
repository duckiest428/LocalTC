"""What the pilot asks of the ground crew, read from their words without a language model: what's asked for, of which
services, and the direction, the gate or the choice that goes with it. It's all there is without a cloud model."""

import re
from dataclasses import dataclass

from localtc.ground.api import SERVICES

REQUEST, CANCEL, FINISH, HURRY, STATUS, PUSH, STOP_PUSH, OFF = "request", "cancel", "finish", "hurry", "status", "push", "stop_push", "off"
WHERE, GATE, YES, NO, OPTION, BRAKES_SET, BRAKES_RELEASED, GOOD_START, FULL = "where", "gate", "yes", "no", "option", "brakes_set", "brakes_released", "good_start", "full"
LEVEL = "level"  # how much fuel: ``option`` percent

# Nose and tail, as GSX has them: nose left is tail right. Said on its own, "left" is GSX's left: the tail.
NOSE_LEFT, NOSE_RIGHT, STRAIGHT = "nose_left", "nose_right", "straight"

SERVICE_WORDS: dict[str, str] = {
    "GPU": r"g\s?p\s?u|ground\s+power(?:\s+unit)?|external\s+power|power\s+(?:unit|cart)",
    "OperateJetways": r"jet\s?ways?|jet\s?bridges?|air\s?bridges?|gangways?|the\s+bridge",
    "OperateStairs": r"air\s?stairs|stairs?|steps",
    "Deboarding": r"de[\s-]?board(?:ing)?|disembark(?:ation|ing)?|offload(?:ing)?(?:\s+the)?\s+passengers|passengers?\s+off",
    "Boarding": r"(?<!de)(?<!de\s)(?<!de-)board(?:ing)?|embark(?:ation|ing)?|passengers?\s+on(?:board)?",
    "Cleaning": r"clean(?:ing|ers?)?|cabin\s+service",
    "Catering": r"cater(?:ing|ers?)?|meals?|galley",
    "Refueling": r"re[\s-]?fuel(?:l?ing)?|fuel(?:l?ing)?|bowser",
    "Water": r"(?:potable\s+)?water",
    "Lavatory": r"lav(?:s|atory|atories)?|toilets?|waste",
    "DeIce": r"de[\s-]?ic(?:e|ing)",
    "Departure": r"push\s?-?back|push(?:ing)?\b|the\s+tug|a\s+tug",
}
_SERVICE = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in SERVICE_WORDS.items()}
ADDRESS = re.compile(r"^\W*(?:hey\s+|hello\s+|ok(?:ay)?\s+)?(?:ramp(?:\s+agent)?|ground\s+crew|chief|headset|tug|marshall?er|dispatcher)\b[\s,.:]*", re.I)

_YES = re.compile(r"^\W*(?:yes|yeah|yep|affirm(?:ative)?|correct|go\s+ahead|please\s+do|do\s+it|sure|ok(?:ay)?|roger|please)\W*(?:please\W*)?$", re.I)
_NO = re.compile(r"^\W*(?:no|nope|negative|not\s+now|no\s+thanks|don'?t|leave\s+it|stand\s+by)\W*$", re.I)
_NUMBERS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4, "five": 5, "fifth": 5,
            "six": 6, "sixth": 6, "seven": 7, "seventh": 7, "eight": 8, "eighth": 8, "nine": 9, "ninth": 9, "ten": 10, "tenth": 10}
_OPTION = re.compile(r"\b(?:option|number|choice)\s+(\d+|" + "|".join(_NUMBERS) + r")\b|\bthe\s+(" + "|".join(k for k in _NUMBERS if k.endswith(("st", "nd", "rd", "th"))) + r")\s+one\b", re.I)
_BRAKES_SET = re.compile(r"\bbrakes?\s+(?:are\s+|is\s+)?(?:now\s+)?(?:set|on|parked|applied)\b|\bparking\s+brake\s+(?:is\s+)?(?:now\s+)?(?:set|on)\b|\bset\s+(?:the\s+)?parking\s+brake\b|"
                         r"\bbrakes?\s+(?:has|have)\s+been\s+set\b|^\W*(?:it'?s|it\s+is|that'?s|its)\s+(?:now\s+)?set\W*$", re.I)
_BRAKES_OFF = re.compile(r"\bbrakes?\s+(?:are\s+|is\s+)?(?:released?|off)\b|\breleas(?:e|ed|ing)\s+(?:the\s+)?(?:parking\s+)?brakes?\b", re.I)
_GOOD_START = re.compile(r"\bgood\s+start\b|\bengines?\s+(?:is\s+|are\s+)?(?:started|running|stable|good)\b|\ball\s+(?:engines\s+)?started\b|\bstart\s+(?:is\s+)?good\b", re.I)
_STOP_PUSH = re.compile(r"\bstop\s+(?:the\s+)?(?:push|pushback|tug|tow)\b|\bstop\s+stop\b|^\W*(?:stop|hold\s+position|hold\s+there)\W*$", re.I)
_STATUS = re.compile(r"\bstatus\b|\boutstanding\b|\bwhat(?:'?s|\s+is)\s+(?:still\s+)?(?:left|going\s+on|happening|pending|running|remaining|active)\b|\bhow(?:'?s|\s+is|\s+long|\s+much\s+longer|\s+are\s+(?:we|things|the))\b|\bwhere(?:'?s|\s+is|\s+are)\b|\banything\s+(?:left|else\s+going)\b|\bare\s+(?:we|you)\s+(?:done|finished|ready)\b|\bis\s+(?:the\s+)?\w+(?:\s+\w+)?\s+(?:done|finished|ready|complete)\b|\beta\b", re.I)
_CANCEL = re.compile(r"\b(?:cancel|abort|scrub|forget(?:\s+about)?|never\s*mind|skip|call\s+off|don'?t\s+(?:need|want|bother(?:\s+with)?)|no\s+need\s+for)\b", re.I)
_FINISH = re.compile(r"\b(?:complete|finish|wrap\s+up|end)\b[^.]*\bnow\b|\b(?:that'?s\s+enough|wrap\s+it\s+up|finish\s+(?:it\s+)?up)\b", re.I)
_HURRY = re.compile(r"\bspeed\s+(?:it\s+|that\s+|things\s+)?up\b|\bhurry\b|\bexpedite\b|\bfaster\b|\bquicker\b|\brush\b", re.I)
_OFF = re.compile(r"\b(?:disconnect(?:ion|ing)?|remov(?:e|al|ing)|dismiss|retract(?:ion)?|detach|pull(?:\s+\w+){0,2}\s+back|pull\s+back|take\s+(?:\w+\s+){0,2}away|send\s+(?:\w+\s+){0,2}away|get\s+rid\s+of|undock)\b|\b(?:off|away)\W*(?:please\W*)?$", re.I)
_FULL = re.compile(r"\b(?:full\s+(?:service|turn\s?around)|the\s+(?:whole\s+)?turn\s?around|do\s+the\s+turn\s?around|the\s+works|all\s+(?:the\s+)?services|everything\s+please)\b", re.I)
_GATE = re.compile(r"\b(?:gate|stand|parking(?:\s+(?:spot|position))?)\s+([a-z]?\s?-?\s?\d{1,3}\s?[a-z]?|[a-z]{1,2}\s?\d{1,3})\b", re.I)
_WHERE = re.compile(r"\b(?:what|which)\s+(?:gate|stand|bay|parking(?:\s+(?:spot|position))?)\b|\bwhere\s+are\s+we\s+parked\b|\bwhat(?:'?s|\s+is)\s+(?:our|my|this)\s+(?:gate|stand)\b", re.I)
_GATE_CHANGE = re.compile(r"\b(?:change|move|switch|different|another|prefer|want|like|give|assign|need|request|can\s+(?:we|i)\s+have)\b", re.I)
_NOSE = re.compile(r"\bnose\s+(left|right)\b|\bfac(?:e|ing)\s+(left|right)\b|\b(left|right)\s+nose\b", re.I)
_TAIL = re.compile(r"\btail\s+(left|right)\b|\b(left|right)\s+tail\b|\bpush(?:\s?-?back)?\s+(?:to\s+the\s+)?(left|right)\b|^\W*(left|right)\W*(?:please\W*)?$", re.I)
_STRAIGHT = re.compile(r"\bstraight(?:\s+(?:back|out))?\b", re.I)
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "one hundred": 100, "hundred": 100}
_PERCENT = re.compile(r"\b(\d{1,3}|" + "|".join(_TENS) + r")(?:[\s-]+(five))?\s*(?:%|per\s?cent)", re.I)
_FILL_UP = re.compile(r"\bfull\s+(?:tanks?|fuel|load)\b|\bfill\s+(?:it|her|them|us|the\s+tanks?)\s+up\b|\bfill\s+up\b", re.I)
_BREAK = re.compile(r"\bbreaks?\b", re.I)  # (typed, or heard: "parking break")
# Clauses that ask for different things: "cancel catering but send water". (Not "and": "cancel catering and fuel".)
_CLAUSES = re.compile(r"\s*(?:[.;!?]+|\bbut\b|\bthen\b|\bafter\s+that\b|\balso\b)\s*", re.I)


@dataclass(frozen=True)
class Ask:
    action: str
    services: tuple[str, ...] = ()
    direction: str = ""
    gate: str = ""
    option: int = 0


def without_address(text: str) -> str:
    return ADDRESS.sub("", text, count=1)


def is_addressed(text: str) -> bool:
    return bool(ADDRESS.match(text))


def direction_of(text: str) -> str:
    if m := _NOSE.search(text):
        return NOSE_LEFT if (m.group(1) or m.group(2) or m.group(3)).lower() == "left" else NOSE_RIGHT
    if m := _TAIL.search(text):  # the tail goes one way, the nose the other
        return NOSE_RIGHT if next(g for g in m.groups() if g).lower() == "left" else NOSE_LEFT
    return STRAIGHT if _STRAIGHT.search(text) else ""


def services_in(text: str) -> tuple[str, ...]:
    """The services named, in the order said."""
    hits = [(m.start(), k) for k, rx in _SERVICE.items() if (m := rx.search(text))]
    named = [k for _, k in sorted(hits)]
    return tuple(k for k in named if k in SERVICES)


def _gate(text: str) -> str:
    m = _GATE.search(text)
    return re.sub(r"[\s-]+", "", m.group(1)).upper() if m else ""


def level_of(text: str) -> int:
    """The fuel asked for, in percent ("sixty percent", "70%", "fill it up"); 0: none said."""
    if _FILL_UP.search(text):
        return 100
    m = _PERCENT.search(text)
    if not m:
        return 0
    said = m.group(1).lower()
    return (int(said) if said.isdigit() else _TENS[said]) + (5 if m.group(2) else 0)


def _clause(text: str) -> list[Ask]:
    named = services_in(text)
    if level := level_of(text):
        if "Refueling" in named or _FILL_UP.search(text):  # (with whatever else was asked for in the same breath)
            return [Ask(REQUEST, named if "Refueling" in named else ("Refueling", *named), option=level)]
        return [Ask(LEVEL, option=level)]
    if _STOP_PUSH.search(text):
        return [Ask(STOP_PUSH)]
    if _BRAKES_SET.search(text) and not named:
        return [Ask(BRAKES_SET)]
    if _BRAKES_OFF.search(text) and not named:
        return [Ask(BRAKES_RELEASED)]
    if _GOOD_START.search(text):
        return [Ask(GOOD_START)]
    if m := _OPTION.search(text):
        said = (m.group(1) or m.group(2)).lower()
        return [Ask(OPTION, option=int(said) if said.isdigit() else _NUMBERS[said])]
    if _YES.match(text):
        return [Ask(YES)]
    if _NO.match(text):
        return [Ask(NO)]
    if _WHERE.search(text) and not _GATE_CHANGE.search(text):
        return [Ask(WHERE)]
    if (gate := _gate(text)) and (_GATE_CHANGE.search(text) or not named):
        return [Ask(GATE, gate=gate)]
    if _CANCEL.search(text):
        return [Ask(CANCEL, named)]
    if _FINISH.search(text):
        return [Ask(FINISH, named)]
    if _HURRY.search(text):
        return [Ask(HURRY, named)]
    if _STATUS.search(text):
        return [Ask(STATUS, named)]
    if _FULL.search(text):
        return [Ask(FULL)]
    if _OFF.search(text) and named:
        return [Ask(OFF, named)]
    direction = direction_of(text)
    if "Departure" in named or direction:
        rest = tuple(k for k in named if k != "Departure")
        return ([Ask(REQUEST, rest)] if rest else []) + [Ask(PUSH, direction=direction)]
    return [Ask(REQUEST, named)] if named else []


def parse(text: str) -> tuple[Ask, ...]:
    """What's asked, in the order said; nothing when it couldn't be read."""
    said = _BREAK.sub("brake", without_address(text).strip())
    asks: list[Ask] = []
    for clause in _CLAUSES.split(said):
        for ask in _clause(clause.strip()) if clause.strip() else ():
            if ask.action == PUSH and asks and asks[-1].action == PUSH:  # "can we get a pushback? tail left please": one push
                asks[-1] = Ask(PUSH, direction=ask.direction or asks[-1].direction)
            else:
                asks.append(ask)
    return tuple(asks)
