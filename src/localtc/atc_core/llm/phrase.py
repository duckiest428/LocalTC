"""The language model wording ATC's replies.

Two jobs, both after the engine has decided what the reply is:
- ``reply``: the words for what has no template, an answer to a free-form question or declining a request
  the engine doesn't support. Checked before it goes on the air: no instructions or approvals (no "cleared",
  "climb", "contact", "squawk", ...), every number from the facts it was given, short.
- ``reword``: the words for a reply the script already has (a taxi clearance, "contact departure 124.0"), in
  the modes where the model words ATC's replies (``[llm] mode``). Checked the other way round: every number,
  runway, name and instruction word of the script's reply is in it, and nothing is added (no number, approval,
  negation or instruction the script didn't give). The readback ATC expects stays the script's.
The callsign is added by the template, not the model. Anything that fails twice is the template's words, and
the engine records why (``AtcDecision``).
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import replace

from localtc.atc_core.llm.backend import LlmBackend, LlmRequest, waits
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Phrase
from localtc.sim_api import LlmExchange

# [llm] beyond_facts: the model may answer from what it knows as well, where the sim's data says nothing.
FACTS_RULE = """- Use only the facts given. Never invent numbers, names or information: nothing is closed, restricted, active or \
delayed unless the facts say so."""
BEYOND_RULE = """- Use the facts first. Where they say nothing, you may answer from what a controller would know about aviation and \
this airport; if you don't know, say the information isn't available. Never contradict the facts."""

SYSTEM_TEMPLATE = """You word one short reply for an air traffic controller, in standard radio phraseology. \
The controller has already decided what to say; you only put it into words.
- You are the controller named in the facts: answer as that station would.
{facts_rule}
- Use only the facts that answer what the pilot said; never list the others.
- Never give or approve an instruction: no clearances, altitudes, headings, frequencies to contact, squawk codes \
or taxi routes, and never say "approved". If the pilot asks for something like that, say "unable" and, if it \
fits, "continue as filed".
- Decision answer: answer what the pilot asked. If the facts answer only part of it, give that part and say the \
rest isn't available (you have no weather reports along the route or at other airports unless the facts say so). \
If they answer none of it, say "unable, information not available".
- Decision reply: the pilot said something that is no request (small talk, thanks, a remark, a correction). Reply \
briefly and politely as a busy controller would ("roger", "copy that", "no problem"); if they're right or wrong \
about something in the facts, say so. If the facts say you are waiting for a readback the pilot didn't give, ask \
for it: "read back the altitude".
- At most 20 words. Do not start with the callsign; it is added for you."""


def system(beyond_facts: bool = False) -> str:
    return SYSTEM_TEMPLATE.replace("{facts_rule}", BEYOND_RULE if beyond_facts else FACTS_RULE)


SYSTEM = system()

SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
          "additionalProperties": False}

EXAMPLES: tuple[tuple[str, str], ...] = (
    ('Pilot asked: "any weather to report at quebec"\nDecision: answer\nFacts: controller Montreal Center; time 1412Z; '
     'Quebec wind 240@8; Quebec altimeter 29.92',
     '{"reply":"Quebec wind 240 at 8, altimeter 29.92"}'),
    ('Pilot asked: "request direct Quebec"\nDecision: decline\nFacts: controller Montreal Center; destination Quebec; '
     'phase cruise; cleared altitude FL230',
     '{"reply":"unable direct at this time, continue as filed"}'),
    ('Pilot asked: "any bad weather on the way to Phoenix today"\nDecision: answer\nFacts: controller San Diego Ground; '
     'destination Phoenix; wind 270@6; altimeter 29.98; departure runway 27; here and now: wind 270 at 6, altimeter 29.98',
     '{"reply":"no weather reports along your route available, San Diego wind 270 at 6, altimeter 29.98"}'),
    ('Pilot asked: "how long until we get there"\nDecision: answer\nFacts: controller Seattle Approach; time 0230Z; '
     'landing runway 16L; ATIS information C',
     '{"reply":"unable, information not available"}'),
    ('Pilot asked: "how are you doing today"\nDecision: reply\nFacts: controller Denver Ground; phase parked; '
     'wind 180@9; departure runway 17R',
     '{"reply":"doing well, thanks for asking"}'),
    ('Pilot asked: "negative, that plane is not in our way"\nDecision: reply\nFacts: controller Toronto Ground; '
     'phase taxi out; wind 360@13; departure runway 33R; ATIS information T',
     '{"reply":"roger, thanks for letting me know"}'),
)

BANNED = {"cleared", "clear", "climb", "descend", "maintain", "turn", "heading", "contact", "squawk", "taxi", "approved",
          "proceed", "vectors", "expect", "line", "takeoff", "monitor", "identify"}
# The prompt's own words ("phase arrival in effect") and navaids it wasn't told about ("ILS not available").
INTERNAL = {"phase", "facts", "fact", "decision", "effect"}
NAVAIDS = {"ils", "rnav", "localizer", "glideslope", "vor", "ndb", "gps"}
# Claims about the state of things ("runway 19L closed", "restricted airspace active"): only what the facts say.
STATUS = {"closed", "closure", "closures", "maintenance", "restricted", "restriction", "restrictions", "active", "inactive",
          "inoperative", "unserviceable", "construction", "tfr", "notam", "notams", "occupying", "occupied", "blocked",
          "delay", "delays", "holding", "stop", "outage", "parallel", "simultaneous", "congested", "work", "expected", "shortly", "soon"}
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")  # "10,000": one number
MAX_WORDS = 25


class PhraseError(ValueError):
    pass


def user_message(pilot: str, decision: str, facts: dict[str, str]) -> str:
    listed = "; ".join(f"{k} {v}" for k, v in facts.items()) or "none"
    return f'Pilot asked: "{pilot}"\nDecision: {decision}\nFacts: {listed}'


def _number(n: str) -> str:
    """A number as compared: "124.50" is "124.5", "05" is "5"."""
    whole, _, frac = n.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def numbers(text: str) -> set[str]:
    return {_number(n) for n in NUMBER_RE.findall(THOUSANDS_RE.sub("", text))}


def _reply_text(raw: str, callsigns: tuple[str, ...]) -> str:
    """The reply's words from the model's JSON, without a callsign it put first (the template adds it)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PhraseError(f"not valid JSON ({exc.msg})") from None
    reply = data.get("reply") if isinstance(data, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        raise PhraseError("reply must be a non-empty string")
    text = " ".join(reply.split()).strip(" .")
    for callsign in callsigns:
        if callsign and text.lower().startswith(callsign.lower()):
            text = text[len(callsign):].lstrip(" ,")
        if callsign and text.lower().endswith(callsign.lower()):  # "..., United 1596" at the end, as pilots hear it too
            text = text[:-len(callsign)].rstrip(" ,.")
    return text


def _check_facts(text: str, words: list[str], facts: dict[str, str]) -> None:
    """Nothing in the reply that the facts don't say."""
    fact_words = set(re.findall(r"[a-z]+", " ".join([*facts, *facts.values()]).lower()))
    if claims := sorted(set(words) & STATUS - fact_words):
        raise FactsError(f"reply says {', '.join(claims)}, which the facts don't say; say only what they say, or that "
                         "there's nothing to report")
    if navaids := sorted(set(words) & NAVAIDS - fact_words):
        raise FactsError(f"reply mentions {', '.join(navaids)}, which is not in the facts")
    known = numbers(" ".join(facts.values()))
    if invented := [n for n in NUMBER_RE.findall(THOUSANDS_RE.sub("", text)) if _number(n) not in known]:
        raise FactsError(f"reply has numbers that are not in the facts: {', '.join(invented)}")
    _check_runways(text, facts)


def _check_runways(text: str, facts: dict[str, str]) -> None:
    """Runways as the facts have them: a pair ("19L/01R") only as the airport has it, and one said to be in use only
    if the facts say it is."""
    listed = " ".join(facts.values()).upper()
    for pair in re.findall(r"\b\d{1,2}[LRC]?/\d{1,2}[LRC]?\b", text.upper()):
        if not re.search(rf"(?<![\w/]){re.escape(pair)}(?![\w/])", listed):
            raise FactsError(f"reply names runway {pair}, which the airport doesn't have")
    in_use = {d.lstrip("0") for k, v in facts.items() if "in use" in k or k in ("landing runways", "departing runways",
                                                                                "landing runway", "departure runway")
              for d in re.findall(r"\b\d{1,2}[LRC]?\b", v.upper())}
    for clause in re.split(r",|;| and | but ", text.lower()):
        if "in use" in clause or "landing" in clause or "departing" in clause:
            said = {d.lstrip("0") for d in re.findall(r"\b\d{1,2}[LRC]?\b", clause.upper())}
            if wrong := sorted(said - in_use):
                raise FactsError(f"reply says runway {', '.join(wrong)} is in use; the facts say {', '.join(sorted(in_use)) or 'none'}")


class FactsError(PhraseError):
    """A reply saying something the facts don't (a number, a closure, a navaid, a runway in use): turned away unless
    [llm] beyond_facts lets the model answer from what it knows."""


# How a FactsError reads in an LlmExchange's detail (the engine tells the pilot the setting would have let it through).
FACTS_PROBLEMS = ("which the facts don't say", "which is not in the facts", "not in the facts:",
                  "which the airport doesn't have", "is in use; the facts say")


def facts_problem(detail: str) -> bool:
    return any(p in detail for p in FACTS_PROBLEMS)


def check_reply(raw: str, facts: dict[str, str], callsigns: tuple[str, ...], required: str = "", *,
                beyond_facts: bool = False) -> str:
    """The model's reply, checked against the facts. ``required``: the data's own answer to the question (the facts'
    "here and now"): a reply that gives other numbers than its own, or another runway, answers something else.
    ``beyond_facts``: what the facts don't cover may come from the model ([llm] beyond_facts); the safety checks (no
    instruction, short, the data's own answer when there is one) stay."""
    text = _reply_text(raw, callsigns)
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        raise PhraseError("reply has no words")
    if len(text.split()) > MAX_WORDS:
        raise PhraseError(f"reply is longer than {MAX_WORDS} words")
    if banned := sorted(set(words) & BANNED):
        raise PhraseError(f"reply gives an instruction ({', '.join(banned)}); only answer or say unable")
    if internal := sorted(set(words) & INTERNAL):
        raise PhraseError(f"reply talks about the prompt ({', '.join(internal)}); answer as a controller would")
    if not beyond_facts:
        _check_facts(text, words, facts)
    if required and "unable" not in words:
        if missing := sorted(numbers(required) - numbers(text)):
            raise PhraseError(f"reply leaves out {', '.join(missing)}: the answer is \"{required}\" (here and now)")
        runways = {f"{int(n)}{side}" for n, side in RUNWAY_DESIGNATOR.findall(required)}
        said = {f"{int(n)}{side}" for n, side in RUNWAY_DESIGNATOR.findall(_plain(text).upper())}
        if said - runways:
            raise PhraseError(f"reply is about runway {', '.join(sorted(said - runways))}; the answer is \"{required}\"")
    return text


def spoken(text: str) -> str:
    """Numbers read digit by digit, as controllers say them."""
    return NUMBER_RE.sub(lambda m: speech.digits(m.group(0)), text)


def more_lines(more: dict[str, str] | None) -> str:
    """Facts past what a small model is given, one per line, for a cloud model (``LlmRequest.context``)."""
    return "\n".join(f"- {k}: {v}" for k, v in (more or {}).items())


def phrase_request(pilot: str, decision: str, facts: dict[str, str], *, beyond_facts: bool = False,
                   more: dict[str, str] | None = None) -> LlmRequest:
    """The request for a reply's wording: the examples, then this call."""
    messages: tuple[tuple[str, str], ...] = tuple(
        turn for user, assistant in EXAMPLES for turn in (("user", user), ("assistant", assistant))
    ) + (("user", user_message(pilot, decision, facts)),)
    return LlmRequest("phrase", system(beyond_facts), messages, SCHEMA, max_tokens=80, context=more_lines(more))


# --- rewording the script's replies -------------------------------------------------------------------------------

REWORD_SYSTEM = """You are an air traffic controller on the radio. The controller's reply is decided; say it \
the way a real controller would, in standard radio phraseology, fitting what the pilot said.
- Say every item of the Keep line exactly as written: numbers, runways, altitudes, flight levels, headings, \
frequencies, squawks, taxiways, fixes and names, taxiways and fixes in the same order.
- Keep every instruction and its meaning: cleared, hold short, expect, contact, climb, descend, readback correct, \
negative, unable ...
- Add nothing: no instruction, number, approval or information that isn't in the reply.
- Say each thing once. Do not start with the callsign; it is added for you. One or two short sentences."""

REWORD_EXAMPLES: tuple[tuple[str, str], ...] = (
    ('Pilot said: "ground, DP69 at gate 12, ready to taxi with information bravo"\n'
     'Reply: "runway 06L, taxi via B, C, hold short of runway 06L"\nKeep: 06L; B; C',
     '{"reply":"taxi to runway 06L via B and C, hold short of runway 06L"}'),
    ('Pilot said: "center, N172LT, any chance of higher?"\nReply: "climb and maintain 9,000"\nKeep: 9,000',
     '{"reply":"climb and maintain 9,000"}'),
    ('Pilot said: "tower, 2LT, 5 mile final 14R"\nReply: "runway 14R, cleared to land, wind 150 at 8"\n'
     'Keep: 14R; 150; 8',
     '{"reply":"wind 150 at 8, runway 14R cleared to land"}'),
    ('Pilot said: "approach, 2LT, radio check"\nReply: "read you five by five"\nKeep: nothing',
     '{"reply":"loud and clear, five by five"}'),
)

# Words that make or change an instruction. The script's must all be kept, and none added.
INSTRUCTION = BANNED | {"hold", "short", "cross", "wait", "land", "landing", "go", "around", "left", "right", "direct",
                        "via", "follow", "behind", "report", "reduce", "increase", "speed", "vacate", "pushback", "push",
                        "start", "resume", "continue", "enter", "join", "straight", "full", "stop", "option", "touch",
                        "low", "ident", "remain", "stand", "standby"}
NEGATIONS = {"not", "no", "don't", "dont", "negative", "unable", "cannot", "can't", "never", "without"}
RUNWAY_DESIGNATOR = re.compile(r"\b(\d{1,2})([LRC])\b")
IDENT_RE = re.compile(r"\b(?=[A-Z0-9]*[A-Z])[A-Z][A-Z0-9]{0,6}\b|\b\d+[A-Z][A-Z0-9]*\b")  # KMCO, CAVVS4, B3, 4A
COMMON_IDENTS = {"ATC", "ATIS", "IFR", "VFR", "ILS", "RNAV", "GPS", "VOR", "NDB", "QNH", "I"}


def _plain(text: str) -> str:
    """Lower case, with the spellings that mean the same word made one: "take off", "take-off": "takeoff"; "24 right":
    "24r" (a runway's side isn't a turn); "pushback": "push". "Radar contact" goes: it isn't the instruction
    "contact" (and a model that adds "contact approach" to it is adding one)."""
    t = re.sub(r"\b(\d{1,2})\s+(left|right|center|centre)\b", lambda m: m.group(1) + m.group(2)[0], text.lower())
    for a, b in (("take-off", "takeoff"), ("take off", "takeoff"), ("line-up", "line up"), ("push back", "push"),
                 ("push-back", "push"), ("pushback", "push"), ("stand by", "standby"), ("centre", "center"),
                 ("radar contact", "radar")):
        t = t.replace(a, b)
    return t


def keep_line(scripted: str) -> str:
    """What the reworded reply must say as written, for the model to see: "FL360; 127.575; 3305; GOPUP4"."""
    found = re.findall(r"\bFL\s?\d{2,3}\b|\b\d{1,2}[LRC]\b|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|"
                       r"\b(?=[A-Z0-9]*[A-Z])[A-Z][A-Z0-9]{0,6}\b", scripted)
    items = [f for f in dict.fromkeys(found) if f not in COMMON_IDENTS]
    if "readback correct" in scripted.lower():
        items.insert(0, "readback correct")
    return "; ".join(items) or "nothing"


def _repeats(text: str) -> str | None:
    """Three words or more said twice ("expect runway 33R, expect runway 33R for departure"), or None."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    seen: set[tuple[str, ...]] = set()
    for i in range(len(words) - 2):
        gram = tuple(words[i:i + 3])
        if gram in seen:
            return " ".join(gram)
        seen.add(gram)
    return None


def _idents(text: str) -> set[str]:
    found = {m.group(0) for m in IDENT_RE.finditer(text)}
    return {i for i in found if not RUNWAY_DESIGNATOR.fullmatch(i) and not i.startswith("FL")} - COMMON_IDENTS


def _sequence(text: str) -> list[str]:
    """The taxiways, fixes and other idents of ``text`` in the order said, repeats included. (Runways aren't: "taxi
    via G, C to runway 06L" and "runway 06L, taxi via G, C" are the same clearance.)"""
    idents = {i.upper() for i in _idents(text)}
    return [m.group(0) for m in re.finditer(r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?![A-Za-z0-9])", text.upper())
            if m.group(0) in idents]


def _in_order(wanted: list[str], said: list[str]) -> bool:
    """``wanted`` appears in ``said`` in the same order (other things may come between)."""
    rest = iter(said)
    return all(any(token == w for token in rest) for w in wanted)


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


# Said digit by digit ("one-two-one-point-one", "two eight zero"): the same numbers as "121.1" and "280".
_DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "tree": "3", "four": "4", "five": "5", "fife": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "niner": "9"}
_DIGIT_RUN = re.compile(r"\b(?:(?:%s)(?:[\s-]+(?:point|decimal)[\s-]+|[\s-]+)){1,}(?:%s)\b" % (
    "|".join(_DIGIT_WORDS), "|".join(_DIGIT_WORDS)), re.IGNORECASE)
# Phrases whose words carry the meaning together: kept as phrases ("tail right" is not "tailwind right").
KEPT_PHRASES = ("tail right", "tail left", "hold short", "line up and wait", "cleared for takeoff", "cleared to land",
                "go around", "until established", "descend via", "climb via", "at or above", "at or below",
                "give way", "cleared for the option", "touch and go", "low approach", "readback correct", "radar contact")


def digits_from_words(text: str) -> str:
    """A model's "one-two-one point one" as "121.1", so a reply that says a number digit by digit isn't turned away
    for leaving it out."""
    def join(m: re.Match) -> str:
        out = ""
        for part in re.split(r"[\s-]+", m.group(0)):
            low = part.lower()
            out += "." if low in ("point", "decimal") else _DIGIT_WORDS.get(low, "")
        return out
    return _DIGIT_RUN.sub(join, text)


def check_reworded(raw: str, scripted: str, callsigns: tuple[str, ...]) -> str:
    """The model's words for the script's reply ``scripted``, or PhraseError: something of it lost, or added."""
    text = digits_from_words(_reply_text(raw, callsigns).replace("**", ""))
    said, got = _plain(scripted), _plain(text)
    for phrase in KEPT_PHRASES:
        if phrase in said and phrase not in got:
            raise PhraseError(f'reply leaves out "{phrase}"; keep those words as they are')
    words, want = set(re.findall(r"[a-z']+", got)), set(re.findall(r"[a-z']+", said))
    if not words:
        raise PhraseError("reply has no words")
    limit = max(MAX_WORDS, len(scripted.split()) + 8)
    if len(text.split()) > limit:
        raise PhraseError(f"reply is longer than {limit} words")
    if missing := sorted(numbers(scripted) - numbers(text)):
        raise PhraseError(f"reply leaves out {', '.join(missing)}; keep every number")
    if added := sorted(numbers(text) - numbers(scripted)):
        raise PhraseError(f"reply adds {', '.join(added)}, which the controller didn't say")
    for level in re.findall(r"\bFL\s?(\d{2,3})\b", scripted):
        if not re.search(rf"\b(?:fl\s?|flight level\s+){level}\b", got):  # "240" alone is 240 feet
            raise PhraseError(f"reply leaves out FL{level}; say it as a flight level")
    for number, side in RUNWAY_DESIGNATOR.findall(scripted):
        if not re.search(rf"(?<!\d)0?{int(number)}{side.lower()}\b", got):  # "06L", "6 left"
            raise PhraseError(f"reply leaves out runway {number}{side}")
    if lost := sorted((want & (INSTRUCTION | NEGATIONS)) - words):
        raise PhraseError(f"reply leaves out {', '.join(lost)}; keep every instruction")
    if extra := sorted((words & (INSTRUCTION | NEGATIONS)) - want):
        raise PhraseError(f"reply adds {', '.join(extra)}; say only what the controller decided")
    if lost := sorted(i for i in _idents(scripted) if not _has_word(text, i)):
        raise PhraseError(f"reply leaves out {', '.join(lost)}")  # a taxiway, a fix, a procedure
    if extra := sorted(i for i in _idents(text) if not _has_word(scripted, i)):
        raise PhraseError(f"reply adds {', '.join(extra)}, which the controller didn't say")
    if not _in_order(_sequence(scripted), _sequence(text)):
        # "via G, C" is not "via C and G": a taxi route, the fixes of a route, are in the order given.
        raise PhraseError(f"reply changes the order of {' '.join(_sequence(scripted))}; keep them in order")
    if internal := sorted(words & INTERNAL):
        raise PhraseError(f"reply talks about the prompt ({', '.join(internal)}); answer as a controller would")
    if "readback correct" in said and "readback correct" not in got.replace("read back", "readback"):
        raise PhraseError("reply leaves out readback correct; keep it")
    if (twice := _repeats(text)) is not None and _repeats(scripted) is None:
        raise PhraseError(f'reply says "{twice}" twice; say each thing once')
    return text


def reword_request(pilot: str, scripted: str) -> LlmRequest:
    messages: tuple[tuple[str, str], ...] = tuple(
        turn for user, assistant in REWORD_EXAMPLES for turn in (("user", user), ("assistant", assistant))
    ) + (("user", f'Pilot said: "{pilot}"\nReply: "{scripted}"\nKeep: {keep_line(scripted)}'),)
    return LlmRequest("reword", REWORD_SYSTEM, messages, SCHEMA, max_tokens=100)


def spoken_as(text: str, pairs: list[tuple[str, str]]) -> str:
    """``text`` for the voice: each value the script said (display, spoken) as the script says it ("5,000": "five
    thousand", "B": "Bravo"), whatever else is a number digit by digit."""
    forms: dict[str, str] = {}
    for display, said in pairs:
        if display and display != said:
            forms.setdefault(display, said)
            forms.setdefault(THOUSANDS_RE.sub("", display), said)
    alternatives = [re.escape(d) for d in sorted(forms, key=len, reverse=True)]
    runway = r"\b\d{1,2}[LRC]\b"  # "25R": "two five right", not "two five" and a letter
    pattern = re.compile(r"(?<![\w.])(?:" + "|".join(alternatives) + r")(?![\w])|" + runway + r"|\d+(?:[.,]\d+)*"
                         if alternatives else runway + r"|\d+(?:[.,]\d+)*")

    def say(m: re.Match) -> str:
        found = m.group(0)
        if found in forms:
            return forms[found]
        if re.fullmatch(r"\d{1,2}[LRC]", found):
            return speech.runway(found)
        if re.fullmatch(r"(?:2[89]|3[01])\.\d\d", found):  # an altimeter setting: "two niner eight four"
            return speech.digits(found.replace(".", ""))
        return speech.digits(THOUSANDS_RE.sub("", found))

    return pattern.sub(say, text)


class LlmPhraser:
    def __init__(self, backend: LlmBackend, *, timeout_s: float = 2.5, max_attempts: int = 2, budget_s: float = 4.0,
                 patience_s: float = 15.0, beyond_facts: bool = False,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.backend = backend
        self.beyond_facts = beyond_facts  # [llm] beyond_facts: answers may go past the sim's data (``check_reply``)
        self.timeout_s, self.max_attempts, self.budget_s = timeout_s, max_attempts, budget_s
        self.patience_s = patience_s
        self.patient = False  # set while answering after "stand by": one long try
        self._clock = clock

    def reply(self, *, pilot: str, decision: str, facts: dict[str, str], callsigns: tuple[str, ...], t: float,
              trigger: str = "", required: str = "", more: dict[str, str] | None = None,
              persona: str = "") -> tuple[Phrase | None, list[LlmExchange]]:
        """``decision`` is "answer" or "decline". Returns the checked wording, or None to use the template.
        ``required``: the data's answer, which the reply must give (``check_reply``). ``more``: the rest of what's
        known, for a model that can take it (a cloud one); a reply may use it as it may the facts."""
        beyond = self.beyond_facts
        known = {**(more or {}), **facts}
        request = replace(phrase_request(pilot, decision, facts, beyond_facts=beyond, more=more), persona=persona)
        text, exchanges = self._run(request,
                                    lambda raw: check_reply(raw, known, callsigns, required, beyond_facts=beyond),
                                    t, trigger)
        return (Phrase(text, spoken(text)) if text is not None else None), exchanges

    def reword(self, *, pilot: str, scripted: str, callsigns: tuple[str, ...], t: float,
               trigger: str = "", persona: str = "") -> tuple[str | None, list[LlmExchange]]:
        """The model's words for the script's reply ``scripted`` (without the callsign), checked; None: the
        template's words stand. ``persona``: the controller's manner (their words, never other values)."""
        request = replace(reword_request(pilot, scripted), persona=persona)
        return self._run(request, lambda raw: check_reworded(raw, scripted, callsigns), t, trigger)

    def _run(self, request: LlmRequest, check: Callable[[str], str], t: float,
             trigger: str) -> tuple[str | None, list[LlmExchange]]:
        exchanges: list[LlmExchange] = []
        timeout_s, budget_s = (self.patience_s, self.patience_s) if self.patient else \
            waits(self.backend, self.timeout_s, self.budget_s, self.patience_s)
        deadline = self._clock() + budget_s
        for attempt in range(1, self.max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining < 0.2:
                break
            result = self.backend.complete(request, timeout_s=min(timeout_s, remaining))

            def record(outcome: str, detail: str = "", _result=result, _request=request, _attempt=attempt) -> None:
                exchanges.append(LlmExchange(
                    t=t, purpose=_request.purpose, model=self.backend.model, key=_request.key(self.backend.model),
                    prompt=_request.prompt, response=_result.text or "", outcome=outcome, detail=detail, trigger=trigger,
                    latency_ms=round(_result.latency_ms, 1), attempt=_attempt,
                ))

            if result.text is None:
                record(result.error.split(":")[0] or "error", result.error)
                break
            try:
                text = check(result.text)
            except PhraseError as exc:
                record("invalid", str(exc))
                request = replace(request, messages=(
                    *request.messages, ("assistant", result.text),
                    ("user", f"That reply can't be used: {exc}. Reply with the corrected JSON only."),
                ))
                continue
            record("used")
            return text, exchanges
        return None, exchanges
