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
from dataclasses import dataclass
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


# --- how each controller speaks ---------------------------------------------------------------------------------
#
# Piper's three knobs, per call: ``length_scale`` (the pace: under 1 faster), ``noise_scale`` (how much the voice
# varies, its expressiveness) and ``noise_w`` (how uneven the timing is: pauses, rhythm). Each kind of controller
# has a manner of its own, and each station a little on top, so pacing tells them apart as much as the words do.

@dataclass(frozen=True)
class Delivery:
    pace: float = 1.0  # multiplies the configured speaking rate: over 1 faster
    noise_scale: float = 0.667  # Piper's defaults
    noise_w: float = 0.8


DELIVERY: dict[str, Delivery] = {
    "clearance": Delivery(0.98, 0.62, 0.75),  # a long clearance, read steadily
    "ground": Delivery(1.0, 0.72, 0.9),  # conversational
    "tower": Delivery(1.08, 0.6, 0.6),  # quick and clipped: a busy runway
    "departure": Delivery(1.04, 0.64, 0.7),
    "approach": Delivery(1.02, 0.64, 0.72),
    "center": Delivery(0.94, 0.62, 0.85),  # slower, measured: long hours, a big sector
    "atis": Delivery(1.0, 0.25, 0.2),  # the recording: flat and even, nearly a machine
    "chatter": Delivery(1.0, 0.7, 0.85),  # other pilots on the frequency
    "pilot": Delivery(1.0, 0.667, 0.8),
}


def delivery_for(key: str, kind: str) -> Delivery:
    """The manner of a controller (``kind``: clearance ... center, atis, pilot) with this station's own touch
    (``key``: its name): up to 5 % on the pace and a little on the rest, the same every time."""
    base = DELIVERY.get(kind, Delivery())
    if kind in ("atis", "pilot"):
        return base
    h = zlib.crc32(("delivery" + key.lower()).encode())
    pace = base.pace * (0.95 + (h % 101) / 1000)
    return Delivery(round(pace, 3), round(base.noise_scale + ((h >> 8) % 11 - 5) / 100, 3),
                    round(base.noise_w + ((h >> 16) % 11 - 5) / 100, 3))
