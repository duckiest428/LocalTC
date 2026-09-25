"""Piper speech synthesis (the ``piper-tts`` package: prebuilt wheels for Windows, macOS and Linux)."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Speech:
    audio: np.ndarray  # mono float32, -1..1
    rate: int
    latency_ms: float

    @property
    def seconds(self) -> float:
        return len(self.audio) / self.rate


class PiperSynth:
    def __init__(self, voice_file: Path, *, rate: float = 1.15) -> None:
        from piper import PiperVoice

        started = time.monotonic()
        self.voice = PiperVoice.load(voice_file)
        self.voice_file = voice_file
        self.rate = rate
        self.sample_rate = int(self.voice.config.sample_rate)
        self.speakers = int(getattr(self.voice.config, "num_speakers", 1) or 1)
        log.info("Piper voice %s loaded (%d speakers, %.1f s)", voice_file.stem, self.speakers, time.monotonic() - started)

    def synthesize(self, text: str, speaker: int | None = None, *, rate: float | None = None,
                   noise_scale: float | None = None, noise_w: float | None = None) -> Speech:
        """``rate`` over 1 is faster (Piper's length_scale is its inverse); ``noise_scale`` and ``noise_w`` shape how
        expressive and how even the voice is (None: the voice's own)."""
        from piper.config import SynthesisConfig

        started = time.monotonic()
        config = SynthesisConfig(speaker_id=speaker, length_scale=1.0 / (rate or self.rate), noise_scale=noise_scale,
                                 noise_w_scale=noise_w)
        chunks = [c.audio_float_array for c in self.voice.synthesize(text, config)]
        audio = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)
        return Speech(audio, self.sample_rate, (time.monotonic() - started) * 1000)
