"""Piper voice files: where they live, fetching them, and which speaker says what.

A multi-speaker voice (``en_US-libritts_r-medium`` has 904 speakers) gives every controller
their own voice from one download. ``SPEAKERS`` are the ones Whisper understood best when they
read ATC phraseology (``tools/pick_speakers.py``); each station gets one of them, the same one
every time.
"""

import json
import logging
import os
import zlib
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_VOICE = "en_US-libritts_r-medium"
# Picked by tools/pick_speakers.py: clearest to Whisper reading ATC phraseology, at a brisk pace.
SPEAKERS: tuple[int, ...] = (
    235, 612, 149, 372, 122, 299, 685, 517, 458, 408, 825, 226, 803, 172, 453, 313, 95, 349, 830, 716,
    4, 63, 748, 530, 689, 381, 626, 576, 367, 444, 721, 875, 72, 535, 376, 539, 0, 254, 503, 558,
)
PILOT_SPEAKER_SALT = "pilot"


def default_voices_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "LocalTC" / "voices") if base else Path.home() / ".cache" / "localtc" / "voices"


def voice_path(voice: str, voices_dir: Path | None = None) -> Path:
    return (voices_dir or default_voices_dir()) / f"{voice}.onnx"


def installed(voice: str, voices_dir: Path | None = None) -> bool:
    path = voice_path(voice, voices_dir)
    return path.is_file() and path.with_suffix(".onnx.json").is_file()


def download(voice: str = DEFAULT_VOICE, voices_dir: Path | None = None) -> Path:
    """Fetch a voice (once) from the Piper voices collection; returns the .onnx path."""
    from piper.download_voices import download_voice

    target = voices_dir or default_voices_dir()
    target.mkdir(parents=True, exist_ok=True)
    if not installed(voice, target):
        log.info("Downloading Piper voice %s to %s", voice, target)
        download_voice(voice, target)
    return voice_path(voice, target)


def speaker_count(voice_file: Path) -> int:
    config = json.loads(voice_file.with_suffix(".onnx.json").read_text(encoding="utf-8"))
    return int(config.get("num_speakers", 1))


def speaker_for(key: str, count: int, speakers: tuple[int, ...] = SPEAKERS) -> int | None:
    """A stable speaker for a station ("Phoenix Tower") or role; None for a single-speaker voice."""
    if count <= 1:
        return None
    pool = [s for s in speakers if s < count] or list(range(count))
    return pool[zlib.crc32(key.lower().encode()) % len(pool)]
