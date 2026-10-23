"""People on the radio, not scripts: now and then a controller says "uh" while they look at the strip, and the other
flights' pilots more often ("uh, Approach, Delta 1538, with you, one-two thousand").

Only the spoken words change (the radio log keeps the clean text), and only around the instruction: never inside a
number, a runway, a heading or a frequency, never in a short call, and never in anything urgent (a go-around, "stop",
a traffic alert, a correction): those are said straight. How often goes by the controller (``personality``: a
conversational one more, a strict or formal one hardly at all) and halves on a busy frequency. Rare enough to sound
like a person, never so often it sounds unprofessional. Decided by the words and a key (the time), so a replay says
it the same way.
"""

import random
import re
import zlib

# How often a controller of each kind (personality.KINDS) hesitates in a transmission; a busy frequency halves it.
ATC_RATE = {"calm": 0.14, "formal": 0.05, "friendly": 0.2, "strict": 0.04, "hurried": 0.06, "dry": 0.1,
            "conversational": 0.25}
PILOT_RATE = 0.3  # the other flights' pilots: they hesitate more than the controllers do
TWICE = 0.12  # ... and now and then twice in one call
ATC_FILLERS = ("uh",) * 7 + ("um",)
PILOT_FILLERS = ("uh",) * 6 + ("um", "ah")
FILLERS = {"uh", "um", "ah"}
MIN_WORDS = 6  # shorter calls are said straight ("roger", "cleared to land three four left")
# Nothing urgent gets one: these are said straight, whoever says them.
URGENT = re.compile(r"\b(immediately|go(ing)? around|stop|cancel|caution|traffic alert|low altitude|alert|expedite|"
                    r"abort|hold position|mayday|pan pan|emergency|unable|negative|correction|say again|wind ?shear)\b",
                    re.IGNORECASE)
URGENT_IDS = ("go_around", "stop", "cancel", "traffic", "alert", "immediate", "expedite", "low_alt", "emergency",
              "hold_position", "abort", "unable", "say_again", "correction", "readback")
# A clause starting with one of these is a value (or leads into one): no filler before it.
VALUE_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "niner", "ten", "thousand",
    "hundred", "point", "decimal", "left", "right", "center", "centre", "alpha", "bravo", "charlie", "delta", "echo",
    "foxtrot", "golf", "hotel", "india", "juliett", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec",
    "romeo", "sierra", "tango", "uniform", "victor", "whiskey", "x-ray", "yankee", "zulu", "via",
}
# ... and a clause ending in one of these runs straight into its value.
# ... nor before a sign-off ("..., good day").
SIGN_OFFS = {"good", "have", "see", "so", "bye", "enjoy", "goodbye", "cheers"}
LEADS = {"heading", "runway", "level", "squawk", "altimeter", "qnh", "frequency", "contact", "maintain", "to", "at",
         "on", "and", "via", "information", "speed", "climb", "descend", "turn", "the"}


def _rng(spoken: str, key: str) -> random.Random:
    return random.Random(zlib.crc32(f"{key}|{spoken}".encode()))


def _urgent(spoken: str, instruction_id: str) -> bool:
    iid = (instruction_id or "").lower()
    return bool(URGENT.search(spoken)) or any(word in iid for word in URGENT_IDS)


def _boundaries(spoken: str) -> list[int]:
    """Where a filler can go: after a comma, before a clause that isn't a value, after one that doesn't lead into
    one. Indexes just after the ", "."""
    out = []
    for m in re.finditer(r", ", spoken):
        before = spoken[: m.start()].split()
        after = spoken[m.end():].split()
        if not before or not after:
            continue
        first, last = after[0].lower().strip(".,?"), before[-1].lower().strip(".,")
        if last in LEADS or first in VALUE_WORDS or first in SIGN_OFFS or {first, last} & FILLERS:
            continue
        if after[0][:1].isdigit() or before[-1][-1:].isdigit():
            continue
        out.append(m.end())
    return out


def _insert(spoken: str, at: int, filler: str) -> str:
    return f"{spoken[:at]}{filler}, {spoken[at:]}"


def atc(spoken: str, kind: str = "calm", *, busy: bool = False, key: str = "", instruction_id: str = "",
        rate: float | None = None) -> str:
    """A controller's ``spoken`` words, with a hesitation now and then: after the callsign ("United 318, uh, turn
    left heading two seven zero") or between two parts of a clearance. ``kind``: the controller's manner."""
    if not spoken or len(spoken.split()) < MIN_WORDS or _urgent(spoken, instruction_id):
        return spoken
    rng = _rng(spoken, key)
    chance = ATC_RATE.get(kind, 0.1) if rate is None else rate
    if rng.random() >= chance * (0.5 if busy else 1.0):
        return spoken
    places = _boundaries(spoken)
    if not places:
        return spoken
    # Mostly right after the callsign (the first comma), as they find the next thing to say; else between parts.
    at = places[0] if rng.random() < 0.65 or len(places) == 1 else rng.choice(places[1:])
    return _insert(spoken, at, rng.choice(ATC_FILLERS))


def pilot(spoken: str, *, key: str = "", rate: float = PILOT_RATE) -> str:
    """Another flight's pilot: "uh" before the call, after the station they call, or before their callsign at the
    end of a readback; once in a while twice."""
    if not spoken or len(spoken.split()) < MIN_WORDS - 1 or _urgent(spoken, ""):
        return spoken
    rng = _rng(spoken, key)
    if rng.random() >= rate:
        return spoken
    out = spoken
    for turn in range(2):
        if turn and rng.random() >= TWICE:
            break
        places = _boundaries(out)
        filler = rng.choice(PILOT_FILLERS)
        if rng.random() < 0.3 and not out.lower().startswith(PILOT_FILLERS):
            out = f"{filler.capitalize() if out[:1].isupper() else filler}, {out}"
        elif places:
            out = _insert(out, rng.choice(places), filler)
    return out
