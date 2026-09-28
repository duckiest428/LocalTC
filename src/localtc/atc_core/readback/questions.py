"""Questions the grammar answers without the language model: "any idea what our departure runway will be?",
"request updated altimeter", "say the winds".

ATC answers these from the sim's data (the engine's ``_answer``), so knowing the topic is all it takes. The
model is slow with the sim running, and a question it would only have sorted into the same topic is one
the pilot waits for for nothing; so is one it gets wrong and nobody answers.
"""

from localtc.atc_core.readback.extract import _find_phrase, _has_any
from localtc.atc_core.readback.normalize import Token, normalize

QUESTION_OPENERS = {"what", "what's", "whats", "which", "when", "where", "how", "who", "why", "is", "are", "can", "could",
                    "may", "do", "does", "did", "will", "would", "confirm", "any"}
QUESTION_TOPICS = {"altimeter", "wind", "winds", "weather", "metar", "time", "visibility", "ceiling", "active",
                   "baro", "barometer", "qnh", "barrow"}  # "barrow": what speech-to-text makes of "baro"
# Asking, wherever it comes in the call: "..., and can we also get an updated altimeter?"
QUESTION_CUES = (("what",), ("what's",), ("whats",), ("which",), ("any", "idea"), ("do", "you", "have"),
                 ("do", "you", "know"), ("can", "we", "get"), ("could", "we", "get"), ("can", "i", "get"),
                 ("could", "i", "get"), ("can", "we", "have"), ("could", "we", "have"), ("say",), ("request",),
                 ("requesting",), ("updated",), ("current",), ("latest",))

# Question topics recognisable from a single word.
TOPIC_WORDS = {"altimeter": "altimeter", "altimeters": "altimeter", "altimator": "altimeter", "baro": "altimeter",
               "barometer": "altimeter", "qnh": "altimeter", "barrow": "altimeter", "wind": "wind", "winds": "wind",
               "weather": "weather", "metar": "weather", "runway": "runway", "squawk": "squawk", "code": "squawk",
               "altitude": "altitude", "frequency": "frequency", "atis": "atis", "information": "atis"}
# ... and from two, the way speech-to-text breaks "altimeter" up: "alt-terminator", "all timer".
TOPIC_PHRASES = {("alt", "terminator"): "altimeter", ("all", "timer"): "altimeter", ("alti", "meter"): "altimeter",
                 ("alt", "meter"): "altimeter", ("ultimate", "er"): "altimeter"}


def is_question(text: str) -> bool:
    tokens = normalize(text)
    words = [t.text for t in tokens if t.kind == "word"]
    if "?" in text or (words and words[0] in QUESTION_OPENERS):
        return True
    if "frequency" in words and not any(t.kind == "number" and len(t.text.replace(".", "")) >= 3 for t in tokens):
        return True  # "request frequency for tower"; a handoff readback has the number in it (a callsign's "69" is not one)
    if "say" in words:  # "say altimeter", but not "say again"
        after = words[words.index("say") + 1 : words.index("say") + 2]
        return bool(after) and after[0] != "again"
    # "request altimeter", "request the current baro": asking for a topic straight out is a question,
    # even though "request" usually means the pilot wants something done rather than told.
    if _topic(tokens) == "altimeter" and _has_any(tokens, *QUESTION_CUES):
        return True
    for opener in ("request", "requesting"):
        if opener in words:
            after = words[words.index(opener) + 1 : words.index(opener) + 3]
            if set(after) & QUESTION_TOPICS:
                return True
    # A topic word alone ("weather") makes a question, unless the pilot is asking for something
    # ("request deviation for weather") or reading back ("runway", "squawk").
    return bool(set(words) & QUESTION_TOPICS) and not set(words) & {"cleared", "maintain", "squawk", "runway",
                                                                     "request", "requesting"}


def asks(text: str) -> bool:
    """Asking in so many words: a question mark, "any idea", "can we get", "what". (Not just a topic word:
    "frequency change approved" is an acknowledgement.)"""
    tokens = normalize(text)
    words = [t.text for t in tokens if t.kind == "word"]
    return "?" in text or (bool(words) and words[0] in QUESTION_OPENERS) or _has_any(tokens, *QUESTION_CUES)


def question_topic(text: str) -> str | None:
    """The topic of a question from its words, or None. The topic asked about is the one after the asking:
    "short final runway 32, and can we get the altimeter?" asks about the altimeter."""
    if not is_question(text):
        return None
    tokens = normalize(text)
    cue = min((end - len(phrase) for phrase in QUESTION_CUES for end in _find_phrase(tokens, phrase)), default=None)
    if cue is not None and (topic := _topic(tokens[cue:])) is not None:
        return topic
    return _topic(tokens)


def _topic(tokens: list[Token]) -> str | None:
    for i, token in enumerate(tokens):
        pair = (token.text, tokens[i + 1].text) if i + 1 < len(tokens) else None
        if pair in TOPIC_PHRASES:
            return TOPIC_PHRASES[pair]
        if token.kind == "word" and token.text in TOPIC_WORDS:
            return TOPIC_WORDS[token.text]
    return None
