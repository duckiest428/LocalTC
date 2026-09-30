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

SYSTEM = """You word one short reply for an air traffic controller, in standard radio phraseology. \
The controller has already decided what to say; you only put it into words.
- You are the controller named in the facts: answer as that station would.
- Use only the facts given. Never invent numbers, names or information.
- Never give or approve an instruction: no clearances, altitudes, headings, frequencies to contact, squawk codes \
or taxi routes, and never say "approved". If the pilot asks for something like that, say "unable" and, if it \
fits, "continue as filed".
- Answer what the pilot asked. If the facts answer only part of it, give that part and say the rest isn't \
available (you have no weather reports along the route or at other airports unless the facts say so). If \
they answer none of it, say "unable, information not available".
- At most 20 words. Do not start with the callsign; it is added for you."""

SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
          "additionalProperties": False}

EXAMPLES: tuple[tuple[str, str], ...] = (
    ('Pilot asked: "any weather to report at quebec"\nDecision: answer\nFacts: controller Montreal Center; Quebec wind 240@8; '
     'Quebec altimeter 29.92',
     '{"reply":"Quebec wind 240 at 8, altimeter 29.92"}'),
    ('Pilot asked: "request direct Quebec"\nDecision: decline\nFacts: controller Montreal Center; destination Quebec; phase cruise',
     '{"reply":"unable direct at this time, continue as filed"}'),
    ('Pilot asked: "any bad weather on the way to Phoenix today"\nDecision: answer\nFacts: controller San Diego Ground; '
     'destination Phoenix; wind 270@6; altimeter 29.98; here and now: wind 270 at 6, altimeter 29.98',
     '{"reply":"no weather reports along your route available, San Diego wind 270 at 6, altimeter 29.98"}'),
    ('Pilot asked: "how long until we get there"\nDecision: answer\nFacts: controller Seattle Approach; runway in use 16L',
     '{"reply":"unable, information not available"}'),
)

BANNED = {"cleared", "clear", "climb", "descend", "maintain", "turn", "heading", "contact", "squawk", "taxi", "approved",
          "proceed", "vectors", "expect", "line", "takeoff", "monitor", "identify"}
# The prompt's own words ("phase arrival in effect") and navaids it wasn't told about ("ILS not available").
INTERNAL = {"phase", "facts", "fact", "decision", "effect"}
NAVAIDS = {"ils", "rnav", "localizer", "glideslope", "vor", "ndb", "gps"}
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
    return text


def check_reply(raw: str, facts: dict[str, str], callsigns: tuple[str, ...], required: str = "") -> str:
    """The model's reply, checked against the facts. ``required``: the data's own answer to the question (the facts'
    "here and now"): a reply that gives other numbers than its own, or another runway, answers something else."""
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
    fact_words = set(re.findall(r"[a-z]+", " ".join(facts.values()).lower()))
    if navaids := sorted(set(words) & NAVAIDS - fact_words):
        raise PhraseError(f"reply mentions {', '.join(navaids)}, which is not in the facts")
    known = numbers(" ".join(facts.values()))
    if invented := [n for n in NUMBER_RE.findall(THOUSANDS_RE.sub("", text)) if _number(n) not in known]:
        raise PhraseError(f"reply has numbers that are not in the facts: {', '.join(invented)}")
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


def phrase_request(pilot: str, decision: str, facts: dict[str, str]) -> LlmRequest:
    """The request for a reply's wording: the examples, then this call."""
    messages: tuple[tuple[str, str], ...] = tuple(
        turn for user, assistant in EXAMPLES for turn in (("user", user), ("assistant", assistant))
    ) + (("user", user_message(pilot, decision, facts)),)
    return LlmRequest("phrase", SYSTEM, messages, SCHEMA, max_tokens=80)


# --- rewording the script's replies -------------------------------------------------------------------------------

REWORD_SYSTEM = """You are an air traffic controller on the radio. The controller's reply is decided; say it \
the way a real controller would, in standard radio phraseology, fitting what the pilot said.
- Keep every number, runway, altitude, heading, frequency, squawk, taxiway, fix and name exactly as given, \
taxiways and fixes in the same order, and a flight level as "FL240".
- Keep every instruction and its meaning: cleared, hold short, expect, contact, climb, descend, unable ...
- Add nothing: no instruction, number, approval or information that isn't in the reply.
- Do not start with the callsign; it is added for you. One or two short sentences."""

REWORD_EXAMPLES: tuple[tuple[str, str], ...] = (
    ('Pilot said: "ground, DP69 at gate 12, ready to taxi with information bravo"\n'
     'Reply: "runway 06L, taxi via B, C, hold short of runway 06L"',
     '{"reply":"taxi to runway 06L via B and C, hold short of runway 06L"}'),
    ('Pilot said: "center, N172LT, any chance of higher?"\nReply: "climb and maintain 9,000"',
     '{"reply":"climb and maintain 9,000"}'),
    ('Pilot said: "tower, 2LT, 5 mile final 14R"\nReply: "runway 14R, cleared to land, wind 150 at 8"',
     '{"reply":"wind 150 at 8, runway 14R cleared to land"}'),
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
    "24r" (a runway's side isn't a turn)."""
    t = re.sub(r"\b(\d{1,2})\s+(left|right|center|centre)\b", lambda m: m.group(1) + m.group(2)[0], text.lower())
    for a, b in (("take-off", "takeoff"), ("take off", "takeoff"), ("line-up", "line up"), ("push back", "pushback"),
                 ("push-back", "pushback"), ("stand by", "standby"), ("centre", "center")):
        t = t.replace(a, b)
    return t


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


def check_reworded(raw: str, scripted: str, callsigns: tuple[str, ...]) -> str:
    """The model's words for the script's reply ``scripted``, or PhraseError: something of it lost, or added."""
    text = _reply_text(raw, callsigns)
    said, got = _plain(scripted), _plain(text)
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
    return text


def reword_request(pilot: str, scripted: str) -> LlmRequest:
    messages: tuple[tuple[str, str], ...] = tuple(
        turn for user, assistant in REWORD_EXAMPLES for turn in (("user", user), ("assistant", assistant))
    ) + (("user", f'Pilot said: "{pilot}"\nReply: "{scripted}"'),)
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
    pattern = re.compile(r"(?<![\w.])(?:" + "|".join(alternatives) + r")(?![\w])" + r"|\d+(?:[.,]\d+)*" if alternatives
                         else r"\d+(?:[.,]\d+)*")

    def say(m: re.Match) -> str:
        found = m.group(0)
        return forms.get(found) or speech.digits(THOUSANDS_RE.sub("", found))

    return pattern.sub(say, text)


class LlmPhraser:
    def __init__(self, backend: LlmBackend, *, timeout_s: float = 2.5, max_attempts: int = 2, budget_s: float = 4.0,
                 patience_s: float = 15.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.backend = backend
        self.timeout_s, self.max_attempts, self.budget_s = timeout_s, max_attempts, budget_s
        self.patience_s = patience_s
        self.patient = False  # set while answering after "stand by": one long try
        self._clock = clock

    def reply(self, *, pilot: str, decision: str, facts: dict[str, str], callsigns: tuple[str, ...], t: float,
              trigger: str = "", required: str = "") -> tuple[Phrase | None, list[LlmExchange]]:
        """``decision`` is "answer" or "decline". Returns the checked wording, or None to use the template.
        ``required``: the data's answer, which the reply must give (``check_reply``)."""
        text, exchanges = self._run(phrase_request(pilot, decision, facts),
                                    lambda raw: check_reply(raw, facts, callsigns, required), t, trigger)
        return (Phrase(text, spoken(text)) if text is not None else None), exchanges

    def reword(self, *, pilot: str, scripted: str, callsigns: tuple[str, ...], t: float,
               trigger: str = "") -> tuple[str | None, list[LlmExchange]]:
        """The model's words for the script's reply ``scripted`` (without the callsign), checked; None: the
        template's words stand."""
        return self._run(reword_request(pilot, scripted), lambda raw: check_reworded(raw, scripted, callsigns), t, trigger)

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
