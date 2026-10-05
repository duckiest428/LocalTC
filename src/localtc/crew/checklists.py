"""The checklists the copilot reads, checked against the aircraft.

A checklist is read as a flow: each item with what the copilot sees ("Flaps, one plus F."). An item on the copilot's
side that isn't set yet (lights, the transponder code, the altimeter) is set as it reads it, when it has the hands for
it; one on the captain's side that isn't right stops the checklist ("Flaps: up, plan says 1+F. Holding the
checklist.") until the aircraft shows it, and then it's finished. Items it can't see in the sim aren't read at all:
nothing is ever answered for the pilot without looking.
"""

from collections.abc import Callable
from dataclasses import dataclass

from localtc.atc_core.phraseology import speech
from localtc.crew.actions import Cockpit
from localtc.crew.commands import Command

NAMES = {
    "before_start": "Before start", "after_start": "After start", "before_taxi": "Before taxi",
    "before_takeoff": "Before takeoff", "after_takeoff": "After takeoff", "descent": "Descent",
    "landing": "Landing", "after_landing": "After landing", "shutdown": "Shutdown",
}
# The words a pilot says for each ("run the before takeoff checklist", "landing checklist").
SPOKEN = (("before start", "before_start"), ("after start", "after_start"), ("before taxi", "before_taxi"),
          ("taxi", "before_taxi"), ("before takeoff", "before_takeoff"), ("before take off", "before_takeoff"),
          ("lineup", "before_takeoff"), ("line up", "before_takeoff"), ("after takeoff", "after_takeoff"),
          ("after take off", "after_takeoff"), ("climb", "after_takeoff"), ("descent", "descent"),
          ("approach", "descent"), ("landing", "landing"), ("after landing", "after_landing"),
          ("shutdown", "shutdown"), ("parking", "shutdown"), ("securing", "shutdown"))


@dataclass(frozen=True)
class Item:
    """One line: ``state`` says what the copilot sees, and whether it's right ((words, ok); None: can't tell, the
    item isn't read). ``fix`` is the command that sets it, for an item on the copilot's side."""

    challenge: str
    state: Callable[[Cockpit, "Context"], tuple[str, bool] | None]
    fix: Callable[[Cockpit, "Context"], Command | None] | None = None


@dataclass
class Context:
    """What the checklists check against besides the aircraft: the clearance and the plan."""

    squawk: str | None = None
    altimeter_inhg: float | None = None  # the local setting from the ATIS, below the transition
    takeoff_flaps: str = ""  # the plan's ("1+F", "5")
    landing_flaps: str = ""
    runway: str | None = None


def _own(get):
    return lambda c, x: None if c.own is None else get(c, x)


def _sys(get):
    return lambda c, x: None if c.systems is None else get(c, x)


def _flaps_named(c: Cockpit) -> str:
    own = c.own
    return c.profile.detent_name(own.flaps_index, c.flap_positions, on_ground=own.on_ground) if own else ""


def _say_flaps(name: str) -> str:
    return name.replace("+F", " plus F").replace("+f", " plus F")


def _flaps_takeoff(c: Cockpit, x: Context) -> tuple[str, bool] | None:
    if c.own is None:
        return None
    name = _flaps_named(c)
    if c.own.flaps_index == 0 and c.flap_positions:
        return ("up, set them for takeoff" + (f", plan says {_say_flaps(x.takeoff_flaps)}" if x.takeoff_flaps else ""), False)
    if x.takeoff_flaps and _norm(x.takeoff_flaps) not in (_norm(name), _norm(name.replace("+F", ""))):
        return (f"{_say_flaps(name)}, plan says {_say_flaps(x.takeoff_flaps)}", False)
    return _say_flaps(name), True


def _flaps_landing(c: Cockpit, x: Context) -> tuple[str, bool] | None:
    if c.own is None or not c.flap_positions:
        return None
    name = _flaps_named(c)
    landing = c.own.flaps_index >= max(c.flap_positions - 1, 1)
    if x.landing_flaps and _norm(x.landing_flaps) == _norm(name):
        landing = True
    return (_say_flaps(name), True) if landing else (f"{_say_flaps(name)}, not landing flaps yet", False)


def _norm(name: str) -> str:
    return name.lower().replace(" ", "").replace("full", "full")


def _light(attr: str, want: bool, words: str):
    def state(c: Cockpit, x: Context) -> tuple[str, bool] | None:
        if c.systems is None:
            return None
        on = getattr(c.systems, f"light_{attr}")
        return ("on" if on else "off"), on == want
    def fix(c: Cockpit, x: Context) -> Command | None:
        return Command("light", "on" if want else "off", attr)
    return Item(words, state, fix)


def _squawk(c: Cockpit, x: Context) -> tuple[str, bool] | None:
    if c.own is None or not x.squawk:
        return None
    return speech.squawk(c.own.squawk) if c.own.squawk == x.squawk else c.own.squawk, c.own.squawk == x.squawk


def _altimeter(c: Cockpit, x: Context) -> tuple[str, bool] | None:
    if c.own is None or not x.altimeter_inhg:
        return None
    ok = abs(c.own.altimeter_inhg - x.altimeter_inhg) <= 0.015
    return (speech.altimeter_display(c.own.altimeter_inhg) if ok else f"{c.own.altimeter_inhg:.2f}, should be "
            f"{speech.altimeter_display(x.altimeter_inhg)}"), ok


def _altimeter_fix(c: Cockpit, x: Context) -> Command | None:
    return Command("altimeter", f"{x.altimeter_inhg:.2f}") if x.altimeter_inhg else None


ITEMS: dict[str, tuple[Item, ...]] = {
    "before_start": (
        Item("Parking brake", _own(lambda c, x: ("set" if c.own.parking_brake else "released", c.own.parking_brake))),
        _light("beacon", True, "Beacon"),
        Item("Transponder code", _squawk, lambda c, x: Command("squawk", x.squawk) if x.squawk else None),
        Item("Altimeter", _altimeter, _altimeter_fix),
    ),
    "after_start": (
        Item("Engines", _sys(lambda c, x: (f"{c.systems.engines_running} running", c.systems.engines_running > 0))),
        _light("nav", True, "Nav lights"),
    ),
    "before_taxi": (
        Item("Flaps", _flaps_takeoff),
        _light("taxi", True, "Taxi light"),
    ),
    "before_takeoff": (
        Item("Flaps", _flaps_takeoff),
        Item("Transponder", _own(lambda c, x: ({"alt": "TA/RA", "on": "on"}.get(c.own.xpdr_mode, c.own.xpdr_mode),
                                               c.own.xpdr_mode in ("alt", "on")))),
        _light("strobe", True, "Strobes"),
        _light("landing", True, "Landing lights"),
    ),
    "after_takeoff": (
        Item("Gear", _own(lambda c, x: ("up" if not c.own.gear_down else "down", not c.own.gear_down))),
        Item("Flaps", _own(lambda c, x: ("up" if c.own.flaps_index == 0 else _say_flaps(_flaps_named(c)), c.own.flaps_index == 0))),
    ),
    "descent": (
        Item("Altimeter", _altimeter, _altimeter_fix),
        _light("landing", True, "Landing lights"),
    ),
    "landing": (
        Item("Gear", _own(lambda c, x: ("down, three green" if c.own.gear_down else "up", c.own.gear_down))),
        Item("Flaps", _flaps_landing),
        Item("Spoilers", _sys(lambda c, x: ("armed", True) if c.systems.spoilers_armed else None)),
    ),
    "after_landing": (
        Item("Flaps", _own(lambda c, x: ("up" if c.own.flaps_index == 0 else _say_flaps(_flaps_named(c)), c.own.flaps_index == 0)),
             lambda c, x: Command("flaps", "up")),
        _light("strobe", False, "Strobes"),
        _light("landing", False, "Landing lights"),
    ),
    "shutdown": (
        Item("Parking brake", _own(lambda c, x: ("set" if c.own.parking_brake else "released", c.own.parking_brake))),
        Item("Engines", _sys(lambda c, x: ("off" if c.systems.engines_running == 0 else f"{c.systems.engines_running} running",
                                           c.systems.engines_running == 0))),
        _light("beacon", False, "Beacon"),
    ),
}


def named(text: str) -> str | None:
    """Which checklist the words name ("run the before takeoff checklist" -> "before_takeoff"), or None."""
    lowered = " ".join(text.lower().replace("-", " ").split())
    for words, name in sorted(SPOKEN, key=lambda s: -len(s[0])):
        if words in lowered:
            return name
    return None


@dataclass
class Reading:
    """A checklist read once: the words, the copilot's fixes to send, and the item it stopped at (None: done)."""

    text: str
    fixes: list[Command]
    held: Item | None
    items: int = 0  # how many were read (0: nothing on it the copilot can see)


def read(name: str, c: Cockpit, x: Context, *, hands: bool, start: int = 0) -> tuple[Reading, int]:
    """Read ``name`` from item ``start``: (what's said, the index it stopped at or len(items) when complete)."""
    items = ITEMS[name]
    lines: list[str] = [f"{NAMES[name]} checklist."] if start == 0 else []
    fixes: list[Command] = []
    read_n = 0
    for i in range(start, len(items)):
        item = items[i]
        seen = item.state(c, x)
        if seen is None:
            continue
        read_n += 1
        words, ok = seen
        if not ok and hands and item.fix is not None and (cmd := item.fix(c, x)) is not None:
            fixes.append(cmd)
            words, ok = _fixed_words(cmd), True
        if not ok:
            lines.append(f"{item.challenge}: {words}. Holding the checklist.")
            return Reading(" ".join(lines), fixes, item, read_n), i
        lines.append(f"{item.challenge}, {words}.")
    lines.append(f"{NAMES[name]} checklist complete.")
    return Reading(" ".join(lines), fixes, None, read_n), len(items)


def _fixed_words(cmd: Command) -> str:
    if cmd.action == "light":
        return f"{cmd.value}, set"
    if cmd.action == "squawk":
        return f"{speech.squawk(cmd.value)}, set"
    if cmd.action == "altimeter":
        return f"{speech.altimeter_display(float(cmd.value))}, set"
    if cmd.action == "flaps":
        return "up, set"
    return "set"
