"""Aircraft profiles: how this aircraft's flaps are named and moved, its placard speeds, and any action sent some
other way than the sim's standard key event (an add-on's own event or L:var).

A profile is a TOML file in ``crew/profiles/`` (shipped) or ``%LOCALAPPDATA%\\LocalTC\\profiles`` (the pilot's own,
which win). The aircraft is matched by its title or ATC model ("A20N", "Airbus A320neo"); anything else gets
``stock``: standard key events, detents counted from the aircraft, no placard speeds.
"""

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SHIPPED = Path(__file__).with_name("profiles")


@dataclass(frozen=True)
class Write:
    """One action sent another way: a key event (``event``, with ``value``), or an L:var (``lvar``) set to
    ``on``/``off`` or the action's value."""

    event: str = ""
    value: int | None = None
    lvar: str = ""
    input: str = ""  # an MSFS 2024 input event by name ("LIGHTING_LANDING_1"), set to ``on``/``off`` or the value
    unit: str = "number"
    on: float = 1.0
    off: float = 0.0


@dataclass(frozen=True)
class Profile:
    name: str = "stock"
    match: tuple[str, ...] = ()
    flaps: str = "set"  # "set": FLAPS_SET with the handle position; "detents": FLAPS_UP, FLAPS_1..3, FLAPS_DOWN
    detents: tuple[str, ...] = ()  # names by handle index ("up", "1", "2", "3", "full"); () counted from the aircraft
    aliases: dict[str, str] = field(default_factory=dict)
    vfe: tuple[float, ...] = ()  # the highest speed for each detent (0: none)
    gear_extend_kt: float = 0.0  # 0: unknown, not checked
    gear_retract_kt: float = 0.0
    gear_extended_kt: float = 0.0
    first_detent_ground: str = ""  # how the first detent is said on the ground ("1+F")
    actions: dict[str, Write] = field(default_factory=dict)
    # For the copilot's callouts: the takeoff speed check ("eighty knots"; Airbus "one hundred knots"), the rollout
    # call ("sixty knots"; Airbus "seventy knots"), the speed limit (0: the sim's overspeed warning only), whether
    # there are ground spoilers to call, the lowest detent that's landing flaps ("": the last but one), the fastest
    # taxi.
    speed_check_kt: int = 80
    rollout_call_kt: int = 60
    vmo_kt: float = 0.0
    spoilers: bool = False
    landing_detent: str = ""
    taxi_max_kt: float = 30.0
    # The autobrake switch's positions by the sim's AUTO BRAKE SWITCH CB value ("off", "low", ...): () unknown, and then
    # nothing is said about the autobrake rather than a guess. Reversers: True/False, or None to go by the engines
    # (jets have them). The highest level a step climb is suggested to, and the runway below which a landing without
    # the autobrake gets a word.
    autobrake: tuple[str, ...] = ()
    reversers: bool | None = None
    ceiling_ft: int = 41000
    short_runway_ft: int = 7000
    # What the copilot can do and see in this aircraft. ``hands``: its switches move with the sim's events (an add-on
    # with its own systems, the FSLabs Airbus, ignores them: the copilot calls, it doesn't reach). ``reads_flaps`` /
    # ``reads_autopilot``: the sim's flap handle and autopilot variables follow the aircraft's own (not in the FSLabs).
    hands: bool = True
    reads_flaps: bool = True
    reads_autopilot: bool = True

    def autobrake_name(self, position: int) -> str:
        """The switch position as said ("medium"), or "" when this aircraft's positions aren't known."""
        return self.autobrake[position] if 0 <= position < len(self.autobrake) else ""

    def matches(self, title: str, model: str) -> bool:
        return self.match_length(title, model) > 0

    def match_length(self, title: str, model: str) -> int:
        """How specific the match is: the longest of its names in the title or model (0: none). "FSLabs" in "FSLabs
        A321-211" beats "A321"."""
        words = f"{title} {model}".upper()
        return max((len(m) for m in self.match if m and m.upper() in words), default=0)

    def detent_index(self, name: str, positions: int) -> int | None:
        """The handle index of a detent as the pilot says it ("2", "full", "one plus f", "up"), or None."""
        name = self.aliases.get(name.lower(), name.lower())
        if self.detents:
            return self.detents.index(name) if name in self.detents else None
        if name in ("up", "zero", "0"):
            return 0
        if name == "full":
            return positions or None
        return int(name) if name.isdigit() and (not positions or int(name) <= positions) else None

    def detent_name(self, index: int, positions: int, *, on_ground: bool = False) -> str:
        if index == 1 and on_ground and self.first_detent_ground:
            return self.first_detent_ground
        if self.detents and 0 <= index < len(self.detents):
            return self.detents[index]
        if index == 0:
            return "up"
        return "full" if positions and index == positions and positions > 1 else str(index)

    def vfe_for(self, index: int) -> float:
        return self.vfe[index] if 0 <= index < len(self.vfe) else 0.0

    def landing_index(self, positions: int) -> int:
        """The lowest handle position that's landing flaps."""
        if self.landing_detent and (i := self.detent_index(self.landing_detent, positions)) is not None:
            return i
        return max(positions - 1, 1)


def parse(data: dict) -> Profile:
    a = data.get("aircraft", {})
    actions = {name: Write(**{k: v for k, v in spec.items() if k in Write.__dataclass_fields__})
               for name, spec in data.get("actions", {}).items()}
    return Profile(
        name=str(a.get("name", "stock")), match=tuple(a.get("match", ())), flaps=str(a.get("flaps", "set")),
        detents=tuple(str(d).lower() for d in a.get("detents", ())),
        aliases={str(k).lower(): str(v).lower() for k, v in a.get("aliases", {}).items()},
        vfe=tuple(float(v) for v in a.get("vfe", ())), gear_extend_kt=float(a.get("gear_extend_kt", 0)),
        gear_retract_kt=float(a.get("gear_retract_kt", 0)), gear_extended_kt=float(a.get("gear_extended_kt", 0)),
        first_detent_ground=str(a.get("first_detent_ground", "")), actions=actions,
        speed_check_kt=int(a.get("speed_check_kt", 80)), rollout_call_kt=int(a.get("rollout_call_kt", 60)),
        vmo_kt=float(a.get("vmo_kt", 0)), spoilers=bool(a.get("spoilers", False)),
        landing_detent=str(a.get("landing_detent", "")).lower(), taxi_max_kt=float(a.get("taxi_max_kt", 30)),
        autobrake=tuple(str(x).lower() for x in a.get("autobrake", ())),
        reversers=bool(a["reversers"]) if "reversers" in a else None, ceiling_ft=int(a.get("ceiling_ft", 41000)),
        short_runway_ft=int(a.get("short_runway_ft", 7000)), hands=bool(a.get("hands", True)),
        reads_flaps=bool(a.get("reads_flaps", True)), reads_autopilot=bool(a.get("reads_autopilot", True)),
    )


def load_all(user_dir: Path | None = None) -> list[Profile]:
    """The pilot's profiles first (they win), then the shipped ones; ``stock`` last."""
    out: list[Profile] = []
    for directory in (user_dir, SHIPPED):
        if directory is None or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.toml")):
            try:
                out.append(parse(tomllib.loads(path.read_text(encoding="utf-8"))))
            except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
                log.warning("Aircraft profile %s not used: %s", path.name, exc)
    return sorted(out, key=lambda p: not p.match)  # the catch-all stock profile after the specific ones


def for_aircraft(profiles: list[Profile], title: str, model: str) -> Profile:
    """The profile that names the aircraft most specifically (the pilot's own first on a tie), else ``stock``."""
    best = max(profiles, key=lambda p: p.match_length(title, model), default=None)
    if best is not None and best.match_length(title, model) > 0:
        return best
    return next((p for p in profiles if not p.match), Profile())
