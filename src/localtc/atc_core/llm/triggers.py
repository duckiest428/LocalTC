"""When the grammar's answer isn't enough and the model gets asked.

In ``fallback`` mode these are the only transmissions the model sees. In ``primary``
mode it sees every one, and the trigger is still recorded, so a session log shows
which transmissions the grammar alone would have mishandled.
"""

from localtc.atc_core.readback import Interpretation
from localtc.atc_core.readback.intents import EMERGENCY
from localtc.atc_core.readback.normalize import normalize

QUESTION_OPENERS = {"what", "what's", "whats", "which", "when", "where", "how", "who", "why", "is", "are", "can", "could",
                    "may", "do", "does", "did", "will", "would", "confirm"}
QUESTION_TOPICS = {"altimeter", "wind", "winds", "weather", "metar", "time", "visibility", "ceiling", "active"}
# Words that mean the pilot wants something the grammar has no intent for.
OFF_SCRIPT = {"request", "requesting", "unable", "negative", "direct", "higher", "lower", "deviation", "deviate", "deviating",
              "turbulence", "chop", "icing", "vectors", "shortcut", "delay", "problem", "medical", "sick", "passenger",
              "souls", "fuel", "smoke", "fire", "failure", "diverting", "divert", "return", "returning", "missed"}
# Grammar intents whose own phrases use one of those words ("request taxi").
OWNS_WORD = {"ready_to_taxi": {"request"}, "request_taxi_parking": {"request"}, "request_ifr_clearance": {"request"}}

REASONS = ("emergency", "question", "parser_failure", "ambiguous", "readback_rejected", "out_of_grammar")


def is_question(text: str) -> bool:
    words = [t.text for t in normalize(text) if t.kind == "word"]
    if "?" in text or (words and words[0] in QUESTION_OPENERS):
        return True
    if "say" in words:  # "say altimeter", but not "say again"
        after = words[words.index("say") + 1 : words.index("say") + 2]
        return bool(after) and after[0] != "again"
    return bool(set(words) & QUESTION_TOPICS) and not set(words) & {"cleared", "maintain", "squawk", "runway"}


def trigger(grammar: Interpretation, text: str) -> str | None:
    """Why the model should look at this transmission, or None if the grammar handled it."""
    if grammar.intent == EMERGENCY:
        return "emergency"
    if is_question(text):
        return "question"
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
    return None
