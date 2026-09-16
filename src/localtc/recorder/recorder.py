"""Writes bus events to a timestamped JSONL recording."""

import asyncio
import gzip
import logging
import re
import shutil
import time
import wave
from collections.abc import AsyncIterable
from datetime import datetime
from pathlib import Path
from typing import Any

from localtc import __version__
from localtc.recorder.format import (
    AUDIO_DIR,
    SCHEMA_VERSION,
    SESSION_FILE,
    RecordingHeader,
    encode_line,
)
from localtc.sim_api import BusEvent, SessionInfo

log = logging.getLogger(__name__)


class Recorder:
    """Appends events to ``<session_dir>/session.jsonl``.

    ``record()`` never blocks: events are encoded and queued, and a background
    task writes them in batches and flushes about every ``flush_interval``
    seconds.
    """

    def __init__(
        self,
        session_dir: str | Path,
        header: RecordingHeader,
        *,
        flush_interval: float = 1.0,
        compress: bool = False,
    ) -> None:
        self._dir = Path(session_dir)
        self._header = header
        self._flush_interval = flush_interval
        self._compress = compress
        self._session_file = self._dir / SESSION_FILE
        self._fh = None
        self._queue: asyncio.Queue[bytes | None] | None = None
        self._task: asyncio.Task | None = None
        self._closing = False
        self._audio_count = 0
        self.events_written = 0

    @classmethod
    def create(
        cls,
        root: str | Path,
        session: SessionInfo,
        *,
        config: dict[str, Any] | None = None,
        label: str | None = None,
        now: datetime | None = None,
        **kwargs: Any,
    ) -> "Recorder":
        """Make a recorder for a new ``<root>/<YYYYMMDD-HHMMSS>_<label>`` directory."""
        now = (now or datetime.now()).astimezone()
        name = f"{now:%Y%m%d-%H%M%S}_{_slug(label or session.source_kind)}"
        header = RecordingHeader(
            schema=SCHEMA_VERSION,
            localtc_version=__version__,
            created=now.isoformat(timespec="seconds"),
            session=session,
            config=config or {},
        )
        return cls(_unique_dir(Path(root) / name), header, **kwargs)

    @property
    def session_dir(self) -> Path:
        return self._dir

    @property
    def session_file(self) -> Path:
        return self._session_file

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("recorder already started")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._session_file, "wb")
        self._fh.write(encode_line(self._header))
        self._fh.flush()
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._writer(), name="recorder-writer")
        log.info("Recording to %s", self._dir)

    def record(self, event: BusEvent) -> None:
        if self._queue is None or self._closing:
            raise RuntimeError("recorder is not running")
        self._queue.put_nowait(encode_line(event))

    async def consume(self, events: AsyncIterable[BusEvent]) -> None:
        """Record everything from ``events`` (e.g. a bus subscription) until it ends."""
        async for event in events:
            self.record(event)

    def save_audio(
        self, pcm: bytes, *, sample_rate: int, channels: int = 1, sample_width: int = 2
    ) -> str:
        """Save PCM audio as a WAV beside the session; returns the ``audio_ref`` to put in events."""
        while True:  # skip names already present (e.g. audio copied from a replayed recording)
            self._audio_count += 1
            ref = f"{AUDIO_DIR}/{self._audio_count:04d}.wav"
            path = self._dir / ref
            if not path.exists():
                break
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(sample_width)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        return ref

    async def close(self) -> None:
        if self._task is None or self._closing:
            return
        self._closing = True
        self._queue.put_nowait(None)
        await self._task
        self._fh.close()
        if self._compress:
            self._session_file = await asyncio.to_thread(_gzip_file, self._session_file)
        log.info("Recorded %d events to %s", self.events_written, self._session_file)

    async def __aenter__(self) -> "Recorder":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _writer(self) -> None:
        last_flush = time.monotonic()
        done = False
        while not done:
            batch: list[bytes] = []
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval)
            except TimeoutError:
                item = b""
            while True:
                if item is None:
                    done = True
                    break
                if item:
                    batch.append(item)
                if self._queue.empty():
                    break
                item = self._queue.get_nowait()
            if batch:
                self._fh.write(b"".join(batch))
                self.events_written += len(batch)
            if done or time.monotonic() - last_flush >= self._flush_interval:
                self._fh.flush()
                last_flush = time.monotonic()


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_")[:40] or "session"


def _unique_dir(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}-{n}")
    return candidate


def _gzip_file(path: Path) -> Path:
    gz_path = path.with_name(path.name + ".gz")
    with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return gz_path
