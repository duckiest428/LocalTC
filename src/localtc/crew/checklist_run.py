"""Reading a checklist from ``checklist_db`` against the aircraft: what the copilot sees of each item, whether it's as
the checklist wants it, and what the copilot does about it.

The copilot reads in one of three ways (``[crew] checklist_mode``, the pilot's choice):

- ``challenge`` (challenge and response, the default): it reads each challenge and waits for the captain's answer;
  the items on its own side (the seatbelt sign, the transponder, its altimeter) it answers itself; an item the
  aircraft shows wrong holds the checklist until it's right.
- ``read`` (no challenge and response): it reads each line, challenge and response, checking what it can see, and
  says what's wrong; nothing waits.
- ``auto`` (full automatic): as ``read``, and it sets what it can of its own side (and does the actions the
  aircraft's own checklist gives its copilot); what's the captain's and wrong, it says, and holds there.

An item it can't see is read and left to the captain: never answered for them.
"""

import re
from dataclasses import dataclass, field

from localtc.atc_core.phraseology import speech
from localtc.crew.actions import Cockpit
from localtc.crew.checklist_db import Checklist, Line, fill, sense_key
from localtc.crew.commands import Command

MODES = ("challenge", "read", "auto")
# The items on the copilot's side: answered (and set, when it has the hands) by it, not asked of the captain.
COPILOT_SIDE = {"seatbelt", "transponder", "altimeter"}
SYSTEM_FIELDS = {"LIGHT BEACON": "light_beacon", "LIGHT STROBE": "light_strobe", "LIGHT LANDING": "light_landing",
                 "LIGHT TAXI": "light_taxi", "LIGHT NAV": "light_nav", "CABIN SEATBELTS ALERT SWITCH": "seatbelt",
                 "SPOILERS ARMED": "spoilers_armed", "AUTOPILOT MASTER": "ap_master"}
OWN_FIELDS = {"BRAKE PARKING POSITION": "parking_brake", "GEAR HANDLE POSITION": "gear_down"}
OPS = {"==": lambda a, b: abs(a - b) < 1e-6, "!=": lambda a, b: abs(a - b) >= 1e-6, "<": lambda a, b: a < b,
       "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


@dataclass
class Progress:
    """Where a checklist is: the line next (``index``), when it was asked, what each line came to ("ok", "wrong",
    "read" (not seen: the captain's), "skipped", or "" not yet), and whether it's holding on a line."""

    checklist: Checklist
    index: int = 0
    asked_t: float = 0.0
    repeats: int = 0
    held: bool = False
    paused: bool = False
    results: list[str] = field(default_factory=list)
    skipped: dict[int, str] = field(default_factory=dict)  # line -> why

    def __post_init__(self) -> None:
        if not self.results:
            self.results = [""] * len(self.checklist.lines)

    def to_json(self, values: dict[str, str]) -> dict:
        return {"id": self.checklist.id, "name": self.checklist.name, "phase": self.checklist.phase, "index": self.index,
                "held": self.held, "paused": self.paused,
                "lines": [{"challenge": ln.challenge, "response": fill(ln.response, values), "result": r,
                           "why": self.skipped.get(i, "")} for i, (ln, r) in enumerate(zip(self.checklist.lines, self.results))]}


def wanted_on(response: str) -> bool | None:
    """What a response asks for of a switch: on (True), off (False), or nothing said (None: "___", "AS RQRD")."""
    r = response.upper()
    if re.search(r"\b(?:OFF|DISARM|RELEASED|UP)\b", r) and not re.search(r"\bON\b", r):
        return False
    if re.search(r"\b(?:ON|SET|ARM(?:ED)?|DOWN|DN|AUTO)\b", r):
        return True
    return None


def seen(line: Line, c: Cockpit, values: dict[str, str]) -> tuple[str, bool | None] | None:
    """What the copilot sees of a line: (in words, whether it's as the checklist wants it, None: nothing wanted to
    compare it with), or None when it can't see it."""
    if line.condition is not None:
        var, op, want = line.condition
        value = _read(var, c)
        if value is not None:
            ok = OPS.get(op, OPS["=="])(value, want)
            return ("as it should be" if ok else "not as it should be"), ok
    key = sense_key(line.challenge)
    if key is None or c.own is None:
        return None
    response = fill(line.response, values)
    want = wanted_on(response)
    s, own, p = c.systems, c.own, c.profile
    if key == "seatbelt":
        if s is None or "seatbelt" in p.unread:
            return None
        return ("on" if s.seatbelt else "off"), (None if want is None else s.seatbelt == want)
    if key == "beacon":
        return None if s is None else (("on" if s.light_beacon else "off"), None if want is None else s.light_beacon == want)
    if key == "strobe":
        return None if s is None else (("on" if s.light_strobe else "off"), None if want is None else s.light_strobe == want)
    if key == "parking_brake":
        return ("set" if own.parking_brake else "released"), (None if want is None else own.parking_brake == want)
    if key == "gear":
        return ("down" if own.gear_down else "up"), (None if want is None else own.gear_down == want)
    if key == "spoilers":
        return None if s is None else (("armed" if s.spoilers_armed else "not armed"),
                                       None if want is None else s.spoilers_armed == want)
    if key == "transponder":
        mode = {"alt": "TA/RA", "on": "on", "standby": "standby"}.get(own.xpdr_mode, own.xpdr_mode)
        r = response.upper()
        if re.search(r"\bTA\b|TA/RA", r):  # TA or TA/RA: altitude reporting on (just "on" isn't it)
            return mode, own.xpdr_mode == "alt"
        return mode, (own.xpdr_mode in ("alt", "on") if re.search(r"\b(?:AUTO|ON)\b", r) else None)
    if key == "altimeter":
        setting = own.altimeter_setting_inhg if getattr(own, "altimeter_setting_inhg", 0) else own.altimeter_inhg
        std = abs(setting - 29.92) < 0.005
        wanted = values.get("altimeter", "")
        shown = "standard" if std else speech.altimeter_display(setting)
        if not wanted:
            return shown, None
        if wanted.upper() in ("STD", "STANDARD"):
            return shown, std
        try:
            target = float(wanted) / 33.8639 if float(wanted) > 100 else float(wanted)
        except ValueError:
            return shown, None
        return shown, abs(setting - target) < 0.02
    if key == "flaps":
        if not c.flaps_known or c.flaps_index is None:
            return None
        name = p.detent_name(c.flaps_index, c.flap_positions, on_ground=own.on_ground)
        plan = values.get("flaps", "")
        if plan and "{flaps}" in line.response:
            return name, _norm(name) == _norm(plan) or _norm(name.replace("+F", "")) == _norm(plan)
        return name, (c.flaps_index == 0 if want is False else None)
    if key == "autobrake":
        if s is None or s.autobrake < 0:
            return None
        return (p.autobrake_name(s.autobrake) or str(s.autobrake)), None
    if key == "fuel":
        return (values.get("fuel") or None, True) if values.get("fuel") else None
    return None


def fix_for(line: Line, c: Cockpit, values: dict[str, str]) -> Command | None:
    """What the copilot sends to set a line of its own side that isn't right."""
    key = sense_key(line.challenge)
    response = fill(line.response, values).upper()
    if key == "seatbelt":
        on = wanted_on(response)
        return Command("seatbelt", "on" if on else "off") if on is not None else None
    if key == "transponder" and ("TA" in response or "AUTO" in response or "ON" in response):
        return Command("transponder", "alt")
    if key == "altimeter":
        wanted = values.get("altimeter", "")
        if wanted.upper() in ("STD", "STANDARD"):
            return Command("altimeter", "29.92")
        try:
            n = float(wanted)
        except ValueError:
            return None
        return Command("altimeter", str(int(n)), "hpa") if n > 100 else Command("altimeter", f"{n:.2f}")
    return None


def side(line: Line) -> str:
    """Whose an item is: "copilot" for its own side, else "captain"."""
    return "copilot" if sense_key(line.challenge) in COPILOT_SIDE else "captain"


def say(line: Line, values: dict[str, str]) -> str:
    """A line as read: "Parking brake... set." (the response's ___ left out: said as it's set)."""
    challenge = _speakable(line.challenge)
    response = _speakable(fill(line.response, values))
    if not response or "___" in response or response.strip(" .") in ("", "_"):
        return f"{challenge}?"
    return f"{challenge}, {response}."


def done(line: Line, values: dict[str, str], word: str) -> str:
    """A line of the copilot's own as it's set: "TCAS, TA or TA/RA, set." """
    words = say(line, values)
    return words.replace("?", f", {word}.") if words.endswith("?") else f"{words.rstrip('.')}, {word}."


def _read(var: str, c: Cockpit) -> float | None:
    if var in c.vars:
        return float(c.vars[var])
    base = var.split(":")[0].strip().upper()
    if base in SYSTEM_FIELDS and c.systems is not None:
        return float(bool(getattr(c.systems, SYSTEM_FIELDS[base])))
    if base in OWN_FIELDS and c.own is not None:
        return float(bool(getattr(c.own, OWN_FIELDS[base])))
    return None


# A checklist's abbreviations as said; the acronyms said as letters stay capitals.
SAID = {"T.O": "takeoff", "RWY": "runway", "W/S": "windshear", "LDG": "landing", "SPLRS": "spoilers", "BRK": "brake",
        "LT": "lights", "ENG": "engine", "SEL": "selector", "PRED": "predictive", "BAT": "batteries", "EXT": "exterior",
        "QTY": "quantity", "DN": "down", "GND": "ground", "PARK": "parking", "APPR": "approach", "CHKD": "checked",
        "RQRD": "required", "AS": "as", "NO": "no", "INOP": "inop", "ALT": "altitude", "HDG": "heading", "SPD": "speed",
        "STBY": "standby", "SYS": "system", "FLT": "flight", "CTL": "control", "CTLS": "controls", "MEMO": "memo"}
ACRONYMS = {"TCAS", "ECAM", "APU", "ADIRS", "IRS", "IRSS", "EFBS", "EFB", "MCP", "CDU", "FMS", "MCDU", "ATC", "VREF",
            "TA", "RA", "TA/RA", "V1", "VR", "V2", "QNH", "STD", "ILS", "RNAV", "VOR", "XPDR", "ND", "PFD", "IFE", "EICAS",
            "PTU", "ECL", "DU", "RTO", "MAX", "CONF"}


def _speakable(text: str) -> str:
    """ "T.O RWY ___ (BOTH)" as said: "Takeoff runway, both" (the words, the acronyms as letters)."""
    t = re.sub(r"\(BOTH\)", " both", text).replace("&", " and ")
    words = []
    for word in t.split():
        up = word.upper().strip(",.")
        if up in SAID:
            words.append(SAID[up])
        elif up in ACRONYMS or re.fullmatch(r"[A-Z]?\d+[A-Z]?", up):
            words.append(word.upper())
        else:
            words.append(word.lower())
    out = " ".join(words).strip()
    return out[:1].upper() + out[1:]


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9+]", "", text.upper()).replace("CONF", "")
