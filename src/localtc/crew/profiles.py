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

    def matches(self, title: str, model: str) -> bool:
        words = f"{title} {model}".upper()
        return any(m.upper() in words for m in self.match)

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
    return next((p for p in profiles if p.matches(title, model)), next((p for p in profiles if not p.match), Profile()))
