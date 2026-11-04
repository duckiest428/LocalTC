"""What the ground crew needs from whoever does the work: the services' states, a menu to read, a way to ask."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# GSX's service ids (its Remote API guide, appendix B), in the order a turnaround calls them.
SERVICES = ("GPU", "OperateJetways", "OperateStairs", "Deboarding", "Cleaning", "Catering", "Refueling", "Water", "Lavatory",
            "DeIce", "Boarding", "Departure")
ACTIVE = ("requested", "performing", "completing")
# Under GSX's "Additional Services" page, not on its first one.
ADDITIONAL = ("GPU", "DeIce", "Lavatory", "Water", "Cleaning")


@dataclass(frozen=True)
class Service:
    id: str
    name: str = ""
    state: str = "available"  # available, unavailable, bypassed, requested, performing, completing, completed
    waiting: bool = False
    operator: str = ""
    status: str = ""  # what it's doing, as GSX says it ("front on the way, ETA 25 secs"), several lines
    progress: str = ""  # "120/180"
    can_trigger: bool = True

    @property
    def active(self) -> bool:
        return self.state in ACTIVE


@dataclass(frozen=True)
class Menu:
    title: str
    entries: tuple[str, ...] = ()
    disabled: tuple[bool, ...] = ()


@dataclass(frozen=True)
class GroundState:
    connected: bool = False
    running: bool = False
    capabilities: frozenset[str] = frozenset()
    services: dict[str, Service] = field(default_factory=dict)
    menu: Menu | None = None  # None while nothing is open: a title without the menu showing is a stale one
    message: str = ""
    parking: str = ""
    airport: str = ""
    prompt: dict | None = None


@dataclass(frozen=True)
class Result:
    ok: bool
    code: str = ""
    message: str = ""


class GroundAdapter(Protocol):
    name: str  # "gsx", "standalone", "mock"

    @property
    def state(self) -> GroundState: ...

    async def start(self, on_change: Callable[[], None]) -> None: ...

    async def stop(self) -> None: ...

    async def trigger(self, service: str) -> Result: ...

    async def open_menu(self) -> Result: ...

    async def pick(self, index: int) -> Result: ...

    async def close_menu(self) -> Result: ...

    async def answer_prompt(self, gen: int, text: str) -> Result: ...
