"""faster-whisper, set up for short push-to-talk clips on whatever hardware there is.

- ``model="auto"``: ``small.en`` with an NVIDIA GPU, ``base.en`` on CPU (fast enough for a few
  seconds of speech on any recent CPU).
- ``device="auto"``: CUDA when ctranslate2 can see a GPU and load cuBLAS/cuDNN, else CPU. The
  CUDA libraries come from the ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` pip packages on
  Windows; their DLL folders are added to the search path here.
- Models live in a local folder (``localtc setup`` downloads them), so a flight never needs
  the network.
"""

import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MODELS = ("tiny.en", "base.en", "small.en", "medium.en", "large-v3", "distil-large-v3")


def default_models_dir() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "LocalTC" / "models"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "localtc" / "models"


def add_nvidia_dll_dirs() -> list[str]:
    """Windows: make the pip-installed CUDA libraries findable by ctranslate2. Returns the folders added."""
    if sys.platform != "win32":
        return []
    added = []
    try:
        import nvidia  # namespace package from nvidia-cublas-cu12 / nvidia-cudnn-cu12
    except ImportError:
        return []
    for root in getattr(nvidia, "__path__", []):
        for bin_dir in Path(root).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
    return added


def cuda_available() -> bool:
    try:
        add_nvidia_dll_dirs()
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # no ctranslate2, no driver, ...
        return False


@dataclass(frozen=True)
class Choice:
    model: str
    device: str
    compute_type: str


def choose(model: str = "auto", device: str = "auto", compute_type: str = "auto") -> Choice:
    cuda = device == "cuda" or (device == "auto" and cuda_available())
    return Choice(
        model=model if model != "auto" else ("small.en" if cuda else "base.en"),
        device="cuda" if cuda else "cpu",
        compute_type=compute_type if compute_type != "auto" else ("float16" if cuda else "int8"),
    )


def model_path(model: str, models_dir: Path) -> Path:
    return models_dir / f"faster-whisper-{model}"


def download(model: str, models_dir: Path | None = None) -> Path:
    """Fetch a model into the local folder (once); returns its path."""
    from faster_whisper import download_model

    target = model_path(model, models_dir or default_models_dir())
    if (target / "model.bin").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    log.info("Downloading Whisper %s to %s", model, target)
    return Path(download_model(model, output_dir=str(target)))


@dataclass(frozen=True)
class SttResult:
    text: str
    confidence: float  # 0-1, from the segments' average log-probability
    no_speech: float  # Whisper's probability that there was no speech
    latency_ms: float
    audio_s: float


class WhisperTranscriber:
    def __init__(self, model: str = "auto", *, device: str = "auto", compute_type: str = "auto",
                 models_dir: str | Path | None = None, beam_size: int = 5, allow_download: bool = True) -> None:
        self.choice = choose(model, device, compute_type)
        self.models_dir = Path(models_dir) if models_dir else default_models_dir()
        self.beam_size = beam_size
        self.allow_download = allow_download
        self._model: Any = None

    @property
    def description(self) -> str:
        return f"Whisper {self.choice.model} on {self.choice.device} ({self.choice.compute_type})"

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        path = model_path(self.choice.model, self.models_dir)
        if not (path / "model.bin").exists():
            if not self.allow_download:
                raise FileNotFoundError(f"Whisper {self.choice.model} isn't installed in {self.models_dir}; "
                                        "run: localtc setup")
            path = download(self.choice.model, self.models_dir)
        try:
            self._model = WhisperModel(str(path), device=self.choice.device, compute_type=self.choice.compute_type)
        except Exception as exc:
            if self.choice.device != "cuda":
                raise
            log.warning("Whisper on CUDA failed (%s); using the CPU", exc)
            self.choice = Choice(self.choice.model, "cpu", "int8")
            self._model = WhisperModel(str(path), device="cpu", compute_type="int8")
        log.info("%s ready", self.description)

    def transcribe(self, audio, *, prompt: str = "", hotwords: str = "") -> SttResult:
        """``audio``: mono float32 samples at 16 kHz."""
        self.load()
        started = time.monotonic()
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            beam_size=self.beam_size,
            initial_prompt=prompt or None,
            hotwords=hotwords or None,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,  # push-to-talk already marks the speech
        )
        segments = list(segments)  # decoding happens while iterating
        text = " ".join(s.text.strip() for s in segments).strip()
        total = sum(max(s.end - s.start, 0.01) for s in segments)
        logprob = sum(s.avg_logprob * max(s.end - s.start, 0.01) for s in segments) / total if segments else -10.0
        return SttResult(
            text=text,
            confidence=round(math.exp(min(logprob, 0.0)), 3),
            no_speech=round(max((s.no_speech_prob for s in segments), default=1.0), 3),
            latency_ms=round((time.monotonic() - started) * 1000, 1),
            audio_s=round(len(audio) / SAMPLE_RATE, 2),
        )

    def warm_up(self) -> float:
        """Load the model and run it once; returns seconds taken."""
        import numpy as np

        started = time.monotonic()
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
        return time.monotonic() - started
