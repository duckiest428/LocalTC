"""Readbacks said right but heard the ways speech-to-text writes them (Whisper: digits with and without commas,
dashes between digits said one by one, words misheard): LocalTC's fixups and grammar still take them as right. The
readback sweep turned up "climbing to 8,000" and "descending and maintaining" not read as altitudes, "squawking",
"knot", "lining up and wait", "juliett" (two t's), "runway 1, 5", "maintain 2,000" turned into the MONTN2 departure,
"expect 14,010 minutes" ..."""

import random
import re

import pytest

import test_readback as T
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.readback.normalize import normalize, render
from localtc.atc_core.values import Approach, Callsign
from localtc.stt.vocabulary import VocabularyHints, fixup, match_names

# --- Whisper's writing of a spoken readback --------------------------------------------------------------------------

DIGIT = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
         "eight": "8", "nine": "9", "niner": "9", "tree": "3", "fife": "5"}
TENS = {"twenty": 2, "thirty": 3, "forty": 4, "fifty": 5, "sixty": 6, "seventy": 7, "eighty": 8, "ninety": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
NUMWORD = set(DIGIT) | {"thousand", "hundred", "point", "decimal"} | set(TENS) | set(TEENS)


def is_num(w: str) -> bool:
    w = w.lower().strip(".,")
    return w in NUMWORD or (("-" in w) and all(p in TENS or p in DIGIT for p in w.split("-")))


def num_value(words: list[str]) -> dict:
    """The run's pieces: digits before 'point', after it, thousands, hundreds."""
    digits, after, thousands, hundreds, point = [], [], None, None, False
    for raw in words:
        w = raw.lower().strip(".,")
        if w in ("point", "decimal"):
            point = True
            continue
        if w == "thousand":
            thousands = "".join(digits) or "1"
            digits = []
            continue
        if w == "hundred":
            hundreds = "".join(digits)
            digits = []
            continue
        if "-" in w:
            a, b = w.split("-")
            val = str(TENS[a]) + DIGIT.get(b, "0")
        elif w in TENS:
            val = str(TENS[w]) + "0"  # "twenty" alone
        elif w in TEENS:
            val = str(TEENS[w])
        else:
            val = DIGIT[w]
        target = after if point else digits
        if target and target[-1].endswith("0") and len(target[-1]) == 2 and target[-1][0] != "0" and len(val) == 1                 and w not in ("zero",):
            target[-1] = target[-1][0] + val  # "twenty three"
            continue
        target.append(val)
    return {"digits": "".join(digits), "after": "".join(after), "point": point, "thousands": thousands,
            "hundreds": hundreds}


def write(words: list[str], rng: random.Random) -> str:
    v = num_value(words)
    style = rng.choice(["plain", "plain", "plain", "comma", "dash", "space", "words", "mixed"])
    if v["point"]:
        left, right = v["digits"], v["after"]
        sep = rng.choice([".", ".", ".", " point ", " decimal ", ". "])
        if style == "dash":
            left = "-".join(left)
        elif style == "space":
            left = " ".join(left)
        elif style == "words":
            return " ".join(words)
        return f"{left}{sep}{right}"
    if v["thousands"] is not None:
        n = int(v["thousands"]) * 1000 + int(v["hundreds"] or 0) * 100 + int(v["digits"] or 0)
        if style == "words":
            return " ".join(words)
        if style == "dash" and len(v["thousands"]) == 2:
            return f"{v['thousands'][0]}-{v['thousands'][1]},{n % 1000:03d}"
        if style == "space" and len(v["thousands"]) == 2:
            return f"{v['thousands'][0]} {v['thousands'][1]},{n % 1000:03d}"
        if style == "mixed":
            rest = f" {v['hundreds']} hundred" if v["hundreds"] else ""
            return f"{int(v['thousands'])} thousand{rest}"
        return f"{n:,}" if style != "comma" or n < 1000 else f"{n:,}".replace(",", "")
    if v["hundreds"] is not None:
        return f"{int(v['hundreds']) * 100}"
    d = v["digits"]
    if style == "dash" and len(d) > 1:
        return "-".join(d)
    if style == "space":
        return " ".join(d)
    if style == "comma" and len(d) > 1:
        return ", ".join(d)
    if style == "words":
        return " ".join(words)
    if style == "mixed" and len(d) == 3:
        return f"{d[0]} {d[1:]}"
    return d


WORD_SUBS = [
    (r"\bclimb and maintain\b", ["climb maintain", "climbing maintain", "climb and maintain", "climb to", "climbing to",
                                 "climbing and maintaining", "climb in maintain", "climb and maintaining", "climb, maintain"]),
    (r"\bdescend and maintain\b", ["descend maintain", "descending maintain", "descent and maintain", "descending to",
                                   "descend to", "descending and maintaining", "descend in maintain", "descend, maintain"]),
    (r"\bdescend\b(?! via)", ["descending", "descend", "descent"]),
    (r"\bclimb\b(?! to| and| maintain| in)", ["climbing", "climb", "climb to"]),
    (r"\bmaintain\b", ["maintaining", "maintain", "maintain"]),
    (r"\bcleared to land\b", ["clear to land", "cleared to land", "cleared land", "clear to land on"]),
    (r"\bcleared for takeoff\b", ["clear for takeoff", "cleared for take off", "cleared for take-off", "clear for take off"]),
    (r"\bcleared\b", ["clear", "cleared", "cleared for the"]),
    (r"\bline up and wait\b", ["lining up and wait", "line up and wait", "lineup and wait", "line up wait",
                               "lining up and waiting", "line-up and wait"]),
    (r"\bhold short\b", ["holding short", "hold short", "hold short of", "holding short of"]),
    (r"\bsquawk\b", ["squawk", "squawking", "squak", "squawk code"]),
    (r"\bturn left heading\b", ["left heading", "turn left heading", "turning left heading", "left turn heading",
                                "turn left, heading"]),
    (r"\bturn right heading\b", ["right heading", "turn right heading", "turning right heading", "right turn heading"]),
    (r"\bheading\b", ["heading", "heading", "Heading,"]),
    (r"\bR-NAV\b", ["RNAV", "R-NAV", "R NAV", "RNAV", "are nav"]),
    (r"\bI L S\b", ["ILS", "ILS", "I.L.S.", "I-L-S", "ILS"]),
    (r"\bexpect\b", ["expect", "expecting", "and expect"]),
    (r"\bdirect\b", ["direct", "direct to", "proceeding direct", "direct"]),
    (r"\bknots\b", ["knots", "knots", "knot"]),
    (r"\bpilot's discretion\b", ["pilots discretion", "pilot's discretion", "pilot discretion", "at pilot's discretion"]),
    (r"\bwhen ready\b", ["when ready", "when ready,"]),
    (r"\bgoing around\b", ["going around", "go around"]),
    (r"\bcontinue descent\b", ["continue descent", "continuing descent", "continue the descent", "continue descend"]),
    (r"\buntil established\b", ["until established", "till established", "until establish"]),
    (r"\bcross\b", ["cross", "crossing"]),
    (r"\bcontact\b", ["contact", "over to", "contacting"]),
    (r"\bmonitor\b", ["monitor", "monitoring"]),
    (r"\bground\b", ["ground", "Ground"]),
    (r"\bvia\b", ["via", "via"]),
    (r"\binbound\b", ["inbound", "in bound"]),
    (r"\bleft turns\b", ["left turns", "left hand turns", "left-hand turns"]),
    (r"\bright turns\b", ["right turns", "right hand turns", "right-hand turns"]),
    (r"\brunway heading\b", ["runway heading", "runway heading"]),
    (r"\btaxi\b", ["taxi", "taxiing", "taxi to"]),
    (r"\bpushback\b", ["push back", "pushback", "pushing back"]),
    (r"\bsplit\b", ["split"]),
]

CALLSIGN_FORMS = {
    "Skyhawk two lima tango": ["Skyhawk 2LT", "Skyhawk 2 Lima Tango", "2LT", "Skyhawk two lima tango", "Sky Hawk 2LT",
                               "November 172LT", "Skyhawk 2-Lima-Tango"],
    "Cherokee one two kilo": ["Cherokee 12K", "Cherokee 1 2 Kilo", "12K", "Cherokee one two kilo", "Cherokee 12 Kilo"],
    "Alaska one twenty-three": ["Alaska 123", "Alaska 1-2-3", "Alaska one two three", "Alaska 1 2 3", "Alaska 123.",
                                "Alaska one twenty three", "Alaska 1, 2, 3"],
}

LETTERS = {"alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F", "golf": "G",
           "hotel": "H", "juliet": "J", "kilo": "K", "lima": "L", "mike": "M", "november": "N", "papa": "P",
           "quebec": "Q", "sierra": "S", "tango": "T", "whiskey": "W", "yankee": "Y", "zulu": "Z", "india": "I",
           "oscar": "O", "romeo": "R", "uniform": "U", "victor": "V", "x-ray": "X"}


def whisperize(text: str, rng: random.Random, sub_p: float = 0.5) -> str:
    for spoken, forms in CALLSIGN_FORMS.items():
        if spoken in text:
            text = text.replace(spoken, rng.choice(forms))
    for pattern, choices in WORD_SUBS:
        if rng.random() < sub_p:
            text = re.sub(pattern, lambda m: rng.choice(choices), text, count=1, flags=re.I)
    words = text.split(" ")
    out, i = [], 0
    while i < len(words):
        if is_num(words[i]) and words[i].lower().strip(".,") not in ("point", "decimal", "thousand", "hundred"):
            j = i
            while j < len(words) and is_num(words[j]) and not (j > i and words[j - 1].endswith((",", "."))):
                j += 1
            trail = words[j - 1][-1] if words[j - 1][-1] in ",." else ""
            out.append(write(words[i:j], rng) + trail)
            i = j
            continue
        w = words[i]
        bare = w.lower().strip(".,")
        if bare in LETTERS and rng.random() < 0.3:
            w = LETTERS[bare] + (w[-1] if w[-1] in ",." else "")
        out.append(w)
        i += 1
    text = " ".join(out)
    if rng.random() < 0.3:
        text = re.sub(r"(?<=[A-Za-z]), ", ". ", text, count=1)
    if rng.random() < 0.2:
        text = text.rstrip(".") + "."
    if rng.random() < 0.15:
        text = "Uh, " + text[0].lower() + text[1:]
    return text


ICAO_SUBS = [
    (r"\bflight level\b", ["flight level", "flight level", "FL", "Flight Level"]),
    (r"\bQNH\b", ["QNH", "QNH", "QNH,"]),
    (r"\bholding point\b", ["holding point", "holding point", "holding port"]),
    (r"\bclimb to\b", ["climb to", "climbing to", "climb", "climb to"]),
    (r"\bdescend to\b", ["descend to", "descending to", "descend", "descent to"]),
    (r"\bfeet\b", ["feet", "feet", "ft"]),
]


def _heard(said: str, slots: dict) -> str:
    """What LocalTC makes of it before the grammar: its fixups, and the flight's own procedure and fixes."""
    fixes = (slots["fix"],) if slots["fix"].isupper() else ()
    return match_names(fixup(said), VocabularyHints(procedures=(slots["procedure"],), fixes=fixes))


@pytest.mark.parametrize("style", ["faa", "icao"])
def test_a_right_readback_written_the_ways_whisper_writes_it_is_right(style):
    """Every readback LocalTC asks for, said right and transcribed as Whisper writes it: digits with and without
    commas, "1-2-0.5", "1 4,500", "2, 7, 0", "climbing to 8,000", "descending and maintaining", "squawking",
    "lining up and wait", "juliett", "are nav", "expect 14,010 minutes" ... is taken as right."""
    from contextlib import nullcontext

    from localtc.atc_core.region import Region, speaking

    library = T.ICAO if style == "icao" else T.LIBRARY
    wrong = []
    for iid in T.READBACK_TEMPLATES:
        rng = random.Random(f"{iid}{style}")
        for _ in range(12):
            slots = T.random_slots(rng)
            with speaking(Region("icao", 6000)) if style == "icao" else nullcontext():
                ideal = library.pilot_readback(iid, slots)
                rendered = library.render(iid, slots)
            if style == "icao":
                for pattern, choices in ICAO_SUBS:
                    if rng.random() < 0.5:
                        ideal = re.sub(pattern, lambda m: rng.choice(choices), ideal, count=1)
            said = whisperize(ideal, rng)
            heard = _heard(said, slots)
            pending = T.PendingReadback(iid, rendered.controller, rendered.expected, rendered.required, rendered.optional)
            with speaking(Region("icao", 6000)) if style == "icao" else nullcontext():
                result = GrammarInterpreter().interpret(heard, pending, InterpretContext(callsign=slots["callsign"]))
            if (result.kind, result.status) != ("readback", "correct"):
                wrong.append((iid, said, render(normalize(heard)), result.status, dict(result.mismatched),
                              result.missing))
    assert not wrong, wrong[:5]


CALLSIGN = Callsign("N172LT", type_name="Skyhawk")
MONTN = VocabularyHints(procedures=("MONTN2",), fixes=("BLAKO",))


@pytest.mark.parametrize("instruction, slots, said, status", [
    # Altitudes as pilots say them going for them
    ("common.climb", {"altitude": 8000}, "Climbing to 8,000, Skyhawk 2LT.", "correct"),
    ("common.descend", {"altitude": 6000}, "Descending to 6,000, 2LT.", "correct"),
    ("common.descend", {"altitude": 6500}, "Descending 6,500, 2LT.", "correct"),
    ("common.climb", {"altitude": 10500}, "Climbing and maintaining 1-0,500, 2LT.", "correct"),
    ("common.climb", {"altitude": 14500}, "Climb and maintain 1 4,500, Skyhawk 2LT.", "correct"),
    ("common.descend", {"altitude": 13000}, "Descent to 13 thousand, 2LT.", "correct"),
    ("common.climb", {"altitude": 8000}, "Climbing to 7,000, Skyhawk 2LT.", "incorrect"),  # still a wrong one
    ("center.maintain", {"altitude": 6000, "approach": Approach("ILS", "16R")}, "Maintain flight level 6, 0, Skyhawk 2LT.",
     "correct"),
    # "maintain" is never the MONTN2 departure
    ("common.climb", {"altitude": 2000}, "Climb and maintain 2,000, Skyhawk 2LT.", "correct"),
    ("common.climb", {"altitude": 12000}, "Climb maintain one two thousand, Skyhawk 2LT.", "correct"),
    # Speeds, squawks, headings
    ("approach.speed", {"speed": 210}, "2-1-0 knot, Skyhawk 2LT.", "correct"),
    ("approach.speed", {"speed": 220}, "220 nots, Skyhawk 2LT.", "correct"),
    ("vfr.squawk", {"squawk": "4636"}, "Squawking 4-6-3-6, Skyhawk 2LT.", "correct"),
    ("vfr.squawk", {"squawk": "4521"}, "Squad 4521, Skyhawk 2LT.", "correct"),
    ("approach.heading", {"heading": 270, "turn": "right"}, "Right hitting 270, Skyhawk 2LT.", "correct"),
    # Runways said digit by digit, the line up, the approach
    ("tower.luaw", {"runway": "15"}, "Runway 1, 5, line up and wait, Skyhawk 2LT.", "correct"),
    ("tower.luaw", {"runway": "16L"}, "Runway 16 left, lining up and waiting, Skyhawk 2LT.", "correct"),
    ("common.expect_approach", {"approach": Approach("RNAV", "17")}, "Expect R-NAV runway 17, 2LT.", "correct"),
    ("common.emergency_approach", {"approach": Approach("RNAV", "15")}, "Cleared are nav runway 15 approach, 2LT.",
     "correct"),
    # A frequency, then the callsign after a full stop
    ("common.contact", {"station": "Boeing Ground", "frequency": 134.05}, "Boeing Ground 1 3 4 decimal 05. 2LT.",
     "correct"),
    # Taxiways: "juliett", and the callsign after the last one
    ("ground.taxi_in", {"taxi_route": ("E7",)}, "Taxi to parking via echo 7, 2LT.", "correct"),
    ("ground.taxi_in", {"taxi_route": ("A", "J", "E7")}, "Taxi to parking via alpha, juliett, echo 7, 2LT.", "correct"),
    # The expect time run into the level
    ("clearance.ifr", {"destination": "Boeing Field", "altitude": 5000, "cruise": 14000, "frequency": 124.675,
                       "squawk": "4521"},
     "Cleared to Boeing Field as filed, climb and maintain 5,000, expect 14,010 minutes after, departure 124.675, "
     "squawk 4521, 2LT.", "correct"),
    ("clearance.ifr", {"destination": "Boeing Field", "altitude": 5000, "cruise": 6000, "frequency": 124.675,
                       "squawk": "4521"},
     "Cleared to Boeing Field as filed, climbing to 5,000, expecting 6010 minutes after, departure 124.675, "
     "squawk 4521, 2LT.", "correct"),
])
def test_readbacks_heard_oddly_but_said_right(instruction, slots, said, status):
    slots = {"callsign": CALLSIGN, **slots}
    for name, value in (("station", "Seattle Departure"),):
        slots.setdefault(name, value)
    heard = match_names(fixup(said), MONTN)
    result = GrammarInterpreter().interpret(heard, T.pending_for(instruction, slots), InterpretContext(callsign=CALLSIGN))
    assert (result.kind, result.status) == ("readback", status), (heard, render(normalize(heard)), result)
