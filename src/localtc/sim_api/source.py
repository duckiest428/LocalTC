"""The interface shared by the live SimConnect bridge and the replay tool."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from localtc.sim_api.clock import Clock
from localtc.sim_api.commands import SimCommand
from localtc.sim_api.events import BusEvent, SessionInfo


class SourceUnavailable(RuntimeError):
    """The source can't run here (e.g. the live bridge on a non-Windows machine)."""


@runtime_checkable
class SimSource(Protocol):
    """A stream of sim events.

    Lifecycle: ``await start()`` once, iterate ``events()``, then ``await stop()``.
    After ``stop()``, ``events()`` must finish promptly.
    """

    @property
    def clock(self) -> Clock: ...

    async def start(self) -> SessionInfo: ...

    def events(self) -> AsyncIterator[BusEvent]: ...

    async def stop(self) -> None: ...

    async def send(self, command: SimCommand) -> None:
        """Ask the source to do something (e.g. fetch airport data). Sources may ignore commands."""
        ...
