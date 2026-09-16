"""Wiring: builds the configured source and connects it to the bus and recorder.

This and ``localtc.sim_bridge`` are the only modules allowed to import the
Windows-only bridge.
"""

import asyncio
import logging
import shutil
import struct
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from localtc.airports import AirportCache
from localtc.bus import EventBus, Subscription, pump
from localtc.config import AtcConfig, Config, ConfigError, FlightConfig
from localtc.recorder import Recorder
from localtc.recorder.format import AUDIO_DIR
from localtc.replay import ReplaySource
from localtc.sim_api import (
    ATC_EVENT_TYPES,
    Airport,
    AirportData,
    AtcTransmission,
    BusEvent,
    RequestAirportData,
    SessionInfo,
    SimSource,
    Transcript,
)

log = logging.getLogger(__name__)


def engine_config(flight: FlightConfig, atc: AtcConfig):
    """Map ``[flight]`` and ``[atc]`` config to the ATC engine's settings."""
    from localtc.atc_core.engine import EngineConfig
    from localtc.atc_core.phase import PhaseThresholds

    return EngineConfig(
        destination=flight.destination or None,
        cruise_ft=flight.cruise_ft or None,
        callsign=flight.callsign or None,
        rules=flight.rules,
        center_name=atc.center_name,
        center_mhz=atc.center_mhz,
        strict_callsign=atc.strict_callsign,
        seed=atc.seed,
        thresholds=msgspec.convert(atc.phase, PhaseThresholds),
    )


def make_source(cfg: Config) -> SimSource:
    if cfg.source.kind == "live":
        from localtc.sim_bridge.simconnect_source import SimConnectSource

        return SimConnectSource(cfg.live)
    if not cfg.replay.path:
        raise ConfigError("replay.path is required when source.kind = 'replay'")
    return ReplaySource(
        cfg.replay.path,
        speed=cfg.replay.speed,
        start_at=cfg.replay.start_at,
        end_at=cfg.replay.end_at,
        loop=cfg.replay.loop,
        include_radio=cfg.replay.include_radio,
        # A live ATC engine re-creates ATC output; replaying the recorded one would duplicate it.
        exclude_types=(*ATC_EVENT_TYPES, AtcTransmission) if cfg.atc.enabled else (),
    )


async def run_session(
    cfg: Config,
    *,
    record: bool | None = None,
    on_event: Callable[[BusEvent], None] | None = None,
    stop: asyncio.Event | None = None,
    typed_input: bool = False,
) -> Path | None:
    """Run the source until it ends, ``stop`` is set, or the task is cancelled (Ctrl-C).

    Returns the recording directory if recording was enabled. With ``typed_input``,
    lines typed on stdin become pilot transmissions (``Transcript`` events).
    """
    record = cfg.recorder.enabled if record is None else record
    source = make_source(cfg)
    session = await source.start()
    log.info("Source ready: %s %s %s", session.source_kind, session.sim_product, session.sim_version)

    bus = EventBus()
    recorder: Recorder | None = None
    consumers: list[asyncio.Task] = []
    pump_task: asyncio.Task | None = None
    try:
        if record:
            recorder = Recorder.create(
                cfg.recorder.dir,
                session,
                config=msgspec.to_builtins(cfg),
                flush_interval=cfg.recorder.flush_interval_s,
                compress=cfg.recorder.compress,
            )
            await recorder.start()
            if isinstance(source, ReplaySource):
                _copy_audio(source, recorder)
            consumers.append(asyncio.create_task(recorder.consume(bus.subscribe())))
        if on_event is not None:
            consumers.append(asyncio.create_task(_dispatch(bus.subscribe(), on_event)))
        if session.source_kind == "live":
            consumers.append(asyncio.create_task(_cache_airports(bus.subscribe(AirportData), AirportCache())))
        if cfg.atc.enabled:
            from localtc.airports import load_airport_dir
            from localtc.atc_core.engine import AtcEngine
            from localtc.atc_core.service import AtcService

            engine = AtcEngine(engine_config(cfg.flight, cfg.atc))
            for directory in cfg.atc.airport_dirs:
                for airport in load_airport_dir(directory):
                    engine.handle(AirportData(t=0.0, airport=airport))
            consumers.append(asyncio.create_task(AtcService(engine, bus, source, AirportCache()).run()))
        if typed_input:
            consumers.append(asyncio.create_task(_typed_transmissions(bus, source)))

        pump_task = asyncio.create_task(pump(source, bus))
        if stop is None:
            await pump_task
        else:
            stop_task = asyncio.create_task(stop.wait())
            await asyncio.wait({pump_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            stop_task.cancel()
    finally:
        await source.stop()
        if pump_task is not None and not pump_task.done():
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(pump_task, timeout=2.0)
        bus.close()
        await asyncio.gather(*consumers, return_exceptions=True)
        if recorder is not None:
            await recorder.close()
            log.info("Recording saved to %s", recorder.session_dir)
    return recorder.session_dir if recorder else None


async def _typed_transmissions(bus: EventBus, source: SimSource) -> None:
    """Each line typed on stdin is a pilot transmission on COM1 (a stand-in for push-to-talk + speech-to-text)."""
    import sys

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        if text := line.strip():
            bus.publish(Transcript(t=source.clock.now(), text=text))


async def _cache_airports(sub: Subscription, cache: AirportCache) -> None:
    async for event in sub:
        try:
            path = await asyncio.to_thread(cache.put, event.airport)
            log.info("Cached %s airport data at %s", event.airport.icao, path)
        except OSError as exc:
            log.warning("Could not cache %s: %s", event.airport.icao, exc)


@dataclass
class FacilityDebugReport:
    session: SessionInfo
    airport: Airport | None = None
    # (message id, Type field, bytes after a 40-byte header) -> count
    messages: Counter = field(default_factory=Counter)
    raw_path: Path | None = None


async def debug_airport(
    cfg: Config, icao: str | None, *, raw_path: Path | None = None, timeout: float = 60.0, dll_factory=None
) -> FacilityDebugReport:
    """Fetch one airport from the live sim (or the nearest one if ``icao`` is None) with raw diagnostics."""
    from localtc.sim_bridge.simconnect_source import SimConnectSource

    raw_file = open(raw_path, "wb") if raw_path else None
    counts: Counter = Counter()

    def tap(buf: bytes) -> None:
        size, _, msg_id = struct.unpack_from("<III", buf)
        facility_data = msg_id in (28, 29, 30, 31) and size > 16
        type_field = struct.unpack_from("<I", buf, 24)[0] if facility_data and len(buf) >= 28 else None
        counts[(msg_id, type_field, size - 40 if facility_data else size)] += 1
        if raw_file:
            raw_file.write(struct.pack("<I", len(buf)) + buf)

    cfg.live.nearest_airport_interval_s = 5.0 if icao is None else 0.0
    source = SimConnectSource(cfg.live, raw_tap=tap, dll_factory=dll_factory)
    try:
        session = await source.start()
        report = FacilityDebugReport(session=session, messages=counts, raw_path=raw_path)
        if icao:
            await source.send(RequestAirportData(icao=icao))

        async def wait() -> None:
            async for event in source.events():
                if isinstance(event, AirportData) and (icao is None or event.airport.icao.upper() == icao.upper()):
                    report.airport = event.airport
                    return

        with suppress(TimeoutError):
            await asyncio.wait_for(wait(), timeout)
        return report
    finally:
        await source.stop()
        if raw_file:
            raw_file.close()


async def _dispatch(sub: Subscription, callback: Callable[[BusEvent], None]) -> None:
    async for event in sub:
        callback(event)


def _copy_audio(source: ReplaySource, recorder: Recorder) -> None:
    """Re-recorded replays keep their audio_refs, so bring the referenced WAVs along."""
    audio = source.recording.root / AUDIO_DIR
    if audio.is_dir():
        shutil.copytree(audio, recorder.session_dir / AUDIO_DIR, dirs_exist_ok=True)
