"""Aviation vocabulary for Whisper: a prompt built from the flight, and fixes for common mishearings.

Whisper's ``initial_prompt`` is text it treats as what came before, so it picks up the words and
the writing style (digits, "06L", "5,000") from it. The prompt names the callsign, stations,
airports and every runway at the airports in play, but never the numbers ATC just gave: priming
Whisper with the expected squawk could make it hear the right number when the pilot said a wrong
one, and hide a readback error.
"""

import re
from dataclasses import dataclass, field

# A style primer: standard phrasing with neutral numbers, written the way the parser reads best.
STYLE = (
    "Cleared to the airport as filed, climb and maintain 5,000, squawk 4521. Runway 34L, taxi via Alpha, "
    "Bravo 2, hold short runway 16R. Holding short, ready for departure. Line up and wait. Cleared for takeoff. "
    "Contact departure 124.675. With you at 3,000 climbing 7,000. Descend and maintain flight level 240. "
    "Cleared ILS runway 14R approach. Cleared to land. Wilco. Roger. Say again. Niner. Request higher. "
    "Request direct. Request deviation 20 degrees left for weather. Say the winds. What's the altimeter? "
    "Unable. Mayday, engine failure, 2 souls on board, 1 hour of fuel. Pan-pan."
)
MAX_PROMPT_CHARS = 700  # Whisper keeps the last 224 prompt tokens; stay well inside


@dataclass(frozen=True)
class VocabularyHints:
    callsign: str = ""  # as written, e.g. "DP69" or "N172LT"
    callsign_spoken: str = ""  # e.g. "Delta Papa 69", "Cessna 2 Lima Tango"
    stations: tuple[str, ...] = ()  # "Montreal Tower", "Quebec Ground", ...
    airports: tuple[str, ...] = ()  # "Montreal", "Quebec"
    runways: tuple[str, ...] = ()  # every runway end at the airports in play, e.g. "06L", "24R"
    taxiways: tuple[str, ...] = ()  # names at the current airport
    extra: tuple[str, ...] = field(default_factory=tuple)


def build_prompt(hints: VocabularyHints) -> str:
    parts = [STYLE]
    if hints.stations:
        parts.append("Stations: " + ", ".join(dict.fromkeys(hints.stations)) + ".")
    if hints.airports:
        parts.append("Airports: " + ", ".join(dict.fromkeys(hints.airports)) + ".")
    if hints.runways:
        parts.append("Runways " + ", ".join(dict.fromkeys(hints.runways)) + ".")
    if hints.taxiways:
        parts.append("Taxiways " + ", ".join(list(dict.fromkeys(hints.taxiways))[:20]) + ".")
    parts += list(hints.extra)
    if hints.callsign:
        spoken = f" ({hints.callsign_spoken})" if hints.callsign_spoken and hints.callsign_spoken != hints.callsign else ""
        parts.append(f"This is {hints.callsign}{spoken}.")  # last: nearest to the audio, strongest effect
    prompt = " ".join(parts)
    return prompt[-MAX_PROMPT_CHARS:] if len(prompt) > MAX_PROMPT_CHARS else prompt


def hotwords(hints: VocabularyHints) -> str:
    """Words worth boosting on their own: the callsign and station names."""
    words = [hints.callsign, hints.callsign_spoken, *hints.stations]
    return " ".join(w for w in dict.fromkeys(words) if w)


# Mishearings seen in aviation speech-to-text, as (pattern, replacement), whole words, case-insensitive.
FIXUPS: list[tuple[str, str]] = [
    (r"\bwhole short\b|\bhold shirt\b|\bhole short\b", "hold short"),
    (r"\bsquak\b|\bsquaw\b|\bsquat\b|\bsquack\b|\bskwak\b|\bsquawks\b", "squawk"),
    (r"\bdecent and maintain\b|\bdescent and maintain\b", "descend and maintain"),
    (r"\bclimate and maintain\b|\bclimate maintain\b|\bclimbing maintain\b|\bline and maintain\b", "climb and maintain"),
    (r"\bline up and weight\b|\bline up and wade\b|\blineup and wait\b", "line up and wait"),
    (r"\bwill co\b|\bwilko\b|\bwill comply\b", "wilco"),
    (r"\bmay day\b", "mayday"),
    (r"\bpan pan\b", "pan-pan"),
    (r"\brun way\b|\brunaway\b", "runway"),
    (r"\btake-off\b", "takeoff"),
    (r"\bfl ?(\d{2,3})\b", r"flight level \1"),
    (r"\bcleared for the takeoff\b", "cleared for takeoff"),
    (r"\bi\.l\.s\.?", "ILS"),
    (r"\bready for departures\b", "ready for departure"),
    (r"\b(\d{1,2}),000,000 minutes\b", r"\1,000, 10 minutes"),  # "expect 12,000 one zero minutes" run together
    (r"(\d)er\b", r"\1"),  # "6-9er"
    (r"\b(\d),? (\d),?000\b", r"\1\g<2>000"),  # "level 1 2000", "1, 2000": one two thousand
    (r"\bshort (?:file|finale|fine|find)\b", "short final"),
    (r"\b(?:r-?naf|r-?nav|rnf|rna)\b", "RNAV"),
    (r"\btime (?:and )?maintain\b", "climb and maintain"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in FIXUPS]


DIGIT_RUN = re.compile(r"\b(\d+)-(\d+)\b")


def fixup(text: str) -> str:
    """Correct well-known mishearings; everything else is left for the parser and the model."""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    while DIGIT_RUN.search(text):  # Whisper hyphenates digits said one by one: "1-2000", "0-6", "5-0-1-5"
        text = DIGIT_RUN.sub(r"\1\2", text)
    return " ".join(text.split())
