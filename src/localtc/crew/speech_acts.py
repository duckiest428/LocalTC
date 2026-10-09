"""What the captain's words on the intercom are, before anything is done about them.

The copilot used to look for a command in whatever it heard, and act on it: "Autopilot, bro, we weren't ready to taxi"
(Whisper's "copilot") was answered "Copy, autopilot off", and "did you set the gear?" or a half-heard "gear down" in a
sentence about something else would have moved the gear. Now every utterance is first read as one kind of thing:

- ``command``: asking for something to be done ("flaps two", "go ahead and set QNH 1020", "can you put the gear down").
- ``question``: asking ("did you set the gear?", "is the autopilot on?", "what's our fuel?"). Never done, answered.
- ``report``: the captain saying what they did, do or will do, or how things are ("I'll put flaps two", "engines
  stabilized", "we weren't ready to taxi"). Never done; acknowledged.
- ``correction``: "no, flaps two", "I said heading 240", "not 1020, 1012": replaces what's waiting for a yes.
- ``negation``: "don't", "never mind", "cancel that", "no, don't go around": stops what's waiting, never does anything.
- ``acknowledgement``: "check", "roger", "copy": nothing to answer.
- ``answer``: yes or no to the copilot's question.
- ``radio``: a radio call said on the intercom key ("Seattle Tower, Alaska 123 ..."): not sent unless the captain says.
- ``chat``: anything else: conversation, for the copilot's facts or its model.
- ``unclear``: nothing usable (silence, noise, speech-to-text going round in circles: "3,000. 3,000. 3,000. ...").

How sure the copilot is of the words (``certainty``) comes from speech-to-text's confidence: typed is sure; spoken is
"high" from ``SURE``, "medium" from ``UNSURE``, "low" below it. What to do with a command given how sure it is and how
much is at stake is the Pilot Monitoring's (``crew.pm``), with ``risk`` from here.
"""

import re
from dataclasses import dataclass

from localtc.crew.commands import ACK_WORDS, Command, parse

ACTS = ("command", "question", "report", "correction", "negation", "acknowledgement", "answer", "radio", "chat",
        "unclear")
SURE = 0.75  # speech-to-text this confident (or typed): the words are taken as heard
UNSURE = 0.55  # under this: the words are a guess

# "Can you / could you / would you / will you" + doing something is asking for it, not a question about it.
POLITE = re.compile(r"^(?:\w+,?\s+){0,2}(?:can|could|would|will) you (?:please |just |go ahead and )?(?:set|put|turn|give|"
                    r"bring|select|arm|disarm|move|get|drop|raise|retract|extend|tune|dial|swap|engage|disengage|"
                    r"switch|pull|push|run|read|squawk|release|start|stop|request|call|ask|lower|send)\b")
QUESTION = re.compile(r"^(?:(?:hey|so|and|ok|okay|alright|well|uh|um|copilot|co-pilot|wait)[,\s]+)*(?:what|what's|whats|how|"
                      r"how's|when|where|where's|which|who|why|is|isn't|are|aren't|am|do|does|did|didn't|have|has|"
                      r"haven't|hasn't|was|were|should|shall|could we|can we|will we|would we|any)\b")
# The captain about themselves, or about how things are: "I'll put flaps two", "we've captured the glideslope".
REPORT_START = re.compile(r"^(?:(?:ok|okay|alright|right|so|and|well|yeah|yep)[,\s]+)*(?:i|i'll|i've|i'm|i'd|we|we're|we've|"
                          r"we'll|we'd|we were|we are|it's|its|that's|thats|looks like|there's|there is)\b")
REPORT_END = re.compile(r"\b(?:three green|green|stabili[sz]ed|captured|alive|checked|check|set|is set|are set|on|"
                        r"armed|engaged|done|complete|completed)[\s.!]*$")
NEGATION = re.compile(r"\b(?:don't|do not|dont|never ?mind|cancel that|belay that|disregard|not yet|hold off|leave it|"
                      r"stop that|forget (?:it|that)|no,? no\b|negative,? don't)\b")
CORRECTION = re.compile(r"^(?:no|negative|nope|correction|sorry|i said|i meant|i mean|actually|not)\b[,.\s]*")
YES = re.compile(r"^(?:yes|yeah|yep|yup|affirm(?:ative)?|correct|confirm(?:ed)?|do it|go ahead|go for it|please do|"
                 r"sure|that's right|thats right|right|ok do it|okay do it)\b[\s,.!]*(?:please|thanks)?[\s.!]*$")
NO = re.compile(r"^(?:no|nope|negative|no thanks|not now|don't|leave it|cancel|never ?mind)\b[\s,.!]*(?:thanks)?[\s.!]*$")
ACK_ONLY = re.compile(r"^(?:(?:check(?:ed)?|copy(?: that)?|roger(?: that)?|ok(?:ay)?|yep|yup|yeah|got it|gotcha|alright|"
                      r"all right|noted|thanks|thank you|cool|good|sounds good|understood|right|nice|great)[\s,.!]*)+$")
# What speech-to-text writes for silence or noise.
NOISE = re.compile(r"^(?:thank you\.?|thanks for watching!?|you|bye\.?|\.+|uh+|um+|hmm+)$")

# What's at stake in each command: "low" done when heard well enough, "medium" read back first when the words are
# unsure (or the number disagrees with ATC's), "high" always confirmed. The safety rules (crew.actions.safety) still
# decide whether it's done at all.
LOW = {"light", "com_standby", "verbosity", "checklist", "brief", "status", "check", "say_again", "altimeter"}
HIGH = {"go_around", "emergency"}
NUMERIC = {"heading", "altitude", "speed", "vs", "squawk", "com_active", "com_standby", "altimeter"}
CONTROL = {"verbosity", "checklist", "brief", "status", "check", "say_again", "yes", "no"}


@dataclass(frozen=True)
class Reading:
    act: str  # one of ACTS
    commands: tuple[Command, ...] = ()
    certainty: str = "high"  # high, medium, low
    why: str = ""  # what it was read from, for the record


def certainty(confidence: float | None) -> str:
    if confidence is None:
        return "high"  # typed
    return "high" if confidence >= SURE else "medium" if confidence >= UNSURE else "low"


def risk(cmd: Command, *, airborne: bool) -> str:
    """How much is at stake in ``cmd``: low, medium or high."""
    a, v = cmd.action, cmd.value
    if a in HIGH:
        return "high"
    if a == "autopilot" and v == "off" and airborne:
        return "high"
    if a == "spoilers" and v == "extend" and airborne:
        return "high"
    if a == "squawk" and v in ("7500", "7600", "7700"):
        return "high"
    if a == "radio":
        return "medium"
    if a in LOW or (a == "spoilers" and v in ("arm", "disarm")):
        return "low"
    return "medium"


def looping(text: str) -> bool:
    """Speech-to-text going round in circles on noise: the same word or few words four times or more in a row."""
    words = re.findall(r"[a-z0-9,']+", text.lower())
    for size in (1, 2, 3):
        for start in range(len(words)):
            chunk = words[start:start + size]
            if len(chunk) < size:
                break
            repeats = 1
            j = start + size
            while words[j:j + size] == chunk:
                repeats += 1
                j += size
            if repeats >= 4:
                return True
    return False


def read(text: str, confidence: float | None = None, *, radio_call: bool = False) -> Reading:
    """What ``text`` is (``Reading``). ``radio_call``: it sounds like a radio call (the PM knows the stations)."""
    sure = certainty(confidence)
    lowered = " ".join(text.lower().replace("’", "'").split())
    bare = lowered.strip(" .!?,")
    if not bare or NOISE.match(bare) or looping(lowered):
        return Reading("unclear", certainty="low", why="no words, or the same ones over and over")
    if radio_call:
        return Reading("radio", certainty=sure, why="a station or the callsign first")
    commands = tuple(parse(text))
    if (corrected := CORRECTION.match(bare)) and len(bare) > corrected.end():
        commands = tuple(parse(bare[corrected.end():])) or commands  # "no, flaps two": what's said after the "no"
    actions = [c for c in commands if c.action not in ("yes", "no")]
    if YES.match(bare):
        return Reading("answer", (Command("yes"),), sure, "yes")
    if NO.match(bare) and not actions:
        return Reading("answer", (Command("no"),), sure, "no")
    asked = "?" in text or QUESTION.match(bare)
    if asked and not actions and not POLITE.search(lowered):
        return Reading("question", (), sure, "a question")  # "which days do you not like?" isn't a "don't"
    if NEGATION.search(lowered) and not POLITE.search(lowered):
        # "No, no, no, don't go around": whatever's named is what's not wanted.
        return Reading("negation", tuple(actions), sure, "don't / never mind")
    if ACK_ONLY.match(bare):
        return Reading("acknowledgement", certainty=sure)
    if any(c.action == "check" for c in actions):
        return Reading("command", (Command("check"),), sure, "an intercom check")
    if any(c.action == "say_again" for c in actions):
        return Reading("command", (Command("say_again"),), sure, "say again")
    control = [c for c in actions if c.action in CONTROL]
    if control and not asked:
        return Reading("command", tuple(control), sure, "a request to the copilot itself")
    if asked and not POLITE.search(lowered):
        return Reading("question", tuple(actions), sure, "a question")
    if CORRECTION.match(bare) and actions:
        return Reading("correction", tuple(actions), sure, "no / I said / not that, this")
    if actions and _reported(lowered, actions):
        return Reading("report", tuple(actions), sure, "the captain's own, or how things are")
    if actions:
        return Reading("command", tuple(actions), sure, "asked for")
    if REPORT_START.match(bare) or REPORT_END.search(bare):
        return Reading("report", certainty=sure, why="a statement")
    return Reading("chat", certainty=sure)


def _reported(lowered: str, actions: list[Command]) -> bool:
    """Words with a command in them that say what the captain does or how things are: "I'll put flaps two", "gear
    down, three green", "flaps one, check". A request in them ("go ahead and", "please", "let's") makes it a command."""
    if re.search(r"\b(?:go ahead and|please|let's|lets|can you|could you|would you|set|put|give me|select)\b", lowered) \
            and not re.match(r"^(?:\w+[,\s]+)?(?:i|i'll|i'm|i've|we've|we're)\b", lowered):
        return False
    if re.match(r"^(?:(?:ok|okay|alright|right|so|and|well|yeah|yep)[,\s]+)*(?:i|i'll|i've|i'm|we've|we're|we'll)\b", lowered):
        return True
    last = re.findall(r"[a-z']+", lowered)[-1:] or [""]
    return last[0] in ACK_WORDS or bool(re.search(r"\bthree green\b|\bis (?:set|down|up|on|off|armed)\b", lowered))
