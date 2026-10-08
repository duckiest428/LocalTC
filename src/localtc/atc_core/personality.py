"""Who's on the frequency: each controller a person, the same one every time.

The words ATC must say come from the templates (and the engine decides them); around them, real controllers differ.
Each station has a controller with a manner of their own: calm, formal, friendly, strict, hurried, dry or
conversational. It decides

- how they open and close: "good evening" on the first call or nothing, "good day", "have a good one", "see ya";
- how they acknowledge ("roger", "copy that", "roger, thanks") and ask for a repeat ("say again", "sorry, say again?");
- how they correct, and how patient they are with the same mistake twice ("negative, I say again, ...");
- the length and rhythm of their sentences, and their habits of speech, for the language model's wording;
- how they sound: their pace and cadence on the voice (``tts.voices``, from ``manner``).

How busy the frequency is (``workload``) shifts it: a busy controller greets less, chats less and talks faster.

A controller is picked from the station's name and the shift (``profile``): Denver Center is the same person all
flight, and in its replay, and not the same as Salt Lake Center next door. A flight 5 hours or more after the last
one is a new shift (``[atc] shift``, moved on by the app): other people, and other voices, at every station. The kind follows the role (a tower is more often hurried or
strict, a ground controller friendly or dry) with the name choosing among them; the rest has small variations of its
own. Only the words around the instruction change: what's read back, the numbers, the runways and the routes never
do. The engine's checks see the result the same as any other.
"""

import zlib
from dataclasses import dataclass

KINDS = ("calm", "formal", "friendly", "strict", "hurried", "dry", "conversational")
ROLE_KINDS = {
    "clearance": ("formal", "calm", "friendly", "dry", "formal"),
    "ground": ("friendly", "conversational", "dry", "hurried", "calm"),
    "tower": ("hurried", "strict", "calm", "formal", "friendly"),
    "departure": ("calm", "friendly", "dry", "formal", "conversational"),
    "approach": ("calm", "strict", "hurried", "dry", "formal"),
    "center": ("calm", "dry", "conversational", "friendly", "formal"),
}


@dataclass(frozen=True)
class Traits:
    acks: tuple[str, ...]  # "roger" said this controller's way: the acknowledgement on its own
    say_again: tuple[str, ...]  # asking for a repeat (each says "say again")
    greetings: tuple[str, ...]  # "{when}" is morning, afternoon or evening
    sign_offs: tuple[str, ...]
    icao_sign_offs: tuple[str, ...]
    greets: float  # how often the first call opens with a greeting (quiet frequency)
    signs_off: float  # how often a handoff closes with a sign-off
    sentences: str  # short, medium, long: for the model's wording
    verbosity: int  # 0 terse, 1 normal, 2 chatty: small talk, extra words
    correction: str  # plain, firm (says it again firmly), gentle (softens it)
    patience: int  # the same thing wrong this many times before the correction gets firmer
    pace: float  # the voice's speed (under 1 slower)
    expressive: float  # the voice's variation, added to the role's
    habits: tuple[str, ...]  # words of theirs the model may use (never an instruction)
    manner: str  # one line on how they come across, for the model


TRAITS: dict[str, Traits] = {
    "calm": Traits(("roger",), ("say again",), ("good {when}",), ("good day", "have a good flight"), ("good day",),
                   0.6, 0.7, "medium", 1, "plain", 3, 0.96, 0.0, ("no rush", "when able"),
                   "unhurried and steady, an even voice that never sounds rushed"),
    "formal": Traits(("roger",), ("say again your last transmission", "say again"), ("good {when}",), ("good day",),
                     ("good day", "goodbye"), 0.75, 0.8, "medium", 0, "firm", 2, 0.98, -0.05, (),
                     "correct and by the book, standard phraseology and nothing else"),
    "friendly": Traits(("roger, thanks", "copy that", "roger"), ("sorry, say again?", "say again"),
                       ("good {when}", "hello"), ("have a good one", "have a good flight", "see ya"), ("good day", "bye bye"),
                       0.85, 0.85, "medium", 2, "gentle", 3, 1.0, 0.05, ("thanks", "appreciate it", "no problem"),
                       "warm and easy-going, a thank-you here and there"),
    "strict": Traits(("roger",), ("say again",), ("good {when}",), ("good day",), ("good day",),
                     0.3, 0.5, "short", 0, "firm", 1, 1.03, -0.03, ("expect no delay",),
                     "brisk and exacting, expects a proper readback the first time"),
    "hurried": Traits(("roger",), ("say again",), ("good {when}",), ("good day", "bye"), ("good day",),
                      0.2, 0.35, "short", 0, "plain", 2, 1.08, 0.0, ("no delay",),
                      "quick and clipped, a busy frequency, every word earns its place"),
    "dry": Traits(("copy", "roger"), ("say again",), ("{when}",), ("good day",), ("good day",),
                  0.35, 0.55, "short", 1, "plain", 2, 1.01, -0.06, ("as filed",),
                  "laconic, matter-of-fact, a deadpan word or two"),
    "conversational": Traits(("roger, thanks", "copy that, thanks", "roger"), ("sorry, you were cut out, say again?", "say again"),
                             ("good {when}", "good {when}, welcome"), ("have a good one", "enjoy the flight", "good day"),
                             ("good day", "bye bye"), 0.9, 0.9, "long", 2, "gentle", 3, 0.97, 0.06,
                             ("thanks", "nice and easy", "appreciate the help"),
                             "chatty and personable, likes a word with the crews when it's quiet"),
}


@dataclass(frozen=True)
class Personality:
    """A controller: the station, their kind and its traits, and small touches of their own."""

    station: str
    role: str
    kind: str
    icao: bool
    traits: Traits
    greets: float
    signs_off: float
    sign_off: str  # their usual one
    ack: str  # their usual acknowledgement
    pace: float  # the voice's speed, with their own touch on the kind's

    @property
    def manner(self) -> str:
        """For the voice: "tower:hurried" (the role's cadence, the kind's on top)."""
        return f"{self.role}:{self.kind}"

    def describe(self, workload: str = "normal") -> str:
        """For the language model: who's talking and how (never what to say)."""
        t = self.traits
        busy = {"busy": " The frequency is busy right now: shorter than usual, no small talk.",
                "quiet": " It's quiet right now: a little more relaxed."}.get(workload, "")
        habits = f" Words they use: {', '.join(t.habits)}." if t.habits else ""
        sentences = {"short": "short, clipped sentences", "medium": "plain, medium-length sentences",
                     "long": "fuller sentences"}[t.sentences]
        return (f"The controller at {self.station} is {self.kind}: {t.manner}. They speak in {sentences}"
                f"{' and use standard ICAO wording' if self.icao else ''}. They acknowledge with \"{self.ack}\"."
                f"{habits}{busy} Their manner shapes the words, never the instruction: every value, runway, route, "
                "frequency and readback stays exactly as decided.")


def shift_key(station: str, shift: int = 0) -> str:
    """Who's on at ``station`` this shift: the key their manner and voice are picked by (the name on shift 0)."""
    return f"{station} #{shift}" if shift else station


def profile(station: str, role: str = "", *, icao: bool = False, shift: int = 0) -> Personality:
    """The controller at ``station`` (its ``role``: clearance, ground, tower, departure, approach, center) on
    ``shift``: from the name and the shift, the same every time."""
    h = zlib.crc32(shift_key(station, shift).lower().encode())
    kinds = ROLE_KINDS.get(role, KINDS)
    kind = kinds[h % len(kinds)]
    t = TRAITS[kind]
    offs = t.icao_sign_offs if icao else t.sign_offs
    vary = ((h >> 8) % 21 - 10) / 100  # their own touch: up to 10 % either way on how often they greet and sign off
    return Personality(
        station=station, role=role, kind=kind, icao=icao, traits=t,
        greets=min(0.95, max(0.05, t.greets + vary)), signs_off=min(0.95, max(0.05, t.signs_off - vary)),
        sign_off=offs[(h >> 16) % len(offs)], ack=t.acks[(h >> 20) % len(t.acks)],
        pace=round(t.pace * (0.98 + ((h >> 24) % 5) / 100), 3),
    )


def personality(station: str, *, icao: bool = False, role: str = "") -> Personality:
    """(The older name.)"""
    return profile(station, role, icao=icao)


def workload(transmissions_5min: int, traffic_nearby: int) -> str:
    """How busy the controller is: from what's been said on the frequency lately and the traffic around."""
    score = transmissions_5min + traffic_nearby / 3
    return "busy" if score >= 8 else "quiet" if score <= 2 else "normal"


STYLE_DRIFT = 0.12  # how often a controller says it another (equally correct) way than usual


def wording(station: str, instruction_id: str, n: int, rng) -> int:
    """Which of ``n`` correct wordings of an instruction this controller uses: the same one nearly every time
    (it's their habit, and Montreal Ground sounds unlike Denver Ground), now and then another."""
    if n <= 1:
        return 0
    if rng is not None and rng.random() < STYLE_DRIFT:
        return rng.randrange(n)
    return zlib.crc32(f"{station.lower()}|{instruction_id}".encode()) % n


def part_of_day(zulu_s: float | None, lon: float) -> str | None:
    """ "morning", "afternoon" or "evening" by the sun where the aircraft is (None without a time)."""
    if zulu_s is None:
        return None
    hour = (zulu_s / 3600 + lon / 15) % 24
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening"


def greet(text: str, callsign: str, station: str, words: str) -> str:
    """ "Delta 2543, Denver Departure, radar contact" -> "Delta 2543, Denver Departure, good afternoon, radar
    contact"; without the station named, the greeting follows the callsign."""
    head = f"{callsign}, "
    if not text.startswith(head):
        return text
    rest = text[len(head):]
    if rest.startswith(f"{station}, "):
        head, rest = f"{head}{station}, ", rest[len(station) + 2:]
    return f"{head}{words}, {rest}"


def sign_off(text: str, words: str) -> str:
    """ "..., contact Seattle Center 132.6." -> "..., contact Seattle Center 132.6, good day." (once)."""
    lowered = text.lower()
    if not words or any(phrase in lowered for phrase in ("good day", "good night", "have a good", "so long", "see ya", "bye",
                                                         "enjoy the flight")):
        return text
    return f"{text.rstrip('.')}, {words}."


def acknowledge(text: str, callsign: str, ack: str) -> str:
    """ "Delta 2543, roger." -> "Delta 2543, roger, thanks." in this controller's way (only the bare acknowledgement)."""
    head = f"{callsign}, "
    if not text.startswith(head):
        return text
    rest = text[len(head):].rstrip(".").strip().lower()
    if rest not in ("roger", "copy that", "copy"):
        return text
    return f"{head}{ack}."


def say_again(text: str, callsign: str, words: str) -> str:
    """ "Delta 2543, say again." in this controller's words (each still says "say again")."""
    head = f"{callsign}, "
    if not text.startswith(head) or text[len(head):].rstrip(".").strip().lower() != "say again" or "say again" not in words:
        return text
    return f"{head}{words}" + ("" if words.endswith("?") else ".")


def firmer(text: str, callsign: str) -> str:
    """The same correction, a second time: "negative, I say again, taxi via ..."."""
    head = f"{callsign}, negative, "
    if text.startswith(head) and "i say again" not in text.lower():
        return f"{head}I say again, {text[len(head):]}"
    head = f"{callsign}, read back "
    if text.startswith(head):
        # Only what's missing is wanted: "I need a full readback, read back frequency 125.2" asked for both.
        return f"{callsign}, I still need the readback of {text[len(head):].rstrip('.')}."
    return text


def gentler(text: str, callsign: str) -> str:
    """A correction softened, not changed: "negative, it's taxi via ..." reads oddly, so only "not quite" ahead."""
    head = f"{callsign}, negative, "
    if text.startswith(head):
        return f"{callsign}, not quite, negative, {text[len(head):]}"
    return text
