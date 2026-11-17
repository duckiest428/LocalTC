"""The radio log: one line per event the pilot would want to read back later.

The app's ATC tab shows these as the flight goes, and a replay (``localtc.replay.rewatch``) keeps them as the
flight's transcript, so the two always read the same.
"""

import re

from localtc.console import ALERTS, PHASES
from localtc.sim_api.events import REPEATED
from localtc.sim_api import (
    AtcAlert,
    AtcTransmission,
    AtisBroadcast,
    BusEvent,
    CabinSpeech,
    ConnectionStatus,
    CrewAction,
    CrewSpeech,
    FlightArrived,
    IntercomHeard,
    LlmExchange,
    PhaseChanged,
    RadioChatter,
    RadioTuned,
    ReadbackEvaluated,
    SessionNote,
    RampSpeech,
    SimLifecycle,
    Transcript,
)


# --- the copilot's calls as they're written ---------------------------------------------------------------------

_DIGITS = {"zero": "0", "one": "1", "two": "2", "three": "3", "tree": "3", "four": "4", "five": "5", "fife": "5",
           "six": "6", "seven": "7", "eight": "8", "nine": "9", "niner": "9"}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_NUMBER = "|".join(sorted([*_DIGITS, *_TEENS, *_TENS, "hundred", "thousand", "point", "decimal"], key=len, reverse=True))
_RUN = re.compile(rf"\b(?:{_NUMBER})(?:[ -]+(?:{_NUMBER}))*\b", re.IGNORECASE)
_LETTERS = {"alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F", "golf": "G",
            "hotel": "H", "india": "I", "juliett": "J", "juliet": "J", "kilo": "K", "lima": "L", "mike": "M",
            "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T",
            "uniform": "U", "victor": "V", "whiskey": "W", "x-ray": "X", "yankee": "Y", "zulu": "Z"}
_TAXIWAY = re.compile(rf"\b({'|'.join(_LETTERS)})(?: ({'|'.join(_LETTERS)}))? (\d{{1,2}})\b(?![.,]?\d)", re.IGNORECASE)
NO_COMMAS = ("squawk", "heading", "level", "runway", "flight", "inbound", "radial", "speed", "clearance")


def _value(words: list[str]) -> str:
    """One run of number words as it's written: "one three zero point niner" 130.9, "one one thousand one hundred"
    11,100, "ninety-eight" 98, "two two zero" 220."""
    if any(w in ("point", "decimal") for w in words):
        i = next(k for k, w in enumerate(words) if w in ("point", "decimal"))
        return f"{_value(words[:i])}.{''.join(_DIGITS.get(w, '') for w in words[i + 1:])}"
    total, current, digits, multiplied = 0, 0, "", False
    for w in words:
        if w == "thousand":
            total += (int(digits or "0") + current or 1) * 1000
            current, digits, multiplied = 0, "", True
        elif w == "hundred":
            current = (int(digits or "0") + current or 1) * 100
            digits, multiplied = "", True
        elif w in _TENS:
            if multiplied:
                current += _TENS[w]
            else:
                digits += str(_TENS[w])[0]
                digits += "0"  # "ninety" alone; "ninety-eight" puts the eight in its place
        elif w in _TEENS:
            if multiplied:
                current += _TEENS[w]
            else:
                digits += str(_TEENS[w])
        elif w in _DIGITS:
            if multiplied:
                current += int(_DIGITS[w])
            elif digits.endswith("0") and len(words) > 1 and words[words.index(w) - 1] in _TENS:
                digits = digits[:-1] + _DIGITS[w]
            else:
                digits += _DIGITS[w]
    if multiplied:
        return f"{total + current + int(digits or '0'):,}"
    return digits


def written(text: str) -> str:
    """A call the copilot speaks in words, written as the radio log shows the rest: "Roma Departure one three zero point
    niner, Emirates ninety-eight" is "Roma Departure 130.9, Emirates 98"; a taxiway "kilo one zero" K10."""
    def number(m: re.Match) -> str:
        words = [w.lower() for w in re.split(r"[ -]+", m.group(0)) if w]
        if words == ["one"] or words[0] in ("point", "decimal"):
            return m.group(0)
        value = _value(words)
        before = text[:m.start()].rstrip().rsplit(" ", 1)[-1].lower()
        return value.replace(",", "") if before in NO_COMMAS else value

    out = _RUN.sub(number, text)
    out = _TAXIWAY.sub(lambda m: _LETTERS[m.group(1).lower()] + (_LETTERS[m.group(2).lower()] if m.group(2) else "") + m.group(3), out)
    if (via := re.search(r"\bvia\b", out, re.IGNORECASE)) is not None and ", " in out[via.end():]:
        # The taxiways of a route ("via K11, kilo, Z17, zulu"): letters too, up to the callsign at the end
        route, _, sign = out[via.end():].rpartition(", ")
        route = re.sub(rf"\b({'|'.join(_LETTERS)})\b", lambda m: _LETTERS[m.group(1).lower()], route, flags=re.IGNORECASE)
        route = re.sub(r"\b([A-Z]) ([A-Z]\d*)\b", r"\1\2", route)  # "bravo tango" is taxiway BT
        out = f"{out[:via.end()]}{route}, {sign}"
    return out


def radio_line(ev: BusEvent) -> dict | None:
    """One line of the ATC tab's radio log, or None for events it doesn't show."""
    t = round(ev.t, 1)
    if isinstance(ev, AtcTransmission):
        return {"kind": "atc", "t": t, "station": ev.station, "mhz": ev.frequency_mhz, "text": ev.text}
    if isinstance(ev, Transcript):
        who = "copilot" if ev.source == "copilot" else "pilot"
        if not ev.text and ev.source == "cancelled":
            return None
        if not ev.text:
            return {"kind": "system", "t": t, "text": "Nothing heard: check the microphone if you spoke", "level": "warn"}
        text = written(ev.text) if ev.source == "copilot" else ev.text
        return {"kind": who, "t": t, "text": text, "unclear": ev.confidence is not None and ev.confidence < 0.5,
                "radio": ev.radio}
    if isinstance(ev, IntercomHeard):  # to the copilot, not on the radio
        if not ev.text:
            return None
        return {"kind": "intercom", "to": ev.to, "t": t, "text": ev.text, "unclear": ev.confidence is not None and ev.confidence < 0.5}
    if isinstance(ev, CrewSpeech):
        return {"kind": "crew", "t": t, "text": ev.text, "level": "warn" if ev.kind in ("refused", "alert") else ""}
    if isinstance(ev, CabinSpeech):  # the cabin crew: to the passengers, or to you
        if ev.summary == REPEATED:
            return None  # the same announcement in the airline's other language: one line in the log is enough
        return {"kind": "cabin", "t": t, "text": ev.text, "who": ev.speaker, "channel": ev.channel, "summary": ev.summary}
    if isinstance(ev, RampSpeech):  # the ground crew: on their frequency, or the headset
        return {"kind": "ramp", "t": t, "text": ev.text, "who": ev.speaker, "channel": ev.channel}
    if isinstance(ev, CrewAction):
        return None  # what the copilot said about it is the line; the action itself is in the recording
    if isinstance(ev, ReadbackEvaluated):
        if ev.status == "correct":
            return {"kind": "readback", "t": t, "ok": True, "text": "readback ok"}
        what = ", ".join([*ev.missing, *(f"{k} {v}" for k, v in ev.mismatched.items())])
        return {"kind": "readback", "t": t, "ok": False, "text": f"readback {ev.status}" + (f": {what}" if what else "")}
    if isinstance(ev, RadioChatter):  # somebody else on the frequency
        who = ev.station if ev.speaker == "atc" else ev.callsign
        return {"kind": "chatter", "t": t, "station": who, "mhz": ev.frequency_mhz, "text": ev.text, "atc": ev.speaker == "atc",
                "callsign": ev.callsign, "ident": ev.ident}
    if isinstance(ev, AtisBroadcast):
        return {"kind": "atis", "t": t, "station": f"{ev.station} information {ev.letter}"
                + (f" · {ev.source}" if ev.source else ""), "mhz": ev.frequency_mhz,
                "text": ev.text}
    if isinstance(ev, PhaseChanged):
        return {"kind": "phase", "t": t, "text": PHASES.get(ev.phase, ev.phase)}
    if isinstance(ev, RadioTuned):
        return {"kind": "tuned", "t": t, "mhz": ev.frequency_mhz, "radio": ev.radio,
                "text": ev.station or "no ATC on this frequency"}
    if isinstance(ev, AtcAlert):
        text = ALERTS.get(ev.kind, ev.kind.replace("_", " "))
        if ev.detail and ev.kind in ("emergency", "copilot", "out_of_range", "llm_timeout", "llm_rejected", "online",
                                         "online_back", "online_gap"):
            text = f"Copilot: {ev.detail}" if ev.kind == "copilot" else f"{text}: {ev.detail}"
        return {"kind": "alert", "t": t, "text": text, "level": "error" if ev.kind == "emergency" else "warn",
                "code": ev.kind}
    if isinstance(ev, ConnectionStatus):
        return {"kind": "system", "t": t, "text": ("Sim connected " if ev.connected else "Sim disconnected ") + ev.detail,
                "level": "info" if ev.connected else "warn"}
    if isinstance(ev, SimLifecycle) and ev.kind in ("paused", "unpaused"):
        return {"kind": "phase", "t": t, "text": f"sim {ev.kind}"}
    if isinstance(ev, SessionNote):
        return {"kind": "note", "t": t, "text": ev.text}
    if isinstance(ev, FlightArrived):
        return {"kind": "phase", "t": t, "text": f"arrived: {ev.gate} at {ev.airport}"}
    if isinstance(ev, LlmExchange):
        return None
    return None
