"""Driving GSX's menu. It changes after every pick and its rows change with what's going on ("Request Catering service",
"Catering vehicles are on their way", "Dismiss Water service", "No Jetways here"), so it's read afresh before each one, and
nothing is picked by its place in the list: one row says what's wanted, or nothing is picked and the pilot is told. Never a
wrong click.
"""

import re
from dataclasses import dataclass, field

from localtc.ground.api import ADDITIONAL, Menu

OPEN_S = 15.0  # the menu asked for, and this long to come up (20 s at worst so far: asked again by then, it's still on its way)
PICK_S = 5.0  # picked, and this long for it to change
RETRIES = 2
ADDITIONAL_ROW = ("additional services",)
# GSX's own pages for finding a service. What's on any other page is a question (operator, direction, engine start).
BROWSING = ("activate services", "additional services", "service in progress")
BACK = ("back", "next page", "previous page")

# The words of a service in its row, whatever state the row is in.
ROW_WORDS = {"GPU": ("ground power", "gpu"), "OperateJetways": ("jetway", "jetways"), "OperateStairs": ("stairs",),
             "Deboarding": ("deboarding",), "Boarding": ("boarding",), "Cleaning": ("cleaning",), "Catering": ("catering",),
             "Refueling": ("refueling", "refuelling"), "Water": ("water",), "Lavatory": ("lavatory",), "DeIce": ("deice", "de ice"),
             "Departure": ("push back", "pushback", "departure")}


def norm(text: str) -> str:
    """Lower case, words only: "Prepare for Push-back and Departure" -> "prepare for push back and departure"."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def stand(name: str) -> str:
    """A stand's name to compare: "Gate G 13", "G13" and "stand G13" are one."""
    return norm(name).replace("gate", "").replace("stand", "").replace("parking", "").replace(" ", "")


def has_words(text: str, words: str) -> bool:
    return f" {norm(words)} " in f" {norm(text)} "


def is_question(menu: Menu) -> bool:
    """A page of GSX asking something, not one of its pages for finding a service."""
    title = norm(menu.title)
    return not any(title.startswith(page) for page in BROWSING)


def real_entries(menu: Menu) -> list[tuple[int, str]]:
    """The choices on a page, without its way back and its paging."""
    return [(i, e) for i, e in enumerate(menu.entries) if norm(e) not in BACK and not norm(e).endswith(" back")]


@dataclass(frozen=True)
class Found:
    index: int | None = None
    why: str = ""  # "": found; "missing", "ambiguous", "disabled"
    choices: tuple[str, ...] = ()


def find(menu: Menu, words: tuple[str, ...]) -> Found:
    """The one row with these words, of any of the phrases given."""
    hits = [i for i, e in enumerate(menu.entries) if any(has_words(e, w) for w in words)]
    if not hits:
        return Found(why="missing")
    if len(hits) > 1:
        return Found(why="ambiguous", choices=tuple(menu.entries[i] for i in hits))
    i = hits[0]
    if i < len(menu.disabled) and menu.disabled[i]:
        return Found(why="disabled", choices=(menu.entries[i],))
    return Found(index=i)


@dataclass(frozen=True)
class Want:
    words: tuple[str, ...]
    additional: bool = False  # it may be on the Additional Services page


def row_of(service: str) -> Want:
    return Want(ROW_WORDS.get(service, (service.lower(),)), service in ADDITIONAL)


@dataclass(frozen=True)
class Open:
    fallback: bool = False  # the API acknowledged it and nothing came: L:FSDT_GSX_MENU_OPEN = 1, a plain open


@dataclass(frozen=True)
class Pick:
    index: int
    label: str = ""


@dataclass(frozen=True)
class Close:
    pass


@dataclass
class MenuRun:
    """One errand through the menu: open it if it isn't, find each row in turn, pick it, see it change. ``step`` is told
    what's showing now and says what to do next; ``failed`` then says why, and the menu is left for the pilot to see."""

    wants: tuple[Want, ...]
    started: float
    goal: str = ""
    done: bool = False
    failed: str = ""  # "": not failed; otherwise "never_opened", "missing", "ambiguous", "disabled", "no_change"
    choices: tuple[str, ...] = ()
    _at: int = 0
    _asked_t: float | None = None
    _opens: int = 0
    _picked: Menu | None = None
    _picked_t: float = 0.0
    _picks: int = 0
    _detour: list = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.done or bool(self.failed)

    def _fail(self, why: str, choices: tuple[str, ...] = ()) -> None:
        self.failed, self.choices = why, choices

    def step(self, menu: Menu | None, now: float) -> Open | Pick | Close | None:
        if self.finished:
            return None
        if self._picked is not None:  # waiting for the pick to show
            if menu == self._picked:
                if now - self._picked_t < PICK_S:
                    return None
                self._picks += 1
                self._picked = None
                if self._picks > RETRIES:
                    self._fail("no_change")
                    return None
            else:
                self._picked = None
                self._picks = 0
                if self._at >= len(self.wants):
                    self.done = True
                    # (a row that leaves GSX on a browsing page: closed, nobody asked for a menu on the screen)
                    return Close() if menu is not None and not is_question(menu) else None
        if self._at >= len(self.wants):
            self.done = True
            return None
        if menu is None:
            if self._asked_t is None or now - self._asked_t >= OPEN_S:
                self._opens += 1
                if self._opens > RETRIES:
                    self._fail("never_opened")
                    return None
                self._asked_t = now
                return Open(fallback=self._opens > 1)
            return None
        want = self.wants[self._at]
        found = find(menu, want.words)
        if found.why == "missing" and want.additional and not has_words(menu.title, "additional services") \
                and (more := find(menu, ADDITIONAL_ROW)).index is not None:
            return self._pick(menu, more.index, now, advance=False)
        if found.index is None:
            self._fail(found.why, found.choices)
            return None
        return self._pick(menu, found.index, now, advance=True)

    def _pick(self, menu: Menu, index: int, now: float, *, advance: bool) -> Pick:
        if advance:
            self._at += 1
        self._picked, self._picked_t = menu, now
        return Pick(index, menu.entries[index])
