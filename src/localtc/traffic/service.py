"""EXPERIMENTAL traffic control on the bus: the sim's traffic in, LocalTC's commands out (``control.TrafficControl``)."""

import asyncio
import logging

from localtc.bus import EventBus
from localtc.sim_api import (
    AiObjectAssigned,
    ConnectionStatus,
    ModelList,
    NearbyAirports,
    OwnshipState,
    SimCommand,
    SimLifecycle,
    SimSource,
    TrafficIdentity,
    TrafficSnapshot,
)
from localtc.traffic.control import TrafficControl

log = logging.getLogger(__name__)


class TrafficControlService:
    def __init__(self, control: TrafficControl, bus: EventBus, source: SimSource | None) -> None:
        self.control, self.bus, self.source = control, bus, source
        self._inputs = bus.subscribe(OwnshipState, TrafficSnapshot, TrafficIdentity, AiObjectAssigned, ModelList,
                                     ConnectionStatus, SimLifecycle, NearbyAirports)

    async def _out(self, outputs: list) -> None:
        for output in outputs:
            if isinstance(output, SimCommand):
                if self.source is not None:
                    await self.source.send(output)
            else:
                self.bus.publish(output)

    async def run(self) -> None:
        await self._out(self.control.start())
        async for event in self._inputs:
            try:
                await self._out(self.control.observe(event))
            except Exception:  # noqa: BLE001 - an experiment must never stop the flight
                log.exception("Traffic control failed on %s", type(event).__name__)

    async def set_mode(self, mode: str, t: float) -> None:
        await self._out(self.control.set_mode(mode, t))

    async def close(self) -> None:
        """The flight is over: LocalTC's copies out, the sim's own traffic alone again."""
        await self._out(self.control.release("the flight ended"))
