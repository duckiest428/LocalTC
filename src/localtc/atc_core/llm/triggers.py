"""When the grammar's answer isn't enough and the model gets asked.

The grammar goes first. The model is asked whenever the grammar can't confidently place the transmission
among the calls expected right now; "say again" comes only after the model couldn't either. What makes
the grammar's answer not good enough (``trigger``, in order):

- ``emergency``: always; the keywords count whatever happens, the model adds the details.
- ``question`` / ``parser_failure`` / ``ambiguous``: nothing matched, or two calls that don't go together.
- ``readback_rejected``: a readback that isn't correct (wrong, partial, or a value only nearly right).
- ``compound``: a readback with a request or question riding along ("cleared to land, actually can we do a
  low approach"): the grammar reads the readback and would drop the rest. Or a request with a question in it.
- ``self_correction`` / ``hesitation``: "sorry", "I mean", "uh", a word said twice: the words may not say
  what the pilot meant.
- ``callsign``: this flight's callsign not heard, or only nearly.
- ``non_numeric``: an altimeter given as STD or QNE.
- ``out_of_phase``: a call that makes no sense to this controller now (``readback.expected``).
- ``out_of_grammar``: a word for something the grammar has no call for ("deviation", "icing").
- ``low_confidence`` / ``digits_unsure``: speech-to-text wasn't sure of the words, or of numbers in them.

A readback the grammar finds correct is the script read back: none of this applies to it, except a
request riding along with it. In ``primary`` mode the model sees every transmission and the trigger is
still recorded, so a session log shows which ones the grammar alone would have mishandled.
"""

import itertools
import re

from localtc.atc_core.readback import Interpretation, InterpretContext
from localtc.atc_core.readback.callsign_check import judge as judge_callsign
from localtc.atc_core.readback.expected import is_expected
from localtc.atc_core.readback.extract import _has_any
from localtc.atc_core.readback.intents import EMERGENCY
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.readback.questions import asks, is_question, question_topic

__all__ = ["REASONS", "is_question", "question_topic", "trigger"]

# Words that mean the pilot wants something the grammar has no intent for.
OFF_SCRIPT = {"request", "requesting", "unable", "negative", "direct", "higher", "lower", "deviation", "deviate", "deviating",
              "turbulence", "chop", "icing", "vectors", "shortcut", "delay", "problem", "medical", "sick", "passenger",
              "souls", "fuel", "smoke", "fire", "failure", "diverting", "divert", "return", "returning", "missed"}
# Grammar intents whose own phrases use one of those words ("request taxi").
OWNS_WORD = {"ready_to_taxi": {"request"}, "request_taxi_parking": {"request"}, "request_ifr_clearance": {"request"},
             "request_crossing": {"request", "requesting"}, "request_altitude": {"request", "requesting", "higher", "lower"},
             "request_direct": {"request", "requesting", "direct"}, "request_vectors": {"request", "requesting", "vectors"},
             "request_runway": {"request", "requesting"}, "request_return": {"request", "requesting", "return", "returning"},
             "request_diversion": {"request", "requesting", "divert", "diverting"}, "going_around": {"missed"},
             "report_conditions": {"turbulence", "chop", "icing"}, "report_problem": {"problem", "failure"}}

REASONS = ("emergency", "question", "parser_failure", "ambiguous", "readback_rejected", "compound", "self_correction",
           "hesitation", "callsign", "non_numeric", "out_of_phase", "out_of_grammar", "low_confidence", "digits_unsure")
LOW_CONFIDENCE = 0.5  # speech-to-text confidence below which the words themselves are in doubt
DIGITS_CONFIDENCE = 0.7  # ... and below which the numbers in them are (digits are what it gets wrong most)

FILLERS = {"uh", "um", "er", "erm", "ah", "uhh", "umm", "hmm", "eh"}
SELF_CORRECTIONS = (("sorry",), ("correction",), ("i", "mean"), ("actually",), ("scratch", "that"), ("no", "wait"),
                    ("wait",), ("excuse", "me"), ("disregard",))
NON_NUMERIC = {"std", "qne", "standard"}


def trigger(grammar: Interpretation, text: str, confidence: float | None = None,
            low_confidence: float = LOW_CONFIDENCE, context: InterpretContext | None = None) -> str | None:
    """Why the model should look at this transmission, or None if the grammar handled it."""
    tokens = normalize(text)
    words = [t.text for t in tokens if t.kind == "word"]
    correct_readback = grammar.kind == "readback" and grammar.status == "correct"
    if grammar.intent == EMERGENCY:
        return "emergency"
    if correct_readback:
        # The script, read back. Only something riding along with it that the grammar couldn't place is worth
        # the model's time.
        return "compound" if grammar.asked_more else None
    if grammar.kind == "request" and grammar.values.get("asks") and asks(text):
        # A request and a question in one call ("ready to copy, and what's the weather at Orlando?"): the grammar
        # would answer the question by its keyword. (A topic word alone isn't asking: "..., altimeter standard".)
        return "compound"
    if grammar.kind == "request" and grammar.intent == "question" and grammar.values.get("topic", "other") != "other":
        return None  # the grammar knows the topic; ATC answers from the sim's data, or words it (engine._answer)
    if is_question(text) and (grammar.kind == "unknown" or grammar.intent in (None, "acknowledge", "question")):
        return "question"  # ("can we get taxi?" is the grammar's to answer: it knows that one)
    if grammar.kind == "unknown":
        return "parser_failure"
    if grammar.needs_fallback:
        return "ambiguous"
    if grammar.kind == "readback":
        return "readback_rejected"
    if _has_any(tokens, *SELF_CORRECTIONS):
        return "self_correction"
    said = re.findall(r"[a-z']+", text.lower())  # (normalizing drops the fillers)
    if set(said) & FILLERS or any(a == b and a not in ("zero", "0") for a, b in itertools.pairwise(words)):
        return "hesitation"
    if context is not None and context.callsign is not None and grammar.intent not in ("acknowledge", None) \
            and (verdict := judge_callsign(tokens, context.callsign)) != "ours" \
            and (verdict == "close" or not grammar.callsign_heard):
        return "callsign"
    if set(words) & NON_NUMERIC:
        return "non_numeric"
    if context is not None and not is_expected(grammar.intent, context.station_role, context.phase):
        return "out_of_phase"
    off = (set(words) & OFF_SCRIPT) - OWNS_WORD.get(grammar.intent or "", set())
    if off:
        return "out_of_grammar"
    if confidence is not None and confidence < low_confidence:
        return "low_confidence"  # the grammar found something, but the words may be misheard
    if confidence is not None and confidence < DIGITS_CONFIDENCE and any(t.kind == "number" for t in tokens):
        return "digits_unsure"
    return None
