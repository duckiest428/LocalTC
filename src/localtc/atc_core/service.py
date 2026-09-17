"""Connects ``AtcEngine`` to the bus: feeds it sim and pilot events, publishes what it says and decides."""

import asyncio
import logging

from localtc.airports import AirportCache
from localtc.atc_core.engine import AtcEngine
from localtc.bus import EventBus
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AirportData,
    PttPressed,
    PttReleased,
    RequestAirportData,
    SimSource,
    Transcript,
)

log = logging.getLogger(__name__)


class AtcService:
    def __init__(
        self, engine: AtcEngine, bus: EventBus, source: SimSource | None = None, cache: AirportCache | None = None
    ) -> None:
        self.engine = engine
        self.bus = bus
        self.source = source
        self.cache = cache
        # Subscribe now, not in run(): a fast source could publish everything before run() starts.
        # Only inputs: the engine's own outputs (AtcTransmission, PhaseChanged, ...) are not fed back in.
        self._inputs = bus.subscribe(*SIM_EVENT_TYPES, Transcript, PttPressed, PttReleased)

    async def run(self) -> None:
        async for event in self._inputs:
            try:
                outputs = self.engine.handle(event)
            except Exception:
                log.exception("ATC engine failed on %s", type(event).__name__)
                continue
            for output in outputs:
                self.bus.publish(output)
            await self._fetch_airports(event.t)

    async def _fetch_airports(self, t: float) -> None:
        while self.engine.airport_requests:
            icao = self.engine.airport_requests.pop(0)
            cached = await asyncio.to_thread(self.cache.get, icao) if self.cache else None
            if cached is not None:
                log.info("Using cached airport data for %s", icao)
                # Published on the bus so it's recorded and the engine picks it up like any airport data.
                self.bus.publish(AirportData(t=t, airport=cached))
            elif self.source is not None:
                await self.source.send(RequestAirportData(icao=icao))
            else:
                log.warning("No airport data for %s", icao)
