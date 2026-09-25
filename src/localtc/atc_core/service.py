"""Connects ``AtcEngine`` to the bus: feeds it sim and pilot events, publishes what it says and decides.

With a copilot, also carries out what the copilot decides: its words become ``Transcript``
events, its frequency changes become sim commands.
"""

import asyncio
import logging
from typing import Any

from localtc.airports import AirportCache
from localtc.atc_core.engine import AtcEngine
from localtc.bus import EventBus
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AirportData,
    AtcAlert,
    PttPressed,
    PttReleased,
    RequestAirportData,
    SetComFrequency,
    SimSource,
    Transcript,
)

log = logging.getLogger(__name__)


class AtcService:
    def __init__(
        self,
        engine: AtcEngine,
        bus: EventBus,
        source: SimSource | None = None,
        cache: AirportCache | None = None,
        *,
        copilot: Any = None,  # localtc.copilot.Copilot; the ATC core doesn't import it
    ) -> None:
        self.engine = engine
        self.bus = bus
        self.source = source
        self.cache = cache
        self.copilot = copilot
        # Subscribe now, not in run(): a fast source could publish everything before run() starts.
        # Only inputs: the engine's own outputs (AtcTransmission, PhaseChanged, ...) are not fed back in.
        self._inputs = bus.subscribe(*SIM_EVENT_TYPES, Transcript, PttPressed, PttReleased)

    async def run(self) -> None:
        async for event in self._inputs:
            try:
                if isinstance(event, Transcript):
                    # Understanding a transmission can mean a language model call: keep the loop responsive.
                    outputs = await asyncio.to_thread(self.engine.handle, event)
                else:
                    outputs = self.engine.handle(event)
            except Exception:
                log.exception("ATC engine failed on %s", type(event).__name__)
                continue
            for output in outputs:
                self.bus.publish(output)
            if self.engine.deferred is not None:
                # ATC said "stand by": now the model gets its time, with the "stand by" already on the air.
                try:
                    later = await asyncio.to_thread(self.engine.resolve_deferred)
                except Exception:
                    log.exception("ATC engine failed answering after stand by")
                    later = []
                outputs = [*outputs, *later]
                for output in later:
                    self.bus.publish(output)
            if self.copilot is not None:
                await self._copilot(event, outputs)
            await self._fetch_airports(event.t)

    async def _copilot(self, event, outputs) -> None:
        for observed in (event, *outputs):
            self.copilot.observe(observed)
        for action in self.copilot.due(event.t):
            if action.kind == "say":
                self.bus.publish(Transcript(t=action.t, text=action.text, source="copilot"))
            elif action.kind == "tune":
                if self.source is not None:
                    await self.source.send(SetComFrequency(hz=action.hz))
            elif action.kind == "note":
                log.warning("Copilot: %s", action.text)
                self.bus.publish(AtcAlert(t=action.t, kind="copilot", detail=action.text))

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
