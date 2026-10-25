"""A real ATIS's remarks in words, as its recording reads them: "TWY, E BTN TWY, A AND TWY B CLSD" is shown "Taxiway E
between taxiway A and taxiway B closed" and said "taxiway echo between taxiway alpha and taxiway bravo closed".

The published text is the FAA's contractions (JO 7340.2) and idents run together. Each word is read on its own: a
contraction becomes its words, the letter after "taxiway" its phonetic name, a runway its numbers ("runway eight
left"), feet and frequencies as ATC says them. Words it doesn't know (a navaid's name, "EWABE") stay as they are.
"""

import re

from localtc.atc_core.phraseology import speech

CONTRACTIONS = {
    "ACFT": "aircraft", "ACT": "active", "ADNL": "additional", "ADZ": "advise", "ADVS": "advise", "AFSS": "flight service",
    "AGL": "above ground level", "ALS": "approach lights", "ALT": "altitude", "APCH": "approach", "APCHS": "approaches",
    "ARPT": "airport", "ARR": "arrival", "ARRS": "arrivals", "AVBL": "available", "AZM": "azimuth", "BCN": "beacon",
    "BLW": "below", "ABV": "above", "BTN": "between", "CLNC": "clearance", "CLSD": "closed", "CNTRLN": "centerline",
    "CONST": "construction", "CTN": "caution", "CAUTN": "caution", "DEP": "departure", "DEPS": "departures",
    "DEPG": "departing", "DLVY": "delivery", "DSNT": "distant", "EFCT": "effect", "EQUIP": "equipment", "EXC": "except",
    "EXP": "expect", "EXPC": "expect", "FLD": "field", "FREQ": "frequency", "FT": "feet", "GND": "ground",
    "GS": "glideslope", "GP": "glide path", "HAZ": "hazard", "HRS": "hours", "INCR": "increased", "INFO": "information",
    "INOP": "inoperative", "INT": "intersection", "INTXN": "intersection", "INVOF": "in the vicinity of",
    "LDG": "landing", "LNDG": "landing", "LGT": "light", "LGTS": "lights", "LOC": "localizer", "MAINT": "maintenance",
    "MM": "middle marker", "MULT": "multiple", "NM": "miles", "NOTAMS": "NOTAMs", "OM": "outer marker",
    "OPS": "operations", "OTS": "out of service", "OVR": "over", "PARL": "parallel", "PRCDS": "procedures",
    "PROC": "procedure", "REIL": "runway end identifier lights", "RWY": "runway", "RWYS": "runways",
    "SFC": "surface", "SIMUL": "simultaneous", "TFR": "temporary flight restriction", "THLD": "threshold",
    "TWR": "tower", "TWY": "taxiway", "TWYS": "taxiways", "U/S": "unserviceable", "VCNTY": "vicinity",
    "VEH": "vehicles", "WIP": "work in progress", "WI": "within", "READBACK": "read back", "NUMEROUS": "numerous",
}
# Words that keep their letters: said as they're written ("ILS", "TACAN")
KEEP = {"ILS", "RNAV", "GPS", "VOR", "DME", "TACAN", "NDB", "SIGMET", "AIRMET", "PAPI", "VASI", "NOTAMS", "VFR", "IFR",
        "STAR", "SID", "RVR", "ATIS", "ASDE", "LAHSO", "UNICOM", "CTAF", "PIREP", "PIREPS", "MSL"}
# The words a single letter after them is a taxiway (or a SIGMET) by
LETTER_AFTER = {"SIGMET", "AIRMET", "INFO", "AND", "OR", "BTN", "EXIT", "TO"}
RUNWAY_WORDS = {"RWY", "RWYS", "RUNWAY", "RUNWAYS", "RY"}
TAXIWAY_WORDS = {"TWY", "TWYS", "TAXIWAY", "TAXIWAYS"}


def say(remark: str) -> tuple[str, str]:
    """(shown, said) for one remark as published."""
    text = re.sub(r"\b(TWYS?|RWYS?),\s*", r"\1 ", " ".join(remark.upper().split()))
    tokens = re.findall(r"\d+\.\d+|[A-Z0-9/]+|[,;:]", text)
    shown: list[str] = []
    said: list[str] = []
    before = ""
    in_list = False  # a taxiway list ("TWY Y, S, D"): every name in it a taxiway
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in ",;:":
            if shown:
                shown[-1] += tok
                said[-1] += tok
            continue
        if before in RUNWAY_WORDS and re.fullmatch(r"\d{1,2}[LRC]?", tok):
            number = tok.rstrip("LRC")
            ident = f"{int(number):02d}{tok[len(number):]}"
            shown.append(tok)
            said.append(speech.runway(ident))
        elif tok.isdigit() and nxt == "FT":
            shown.append(f"{int(tok):,}")
            said.append(speech.feet(int(tok)))
        elif re.fullmatch(r"1[1-3]\d\.\d{1,3}", tok):
            shown.append(tok)
            said.append(speech.frequency(float(tok)))
        elif re.fullmatch(r"[A-Z]{1,2}\d{0,2}", tok) and tok not in CONTRACTIONS and (
                before in TAXIWAY_WORDS or in_list or before in LETTER_AFTER and not _word(tok)):
            shown.append(tok)
            said.append(" ".join(speech.letter(c) if c.isalpha() else speech.digits(c) for c in tok))
        elif tok.isdigit():
            shown.append(tok)
            said.append(speech.number_words(int(tok)) if int(tok) < 1000 else speech.digits(tok))
        elif tok in CONTRACTIONS:
            shown.append(CONTRACTIONS[tok])
            said.append(CONTRACTIONS[tok])
        elif tok in KEEP or not tok.isalpha() or len(tok) <= 3 and not re.search(r"[AEIOUY]", tok):  # "HNL", "CKH"
            shown.append(tok)
            said.append(tok)
        else:
            shown.append(tok.lower())
            said.append(tok.lower())
        in_list = (before in TAXIWAY_WORDS or in_list) and i + 1 < len(tokens) and tokens[i + 1] == ","
        before = tok
    line = " ".join(shown)
    return line[:1].upper() + line[1:], " ".join(said)


def _word(tok: str) -> bool:
    """A short English word, not an ident ("ALL", "AT", "THE" are words; "HNL", "CKH" aren't)."""
    return tok in {"A", "ALL", "AT", "THE", "AND", "OR", "TO", "FOR", "ON", "IN", "OF", "IS", "BE", "BY", "DUE", "END",
                   "USE", "NOT", "NO", "FOR", "OFF", "OUT", "ARE", "HAS", "ANY", "PER", "SEE", "NEW", "OLD", "WEST",
                   "EAST", "NE", "NW", "SE", "SW", "DO", "LAST", "MORE", "RMK"}
