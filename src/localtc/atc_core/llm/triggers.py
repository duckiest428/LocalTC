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
QUESTION_TOPICS = {"altimeter", "wind", "winds", "weather", "metar", "time", "visibility", "ceiling", "active",
                   "baro", "barometer", "qnh", "barrow"}  # "barrow": what speech-to-text makes of "baro"
# Words that mean the pilot wants something the grammar has no intent for.
OFF_SCRIPT = {"request", "requesting", "unable", "negative", "direct", "higher", "lower", "deviation", "deviate", "deviating",
              "turbulence", "chop", "icing", "vectors", "shortcut", "delay", "problem", "medical", "sick", "passenger",
              "souls", "fuel", "smoke", "fire", "failure", "diverting", "divert", "return", "returning", "missed"}
# Grammar intents whose own phrases use one of those words ("request taxi").
OWNS_WORD = {"ready_to_taxi": {"request"}, "request_taxi_parking": {"request"}, "request_ifr_clearance": {"request"}}

REASONS = ("emergency", "question", "parser_failure", "ambiguous", "readback_rejected", "out_of_grammar",
           "low_confidence")
LOW_CONFIDENCE = 0.5  # speech-to-text confidence below which the words themselves are in doubt


def is_question(text: str) -> bool:
    words = [t.text for t in normalize(text) if t.kind == "word"]
    if "?" in text or (words and words[0] in QUESTION_OPENERS):
        return True
    if "frequency" in words and not any(t.kind == "number" and len(t.text.replace(".", "")) >= 3 for t in normalize(text)):
        return True  # "request frequency for tower"; a handoff readback has the number in it (a callsign's "69" is not one)
    if "say" in words:  # "say altimeter", but not "say again"
        after = words[words.index("say") + 1 : words.index("say") + 2]
        return bool(after) and after[0] != "again"
    # "request altimeter", "request the current baro": asking for a topic straight out is a question,
    # even though "request" usually means the pilot wants something done rather than told.
    for opener in ("request", "requesting"):
        if opener in words:
            after = words[words.index(opener) + 1 : words.index(opener) + 3]
            if set(after) & QUESTION_TOPICS:
                return True
    # A topic word alone ("weather") makes a question, unless the pilot is asking for something
    # ("request deviation for weather") or reading back ("runway", "squawk").
    return bool(set(words) & QUESTION_TOPICS) and not set(words) & {"cleared", "maintain", "squawk", "runway",
                                                                     "request", "requesting"}


def trigger(grammar: Interpretation, text: str, confidence: float | None = None,
            low_confidence: float = LOW_CONFIDENCE) -> str | None:
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
    if confidence is not None and confidence < low_confidence:
        return "low_confidence"  # the grammar found something, but the words may be misheard
    return None


# Question topics recognisable from a single word; used when the model can't classify a question.
TOPIC_WORDS = {"altimeter": "altimeter", "baro": "altimeter", "barometer": "altimeter", "qnh": "altimeter",
               "barrow": "altimeter", "wind": "wind", "winds": "wind", "weather": "weather", "metar": "weather",
               "runway": "runway", "squawk": "squawk", "code": "squawk", "altitude": "altitude", "frequency": "frequency",
               "atis": "atis", "information": "atis"}


def question_topic(text: str) -> str | None:
    """The topic of a question from its words, or None."""
    if not is_question(text):
        return None
    for token in normalize(text):
        if token.kind == "word" and token.text in TOPIC_WORDS:
            return TOPIC_WORDS[token.text]
    return None
