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
# The most a call can say: a few words a second (a fast radio call is 4), and room for the callsign. (The prompt and
# these together stay inside Whisper's 448.)
TOKENS_BASE, TOKENS_PER_S, TOKENS_MAX = 24, 8, 200
MAX_LENGTH = 448  # Whisper's: the prompt, the hotwords and the words it may say, all together
PROMPT_PART = MAX_LENGTH // 2 - 1  # faster-whisper keeps this much of the prompt, and of the hotwords
SPECIAL_TOKENS = 8  # start of transcript, language, task, no timestamps (and room to spare)
TEMPERATURES = (0.0, 0.3, 0.6)
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


def multilingual(model: str) -> str:
    """The same size of model that knows every language ("small.en": "small"), for a copilot talked to in one."""
    return model.removesuffix(".en")


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

    def _tokens(self, text: str) -> list[int]:
        tokenizer = getattr(self._model, "hf_tokenizer", None)
        if not text.strip() or tokenizer is None:
            return []
        return list(tokenizer.encode(" " + text.strip(), add_special_tokens=False).ids)

    def budget(self, prompt: str, hotwords: str, want: int) -> tuple[list[int] | str, int]:
        """The prompt (as tokens, its start cut off if need be) and the most words Whisper may say, inside its 448:
        a 21 s call with a long prompt asked for 455 and the call was lost."""
        hot = min(len(self._tokens(hotwords)), PROMPT_PART)
        tokens = self._tokens(prompt)[-PROMPT_PART:]
        if not tokens:
            return prompt, max(1, min(want, MAX_LENGTH - SPECIAL_TOKENS - hot))
        room = MAX_LENGTH - SPECIAL_TOKENS - hot - want
        if len(tokens) > room:  # the words it may say come first; the prompt's oldest words go
            tokens = tokens[len(tokens) - max(room, 0):] if room > 0 else []
        return tokens, max(1, min(want, MAX_LENGTH - SPECIAL_TOKENS - hot - len(tokens)))

    def transcribe(self, audio, *, prompt: str = "", hotwords: str = "", language: str = "en") -> SttResult:
        """``audio``: mono float32 samples at 16 kHz. ``language``: what's said in it (the radio English; the
        intercom the copilot's language, with a multilingual model)."""
        self.load()
        started = time.monotonic()
        seconds = len(audio) / SAMPLE_RATE
        initial, max_new = self.budget(prompt, hotwords, min(TOKENS_MAX, int(TOKENS_BASE + TOKENS_PER_S * seconds)))
        segments, _info = self._model.transcribe(
            audio,
            language=language if not self.choice.model.endswith(".en") else "en",
            beam_size=self.beam_size,
            initial_prompt=initial or None,
            hotwords=hotwords or None,
            condition_on_previous_text=False,
            # Never more words than the audio could hold, and a garbled try retried twice at most: 0.8 s of noise came
            # back as "A,B4, A5, A7, ..." sixty times over, after 28 s of retries.
            max_new_tokens=max_new,
            temperature=TEMPERATURES,
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
