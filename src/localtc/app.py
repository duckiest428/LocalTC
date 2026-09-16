"""Wiring: builds the configured source and connects it to the bus and recorder.

This and ``localtc.sim_bridge`` are the only modules allowed to import the
Windows-only bridge.
"""

import asyncio
import logging
import shutil
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import msgspec

from localtc.bus import EventBus, Subscription, pump
from localtc.config import Config, ConfigError
from localtc.recorder import Recorder
from localtc.recorder.format import AUDIO_DIR
from localtc.replay import ReplaySource
from localtc.sim_api import BusEvent, SimSource

log = logging.getLogger(__name__)


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
    )


async def run_session(
    cfg: Config,
    *,
    record: bool | None = None,
    on_event: Callable[[BusEvent], None] | None = None,
    stop: asyncio.Event | None = None,
) -> Path | None:
    """Run the source until it ends, ``stop`` is set, or the task is cancelled (Ctrl-C).

    Returns the recording directory if recording was enabled.
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


async def _dispatch(sub: Subscription, callback: Callable[[BusEvent], None]) -> None:
    async for event in sub:
        callback(event)


def _copy_audio(source: ReplaySource, recorder: Recorder) -> None:
    """Re-recorded replays keep their audio_refs, so bring the referenced WAVs along."""
    audio = source.recording.root / AUDIO_DIR
    if audio.is_dir():
        shutil.copytree(audio, recorder.session_dir / AUDIO_DIR, dirs_exist_ok=True)
