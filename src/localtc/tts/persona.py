"""Who is speaking, whichever synthesizer gives them a voice: a ``Persona`` (role, sex, regional English, pace, pitch,
manner), decided from the station's name alone, so the same station is the same person in every flight, every replay
and every provider. Each provider then casts the persona from its own voices (``Casting``), and a substitute (a cloud
voice unavailable, Piper standing in) keeps the person's sex and manner.

Roles: ATC (each station; with ``[atc] personalities``, whoever is on shift there), the ATIS recording, other pilots
on the frequency, the copilot, and the cabin and ground crew (reserved: nothing speaks for them yet).
"""

import zlib
from dataclasses import dataclass

from localtc.tts.voices import SPEAKERS, delivery_for, sex_of, speaker_for

ROLES = ("atc", "atis", "chatter", "copilot", "cabin", "ground_crew")
# The voice key of a role that isn't a station: one person each.
ROLE_KEYS = {"copilot": "pilot", "cabin": "cabin crew", "ground_crew": "ground crew"}
# The controller's manner (atc_core.personality's kinds) as a speaking style, where a voice has one (Azure's
# express-as); only the even ones: no voice sounds cheerful or angry on the radio.
STYLES = {"calm": "calm", "friendly": "friendly", "conversational": "friendly", "formal": "serious",
          "strict": "serious", "dry": "serious"}
PITCH_SPAN = 6  # a station's voice up to this many percent higher or lower (where a provider can)
LIBRITTS = 904  # the default Piper voice's speakers, whose sexes are known (voices.SPEAKER_SEX)


@dataclass(frozen=True)
class Persona:
    key: str  # the station ("Phoenix Tower", "Phoenix Tower #2" on a later shift) or role key ("pilot")
    role: str = "atc"
    manner: str = ""  # how they speak, for Piper's knobs ("tower:hurried:busy"; voices.delivery_for)
    sex: str = "M"  # "F" or "M"
    locale: str = ""  # regional English ("en-GB", "en-AU", ...); "" none in particular
    pace: float = 1.0  # times the configured speaking rate
    pitch: int = 0  # percent, where a provider can change it
    style: str = ""  # a speaking style where a voice has one ("calm", "friendly", "serious")
    pick: int | None = None  # the pilot's own choice among a provider's voices of that sex (the copilot's voice)
    piper_speaker: int | None = None  # the Piper speaker chosen for it (the copilot's, from the settings)

    @property
    def crew(self) -> bool:
        return self.role in ("copilot", "cabin", "ground_crew")


def _hash(text: str) -> int:
    return zlib.crc32(text.lower().encode())


def sex_for(key: str) -> str:
    """The sex of the person at ``key``: the one Piper's default voice gives them (so a substitute keeps it), else
    an even split by name."""
    found = sex_of(speaker_for(key, LIBRITTS, SPEAKERS))
    return found or ("F" if _hash("sex" + key) % 2 else "M")


def persona_for(key: str, role: str = "atc", *, manner: str = "", locale: str = "", sex: str = "",
                pick: int | None = None, piper_speaker: int | None = None) -> Persona:
    """The person speaking as ``key`` in ``role``. ``manner``: the transmission's ("tower:hurried:busy"); ``locale``:
    the region's English ("" none); ``sex`` "F"/"M" to choose (the copilot's setting), else decided from the key."""
    if role not in ROLES:
        role = "atc"
    key = key or ROLE_KEYS.get(role, role)
    want = sex.upper()[:1] if sex.upper()[:1] in ("F", "M") else ""
    if not want and piper_speaker is not None:
        want = sex_of(piper_speaker)
    kind = "pilot" if role in ("copilot", "cabin", "ground_crew") else "atis" if role == "atis" else \
        "chatter" if role == "chatter" else manner or "atc"
    pace = delivery_for(key, kind).pace
    _, _, rest = manner.partition(":")
    style = "" if role in ("atis", "copilot") else STYLES.get(rest.partition(":")[0], "")
    pitch = 0 if role in ("atis", "copilot") else _hash("pitch" + key) % (2 * PITCH_SPAN + 1) - PITCH_SPAN
    return Persona(key=key, role=role, manner=kind, sex=want or sex_for(key), locale=locale, pace=pace, pitch=pitch,
                   style=style, pick=pick, piper_speaker=piper_speaker)


class Casting:
    """A provider's voices handed out to the people of a flight: each person keeps theirs, and no two share one while
    the provider has another free. Each person's first choice is decided by their name, the next free one after it
    when taken, so a flight (and its replay, the stations met in the same order) is cast the same every time."""

    def __init__(self) -> None:
        self.given: dict[str, str] = {}  # person -> voice
        self.taken: dict[str, str] = {}  # voice -> person

    def cast(self, persona: Persona, pool: list[str]) -> str:
        if persona.key in self.given:
            return self.given[persona.key]
        if not pool:
            raise ValueError("no voices to cast from")
        start = persona.pick % len(pool) if persona.pick is not None else _hash(persona.key) % len(pool)
        voice = pool[start]
        if persona.pick is None:  # the pilot's own pick stands even if a controller has it
            for i in range(len(pool)):
                candidate = pool[(start + i) % len(pool)]
                if candidate not in self.taken:
                    voice = candidate
                    break
        self.given[persona.key] = voice
        self.taken.setdefault(voice, persona.key)
        return voice

    def reserve(self, persona: Persona, pool: list[str]) -> str:
        """The copilot's voice first, before any controller can take it."""
        return self.cast(persona, pool)
