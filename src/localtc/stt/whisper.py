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
import re
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
SIZES = ("tiny", "base", "small", "medium", "distil-large-v3", "large-v3")  # smallest first
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


def cuda_libraries() -> bool:
    """Windows: the CUDA libraries ctranslate2 loads (cuBLAS, cuDNN) are installed (Quick Settings > Models > Whisper on
    your graphics card). An NVIDIA driver alone isn't enough: Whisper on the card without them fails at its first
    words ("cublas64_12.dll is not found"). Elsewhere they come with the system's CUDA, so they're taken as there."""
    if sys.platform != "win32":
        return True
    import importlib.util

    try:
        return importlib.util.find_spec("nvidia.cublas") is not None and importlib.util.find_spec("nvidia.cudnn") is not None
    except (ImportError, ValueError):
        return False


def cuda_available() -> bool:
    if not cuda_libraries():
        return False
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


# Where the models come from: Hugging Face, then its public mirror (huggingface.co can't be reached from China, where
# a pilot's every download timed out and every flight stopped before it began).
HUB_ENDPOINTS = ("https://huggingface.co", "https://hf-mirror.com")
HUB_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def download(model: str, models_dir: Path | None = None) -> Path:
    """Fetch a model into the local folder (once); returns its path. Each of ``HUB_ENDPOINTS`` is tried in turn."""
    import huggingface_hub
    from faster_whisper.utils import _MODELS

    target = model_path(model, models_dir or default_models_dir())
    if (target / "model.bin").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    repo = _MODELS.get(model, model)
    error: Exception | None = None
    for endpoint in HUB_ENDPOINTS:
        log.info("Downloading Whisper %s to %s (from %s)", model, target, endpoint)
        try:
            return Path(huggingface_hub.snapshot_download(repo, local_dir=str(target), allow_patterns=HUB_FILES,
                                                          endpoint=endpoint))
        except Exception as exc:  # no network, the host blocked, ...
            log.warning("Whisper %s not downloaded from %s: %s", model, endpoint, str(exc).splitlines()[0])
            error = exc
    assert error is not None
    raise error


def installed(models_dir: Path) -> list[str]:
    """The Whisper models in the local folder, smallest first."""
    have = [p.name.removeprefix("faster-whisper-") for p in models_dir.glob("faster-whisper-*") if (p / "model.bin").exists()]
    return sorted(have, key=_size)


def stand_in(model: str, models_dir: Path) -> str | None:
    """An installed model to use when ``model`` can't be downloaded: the same size in the other kind (``base`` for
    ``base.en``), else the largest installed one no bigger, else the smallest bigger one."""
    have = installed(models_dir)
    if not have:
        return None
    for same in (multilingual(model), model + ".en"):
        if same in have:
            return same
    smaller = [m for m in have if _size(m) <= _size(model)]
    return smaller[-1] if smaller else have[0]


def _size(model: str) -> int:
    size = model.removesuffix(".en")
    return SIZES.index(size) if size in SIZES else len(SIZES)


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
        self.stand_in_for = ""  # the model asked for, when it couldn't be downloaded and an installed one is used

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
            try:
                path = download(self.choice.model, self.models_dir)
            except Exception:
                other = stand_in(self.choice.model, self.models_dir)
                if other is None:
                    raise
                log.warning("Whisper %s couldn't be downloaded; using Whisper %s, already on this PC",
                            self.choice.model, other)
                self.stand_in_for = self.choice.model
                self.choice = Choice(other, self.choice.device, self.choice.compute_type)
                path = model_path(other, self.models_dir)
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

    def language_of(self, audio) -> tuple[str, float]:
        """The language spoken in ``audio`` and how sure of it (a multilingual model); ("", 0.0): can't tell."""
        if self.choice.model.endswith(".en"):
            return "", 0.0
        try:
            self._load()
            code, probability, *_ = self._model.detect_language(audio)
            return code, float(probability)
        except Exception as exc:  # noqa: BLE001 - telling is a nicety: the words are still heard as English
            log.info("Couldn't tell the language: %s", exc)
            return "", 0.0

    def transcribe(self, audio, *, prompt: str = "", hotwords: str = "", language: str = "en") -> SttResult:
        """``audio``: mono float32 samples at 16 kHz. ``language``: what's said in it (the radio English; the
        intercom the copilot's language, with a multilingual model). On the graphics card, a CUDA library that can't be
        loaded (it's found only at the first words) moves Whisper to the CPU, and the words are heard again there."""
        try:
            return self._transcribe(audio, prompt=prompt, hotwords=hotwords, language=language)
        except (RuntimeError, OSError) as exc:
            if self.choice.device != "cuda" or not re.search(r"cuda|cublas|cudnn|\.dll", str(exc), re.IGNORECASE):
                raise
            log.warning("Whisper on the graphics card failed (%s); using the CPU from now on", exc)
            self.choice = Choice(self.choice.model, "cpu", "int8")
            self._model = None
            return self._transcribe(audio, prompt=prompt, hotwords=hotwords, language=language)

    def _transcribe(self, audio, *, prompt: str, hotwords: str, language: str) -> SttResult:
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
