"""Reads recordings produced by ``localtc.recorder``."""

import gzip
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import IO

import msgspec

from localtc.recorder.format import SCHEMA_VERSION, SESSION_FILE, RecordingHeader, header_decoder
from localtc.sim_api import BusEvent, decode_event

log = logging.getLogger(__name__)


class RecordingFormatError(ValueError):
    pass


def session_file_for(path: str | Path) -> Path:
    """Accept a recording directory or a session file path."""
    path = Path(path)
    if path.is_dir():
        for name in (SESSION_FILE, SESSION_FILE + ".gz"):
            if (path / name).is_file():
                return path / name
        raise FileNotFoundError(f"no {SESSION_FILE} or {SESSION_FILE}.gz in {path}")
    if path.is_file():
        return path
    raise FileNotFoundError(f"recording not found: {path}")


class Recording:
    def __init__(self, path: str | Path) -> None:
        self.session_file = session_file_for(path)
        self.root = self.session_file.parent
        self.header: RecordingHeader = self._read_header()
        self.skipped_lines = 0

    def events(self, *, skip: tuple[str, ...] = ()) -> Iterator[BusEvent]:
        """Yield events in file order, leaving out the event types (tags) in ``skip`` without decoding them.

        Lines that don't decode are skipped with a warning: unknown event types
        from a newer LocalTC, or a truncated last line after a crash.
        """
        skipped = 0
        unwanted = tuple(f'{{"type":"{tag}"'.encode() for tag in skip)
        with self._open() as fh:
            seen_header = False
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                if not seen_header:
                    seen_header = True
                    continue
                if unwanted and line.startswith(unwanted):
                    continue
                try:
                    event = decode_event(line)
                except msgspec.MsgspecError as exc:
                    skipped += 1
                    if skipped <= 5:
                        log.warning("%s:%d: skipping line (%s)", self.session_file, lineno, exc)
                    continue
                yield event
        self.skipped_lines = skipped

    def resolve_audio(self, audio_ref: str) -> Path:
        path = (self.root / audio_ref).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"audio_ref escapes the recording directory: {audio_ref!r}")
        return path

    def _open(self) -> IO[bytes]:
        if self.session_file.suffix == ".gz":
            return gzip.open(self.session_file, "rb")
        return open(self.session_file, "rb")

    def _read_header(self) -> RecordingHeader:
        with self._open() as fh:
            first = next((line for line in fh if line.strip()), None)
        if first is None:
            raise RecordingFormatError(f"{self.session_file}: empty recording")
        try:
            header = header_decoder.decode(first)
        except msgspec.MsgspecError as exc:
            raise RecordingFormatError(f"{self.session_file}: bad header: {exc}") from exc
        if header.schema > SCHEMA_VERSION:
            raise RecordingFormatError(
                f"{self.session_file}: schema {header.schema} is newer than this LocalTC "
                f"supports ({SCHEMA_VERSION}); update LocalTC"
            )
        return header
