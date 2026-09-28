"""When the grammar's answer isn't enough and the model gets asked.

In ``fallback`` mode these are the only transmissions the model sees. In ``primary``
mode it sees every one, and the trigger is still recorded, so a session log shows
which transmissions the grammar alone would have mishandled.
"""

from localtc.atc_core.readback import Interpretation
from localtc.atc_core.readback.intents import EMERGENCY
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.readback.questions import is_question, question_topic

__all__ = ["REASONS", "is_question", "question_topic", "trigger"]

# Words that mean the pilot wants something the grammar has no intent for.
OFF_SCRIPT = {"request", "requesting", "unable", "negative", "direct", "higher", "lower", "deviation", "deviate", "deviating",
              "turbulence", "chop", "icing", "vectors", "shortcut", "delay", "problem", "medical", "sick", "passenger",
              "souls", "fuel", "smoke", "fire", "failure", "diverting", "divert", "return", "returning", "missed"}
# Grammar intents whose own phrases use one of those words ("request taxi").
OWNS_WORD = {"ready_to_taxi": {"request"}, "request_taxi_parking": {"request"}, "request_ifr_clearance": {"request"}}

REASONS = ("emergency", "question", "parser_failure", "ambiguous", "readback_rejected", "out_of_grammar",
           "low_confidence")
LOW_CONFIDENCE = 0.5  # speech-to-text confidence below which the words themselves are in doubt


def trigger(grammar: Interpretation, text: str, confidence: float | None = None,
            low_confidence: float = LOW_CONFIDENCE) -> str | None:
    """Why the model should look at this transmission, or None if the grammar handled it."""
    if grammar.intent == EMERGENCY:
        return "emergency"
    if grammar.kind == "request" and grammar.intent == "question" and grammar.values.get("topic", "other") != "other":
        return None  # the grammar knows what's asked, and ATC answers it from the sim's data
    if is_question(text) and (grammar.kind == "unknown" or grammar.intent in (None, "acknowledge", "question")):
        return "question"  # ("can we get taxi?" is the grammar's to answer: it knows that one)
    if grammar.kind == "unknown":
        return "parser_failure"
    if grammar.needs_fallback:
        return "ambiguous"
    if grammar.kind == "readback" and grammar.status != "correct":
        return "readback_rejected"
    words = {t.text for t in normalize(text) if t.kind == "word"}
    off = (words & OFF_SCRIPT) - OWNS_WORD.get(grammar.intent or "", set())
    if off:
        return "out_of_grammar"
    if confidence is not None and confidence < low_confidence:
        return "low_confidence"  # the grammar found something, but the words may be misheard
    return None
