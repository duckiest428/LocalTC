"""Connects ``AtcEngine`` to the bus: feeds it sim and pilot events, publishes what it says and decides.

With a copilot, also carries out what the copilot decides: its words become ``Transcript``
events, its frequency changes become sim commands.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import msgspec

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
    WeatherReport,
)

log = logging.getLogger(__name__)

WEATHER_CHECK_S = 30.0  # the airports' METARs looked at this often (session time); fetched in the background


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
        self.deck = None  # the flight deck the copilot shares with the intercom copilot (set by the app)
        # The flight's airports' real weather reports (the app's ``MetarStore.get``), or None: none.
        self.weather_source: Callable[[str], WeatherReport | None] | None = None
        self._weather_t = -WEATHER_CHECK_S
        self._weather_sent: dict[str, str] = {}  # icao: the METAR last published for it
        self.atis_source: Callable[[str], list | None] | None = None  # the real ATIS (the app's ``AtisStore.get``)
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
                # The model missed the call: now it gets its time, with the app showing the controller thinking.
                try:
                    later = await asyncio.to_thread(self.engine.resolve_deferred)
                except Exception:
                    log.exception("ATC engine failed answering after the model's second look")
                    later = []
                outputs = [*outputs, *later]
                for output in later:
                    self.bus.publish(output)
            if self.copilot is not None:
                try:
                    await self._copilot(event, outputs)
                except Exception:  # the copilot failing never stops ATC
                    log.exception("Copilot failed on %s", type(event).__name__)
            await self._fetch_airports(event.t)
            self._fetch_weather(event.t)

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
        deck = getattr(self.copilot, "deck", None)
        if deck is not None:
            for note in deck.drain():  # the copilot's record (each published once, by whichever drains it first)
                self.bus.publish(note)

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

    def _fetch_weather(self, t: float) -> None:
        """A new METAR for the departure or the destination: published (recorded, and the engine takes it in)."""
        if (self.weather_source is None and self.atis_source is None) or t - self._weather_t < WEATHER_CHECK_S:
            return
        self._weather_t = t
        flight = self.engine.state.flight
        for icao in dict.fromkeys(a for a in (flight.origin, flight.destination) if a):
            try:
                report = self.weather_source(icao) if self.weather_source is not None else None
                atis = self.atis_source(icao) if self.atis_source is not None else None
            except Exception:  # noqa: BLE001 - the weather is a nicety: never stops ATC
                log.exception("METAR or ATIS for %s failed", icao)
                continue
            if report is not None and self._weather_sent.get(icao) != report.raw:
                self._weather_sent[icao] = report.raw
                self.bus.publish(msgspec.structs.replace(report, t=t))
            for one in atis or ():
                if self._weather_sent.get(f"{icao}/{one.kind}") != one.text:
                    self._weather_sent[f"{icao}/{one.kind}"] = one.text
                    self.bus.publish(msgspec.structs.replace(one, t=t))
