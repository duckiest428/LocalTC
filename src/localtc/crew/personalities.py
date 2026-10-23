"""The copilot's personality: who's in the right seat ([crew] personality). Neutral is the copilot as it always was,
its voice yours to choose; each of the others is a person of their own, with their own voice, pace and liveliness,
their own way of saying the routine calls, and now and then a word of their own after one.

The safety rule, for every one of them: a personality changes only the routine and conversational lines. The
safety calls (stall, overspeed, wind shear, minimums, "not cleared to land", config, the gear, an engine failure,
a wrong runway, anything said at safety priority) keep their standard words, so they're never ambiguous; and the
callouts the pilot acts on ("positive rate", "gear up", "hundred above", "minimums") are said the standard way too.
Nothing a personality says changes a number, a setting or a runway: a routine call's own words are kept in it
("{std}"), or the value it carries ("{value}").

``length`` and ``expressive`` are the voice: Piper's length scale (over 1 slower, a drawl; under 1 quicker,
clipped) and how much it varies (0 flat and even, 1 lively). ``talk`` is how often they add a word of their own.
"""

import random
from dataclasses import dataclass, field

# Never in a personality's words, whatever its priority: the calls that must sound the same in every cockpit.
STANDARD = {
    "stall", "overspeed", "windshear", "flap_speed", "gear_speed", "engine_failure", "engine_failure_roll",
    "wrong_runway", "config", "hold_short", "gear_1000", "no_landing_clearance", "500", "minimums", "100_above",
    "positive_rate", "gear_up", "spoilers", "rollout", "speed_check", "loc_alive", "gs_alive", "fuel_reserve",
    "lined_up", "emergency", "clearance_set", "transition_climb", "transition_descent",
}
# ... though some of these may have a word of their own said after them (never after a safety call).
EXTRAS_AFTER_STANDARD = {"lined_up", "positive_rate"}
# Their word of their own is said below 10,000 feet only after these (the sterile cockpit otherwise).
AFTER_STERILE = {"after_landing", "summary", "park_brake", "1000"}


@dataclass(frozen=True)
class Personality:
    id: str
    name: str
    sex: str  # "F" or "M"; "" for neutral (the voice setting decides)
    style: str  # one line, for the settings and the language model
    sample: str  # a line of theirs, for the settings
    length: float = 1.0
    expressive: float = 0.5
    talk: float = 0.5
    pick: int = 0  # which of that sex's voices
    locale: str = ""  # their English ("en-GB" for a British one), where a voice has it
    story: str = ""  # who they are, for the language model's words
    wordings: dict[str, tuple[str, ...]] = field(default_factory=dict)  # a call's key -> their ways of saying it
    extras: dict[str, tuple[str, ...]] = field(default_factory=dict)  # ... and a word of their own after it
    acks: tuple[str, ...] = ("Check.",)  # "check" their way
    fillers: float = 0.0  # how often an "uh" or "um" starts their own talk (never a callout)
    coach: bool = False  # says how your readbacks went, as an instructor would
    offers_weather: bool = False  # asks in a long cruise whether you'd like the destination's weather

    @property
    def neutral(self) -> bool:
        return self.id == "neutral"

    def voice_sex(self, setting: str) -> str:
        """The [crew] voice_sex the voice is chosen by: theirs, or the setting for neutral."""
        return {"F": "female", "M": "male"}.get(self.sex, setting)

    def voice_pick(self, setting: int) -> int:
        return setting if self.neutral else self.pick


NEUTRAL = Personality("neutral", "Neutral", "", "The copilot as it is: calm, plain and by the book. Its voice is "
                      "yours to choose.", "One thousand, stable.")

PERSONALITIES: tuple[Personality, ...] = (
    NEUTRAL,
    Personality(
        "marcus", "Marcus Hale", "M", "Veteran airline first officer: calm, with a dry sense of humour.",
        "Smooth air. Don't get used to it.", 1.0, 0.5, 0.5, pick=0,
        story="you're Marcus Hale, a first officer with over twenty years on the line: calm, unflappable, a dry sense "
              "of humour used sparingly.",
        wordings={"1000": ("Thousand, stable.",), "turb": ("{std}",), "smooth": ("Smooth again.",),
                  "toc": ("{std}",)},
        extras={"toc": ("Smooth air. Don't get used to it.", "Nice and quiet up here."),
                "turb": ("Hang on to your coffee.",), "after_landing": ("Another one for the logbook.",),
                "summary": ("Not bad for a day's work.",), "10k_climb": ("Cabin can relax now.",)},
        acks=("Check.", "Copy."),
    ),
    Personality(
        "priya", "Priya Nair", "F", "By the book and precise, and warm with it.",
        "Checklist complete. Flaps set, trim set, we're good.", 0.95, 0.4, 0.4, pick=0,
        story="you're Priya Nair, a precise first officer who does everything by the book, warm and encouraging.",
        wordings={"1000": ("One thousand, stable. Continue.",), "smooth": ("Smooth again.",)},
        extras={"checklist_done": ("Flaps set, trim set, we're good.",), "toc": ("Cruise checks done.",),
                "after_landing": ("Nicely done.",)},
        acks=("Check.", "Checked."),
    ),
    Personality(
        "jake", "Jake Mulligan", "M", "Laid-back general aviation pilot, easy-going.",
        "Alright, runway's yours.", 1.05, 0.9, 0.7, pick=1,
        story="you're Jake Mulligan, a laid-back pilot who learned to fly in small planes: easy-going, friendly, "
              "never flustered.",
        wordings={"1000": ("Thousand, stable. Looking good.",),
                  "turb": ("Little bumpy, moderate turbulence. Nothing to write home about.",),
                  "smooth": ("And it's smoothed out.",), "toc": ("{std} Kick back.",)},
        extras={"lined_up": ("Alright, runway's yours.",), "after_landing": ("Nice one.",),
                "summary": ("Easy day.",), "smooth": ("Told you.",)},
        acks=("Check.", "Yep.", "Got it."), fillers=0.25,
    ),
    Personality(
        "elena", "Elena Vargas", "F", "Ex-military: crisp and clipped.",
        "Stable at one thousand. Continue.", 0.9, 0.3, 0.2, pick=1,
        story="you're Elena Vargas, a first officer who flew in the air force before the airline: crisp, clipped, "
              "exact, no small talk.",
        wordings={"1000": ("Stable at one thousand. Continue.",), "turb": ("Moderate turbulence.",),
                  "smooth": ("Smooth.",), "10k_climb": ("{std}",)},
        extras={"summary": ("Good flight.",)},
        acks=("Check.", "Copy."),
    ),
    Personality(
        "oliver", "Oliver Bennett", "M", "British, polite and understated.",
        "Rather bumpy, I'd say. Seatbelt sign's on.", 1.0, 0.6, 0.5, pick=2, locale="en-GB",
        story="you're Oliver Bennett, a British first officer: polite, understated, gently wry.",
        wordings={"turb": ("Rather bumpy, I'd say. Moderate turbulence.",), "smooth": ("That's settled down nicely.",),
                  "1000": ("One thousand, stable.",)},
        extras={"turb": ("Seatbelt sign's on, I'd think.",), "after_landing": ("Lovely.",),
                "summary": ("Splendid. Thank you, captain.",), "toc": ("Rather pleasant up here.",)},
        acks=("Check.", "Righto."),
    ),
    Personality(
        "sofia", "Sofia Lindqvist", "F", "A minimalist: very few words.",
        "Descend ten thousand. Set.", 0.95, 0.3, 0.1, pick=2,
        story="you're Sofia Lindqvist, a first officer of very few words: you say what's needed and nothing else.",
        wordings={"1000": ("Thousand. Stable.",), "turb": ("Moderate turbulence.",), "smooth": ("Smooth.",),
                  "set_alt": ("{value}. Set.",), "set_hdg": ("{value}. Set.",)},
        acks=("Check.",),
    ),
    Personality(
        "dale", "Dale Peterson", "M", "A grizzled grumbler who's seen it all.",
        "Thirty years and they still give me this approach.", 1.1, 0.7, 0.6, pick=3,
        story="you're Dale Peterson, a grizzled first officer with thirty years of flying: grumbles about ATC, the "
              "coffee and the schedule, but sharp and reliable when it counts.",
        wordings={"turb": ("Moderate turbulence. Here we go.",), "smooth": ("About time.",)},
        extras={"approach": ("Thirty years and they still give me this approach.",),
                "tod": ("Here comes the fun part.",), "toc": ("Coffee's cold again.",),
                "after_landing": ("Down and in one piece.",), "summary": ("Another day, another delay.",)},
        acks=("Check.", "Yeah, yeah, check."), fillers=0.15,
    ),
    Personality(
        "maya", "Maya Chen", "F", "Eager newer first officer, enthusiastic.",
        "Okay, stable at a thousand!", 0.95, 0.8, 0.7, pick=3,
        story="you're Maya Chen, a newer first officer, enthusiastic and keen to learn, still loving every flight.",
        wordings={"1000": ("Okay, stable at a thousand!",), "smooth": ("Oh good, smooth again.",),
                  "toc": ("{std} Love this view.",)},
        extras={"after_landing": ("That was a good one.",), "summary": ("That was fun!",),
                "positive_rate": ("Here we go!",)},
        acks=("Check!", "Got it."), fillers=0.2,
    ),
    Personality(
        "tom", "Tom Whitaker", "M", "An instructor and mentor: explains why.",
        "Good readback. Next time, include the runway.", 1.0, 0.5, 0.5, pick=4, coach=True,
        story="you're Tom Whitaker, a training captain flying as first officer today: patient, a mentor, likes to "
              "explain the why behind things briefly.",
        wordings={"1000": ("One thousand, stable. Nicely set up.",)},
        extras={"10k_climb": ("Smooth enough for the cabin to move about.",),
                "tod": ("Start down on time and the approach takes care of itself.",),
                "after_landing": ("Nice and firm, on the centreline.",)},
        acks=("Check.",),
    ),
    Personality(
        "hannah", "Hannah Brooks", "F", "A chatty long-haul companion.",
        "We've got a while. Want the weather at destination?", 1.0, 0.8, 0.8, pick=4, offers_weather=True,
        story="you're Hannah Brooks, a long-haul first officer: chatty, good company on a long night, interested in "
              "the places you fly to.",
        wordings={"smooth": ("That's better, smooth again.",), "toc": ("{std}",)},
        extras={"toc": ("Settle in, we've got a while.",), "after_landing": ("Welcome in.",),
                "summary": ("Good flight. Same time tomorrow?",), "10k_climb": ("Cabin crew will be happy.",)},
        acks=("Check.", "Copy that."), fillers=0.2,
    ),
)
BY_ID = {p.id: p for p in PERSONALITIES}


def get(pid: str) -> Personality:
    return BY_ID.get((pid or "").lower(), NEUTRAL)


def moment(key: str, text: str = "") -> str:
    """The kind of call a key is ("1000:2" -> "1000", "turb:moderate" -> "turb"); a checklist finished is its own."""
    head, _, rest = key.partition(":")
    if head == "turb":
        return "smooth" if rest.startswith("smooth") else "turb" if rest.startswith("moderate") else "turb_" + rest
    if head == "run" and text.rstrip().lower().endswith("checklist complete."):
        return "checklist_done"
    if head == "atis" and "approach" in text.lower():
        return "approach"
    return head


def word(p: Personality, key: str, text: str, *, priority: int, safety: int, rng: random.Random,
         sterile: bool, talk_scale: float = 1.0) -> tuple[str, bool]:
    """The call ``text`` (under ``key``, at ``priority``) as ``p`` says it; True when it's no longer the standard
    words (so its spoken form is made from it again). Safety calls and the standard ones come back as they are,
    apart from a word of their own after one of the few that may have it."""
    if p.neutral or not text or priority >= safety:
        return text, False
    m = moment(key, text)
    out = text
    ways = p.wordings.get(m, ()) if m not in STANDARD else ()
    if not text.endswith(" set."):  # "{value}" only where the call is "<value> set." ("8,000, yours to set" isn't)
        ways = tuple(w for w in ways if "{value}" not in w)
    if ways:
        out = rng.choice(ways).replace("{std}", text).replace("{value}", text.removesuffix(" set."))
    if m in STANDARD and m not in EXTRAS_AFTER_STANDARD:
        return out, out != text
    extra = p.extras.get(m)
    if extra and (not sterile or m in AFTER_STERILE) and rng.random() < p.talk * talk_scale:
        out = f"{out.rstrip()} {rng.choice(extra)}"
    return out, out != text


def ack(p: Personality, rng: random.Random, standard: str = "Check.") -> str:
    """Their "check"."""
    return standard if p.neutral else rng.choice(p.acks)


def chat(p: Personality, text: str, rng: random.Random) -> str:
    """Their own talk (an answer, a greeting) with an "uh" or "um" at the start now and then, as they'd say it."""
    if p.fillers <= 0 or not text or rng.random() >= p.fillers or len(text.split()) < 4:
        return text
    return f"{rng.choice(('Uh', 'Um', 'So'))}, {text[:1].lower()}{text[1:]}" if not text[:2].isupper() else text
