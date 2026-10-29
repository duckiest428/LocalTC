"""The local models a flight uses, what they cost, and which suit this computer.

Three models, each a trade between quality and speed:
- the language model (Ollama) that reads pilot calls,
- Whisper (faster-whisper) that hears them,
- the Piper voice that speaks for ATC.

``PROFILES`` bundles one of each for light, balanced and quality setups. ``recommend``
picks a profile from the hardware (RAM, NVIDIA GPU). The installer (``localtc setup --profile``)
and the app's Quick Settings both use this module, and both download with ``install``.
"""

import ctypes
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    size_mb: int
    speed: str  # how fast, in words a pilot understands
    quality: str
    note: str = ""
    tested: bool = False  # LocalTC's edge cases were run against it


LLM_MODELS = (
    # One model: the one LocalTC is built and tested around (its prompts, examples and checks are tuned to it).
    ModelOption("llama3.2:3b", "Llama 3.2 3B", 2000, "fast", "good",
                "Gets 53 of LocalTC's 54 edge cases, about 2 s per call on a CPU.", tested=True),
)
WHISPER_MODELS = (
    ModelOption("tiny.en", "Tiny", 75, "instant", "basic", "Misses numbers more often."),
    ModelOption("base.en", "Base", 145, "fast", "good", "Recommended without an NVIDIA GPU. About 1 s per call.",
                tested=True),
    ModelOption("small.en", "Small", 480, "medium on CPU", "better",
                "Recommended with an NVIDIA GPU. 2-4 s per call on a CPU.", tested=True),
    ModelOption("medium.en", "Medium", 1500, "slow on CPU", "best", "Needs an NVIDIA GPU for live use."),
)
PIPER_VOICES = (
    ModelOption("en_US-libritts_r-medium", "LibriTTS (every controller a different voice)", 80, "fast", "good",
                "904 speakers in one download: each station keeps its own voice.", tested=True),
)


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    llm: str
    whisper: str
    voice: str
    description: str


PROFILES = (
    Profile("light", "Light", "llama3.2:3b", "tiny.en", "en_US-libritts_r-medium",
            "Older or busy computers: the quickest speech recognition, a few more misheard calls."),
    Profile("balanced", "Balanced", "llama3.2:3b", "base.en", "en_US-libritts_r-medium",
            "Most computers: what LocalTC is tested with."),
    Profile("quality", "Quality", "llama3.2:3b", "small.en", "en_US-libritts_r-medium",
            "An NVIDIA GPU: Whisper hears more accurately at the same speed."),
)


@dataclass(frozen=True)
class Hardware:
    os: str
    cpu_cores: int
    ram_gb: float
    gpu: str = ""  # NVIDIA card name, "" without one
    gpu_vram_gb: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def detect_hardware() -> Hardware:
    gpu, vram = _nvidia()
    return Hardware(os=sys.platform, cpu_cores=os.cpu_count() or 1, ram_gb=round(_ram_bytes() / 2**30, 1),
                    gpu=gpu, gpu_vram_gb=vram)


def recommend(hw: Hardware) -> Profile:
    by_id = {p.id: p for p in PROFILES}
    if hw.gpu and hw.gpu_vram_gb >= 4:
        return by_id["quality"]
    if 0 < hw.ram_gb < 12 or hw.cpu_cores < 6:
        return by_id["light"]
    return by_id["balanced"]


def profile(profile_id: str) -> Profile:
    for p in PROFILES:
        if p.id == profile_id:
            return p
    raise ValueError(f"unknown profile {profile_id!r}; one of: {', '.join(p.id for p in PROFILES)}")


def apply_profile(cfg, chosen: Profile) -> None:
    cfg.llm.model = chosen.llm
    cfg.voice.model = chosen.whisper
    cfg.tts.voice = chosen.voice


def _ram_bytes() -> int:
    try:
        if sys.platform == "win32":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return int(status.ullTotalPhys)
        if sys.platform == "darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return 0


def _nvidia() -> tuple[str, float]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "", 0.0
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        name, memory = out.stdout.strip().splitlines()[0].rsplit(",", 1)
        return name.strip(), round(float(memory) / 1024, 1)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return "", 0.0


# --- what's installed, and installing it ------------------------------------------------------------------------


@dataclass
class ModelStatus:
    kind: str  # llm, whisper, voice
    id: str
    installed: bool
    detail: str = ""


def status(cfg) -> list[ModelStatus]:
    """Are this config's models downloaded?"""
    from localtc.stt.whisper import default_models_dir, model_path
    from localtc.tts.voices import installed as voice_installed

    out = []
    if cfg.llm.enabled:
        state = _ollama(cfg).status(timeout_s=1.5)
        out.append(ModelStatus("llm", cfg.llm.model, state.reachable and state.has(cfg.llm.model),
                               "" if state.reachable else "Ollama isn't running"))
    whisper = whisper_model(cfg)
    models_dir = Path(cfg.voice.models_dir) if cfg.voice.models_dir else default_models_dir()
    out.append(ModelStatus("whisper", whisper, (model_path(whisper, models_dir) / "model.bin").exists()))
    voices_dir = Path(cfg.tts.voices_dir) if cfg.tts.voices_dir else None
    out.append(ModelStatus("voice", cfg.tts.voice, voice_installed(cfg.tts.voice, voices_dir)))
    from localtc.languages import language

    if (lang := language(cfg.crew.language)) is not None and cfg.crew.enabled and lang.voices:
        # The copilot talking in the pilot's language: that language's voice pack on this PC.
        out.append(ModelStatus("voice_lang", ", ".join(lang.voices),
                               all(voice_installed(v, voices_dir) for v in lang.voices)))
    return out


def whisper_model(cfg) -> str:
    """The Whisper model a flight will load ("auto" resolved without importing the GPU libraries)."""
    model = cfg.voice.model if cfg.voice.model != "auto" else \
        "small.en" if cfg.voice.device == "cuda" or (cfg.voice.device == "auto" and _nvidia()[0]) else "base.en"
    from localtc.languages import language

    # The copilot talked to in another language: the same size that knows them all (the radio still heard as English).
    return model.removesuffix(".en") if language(cfg.crew.language) is not None else model


Progress = Callable[[str, float | None], None]  # (message, fraction 0-1 or None when unknown)


def install(cfg, kind: str, progress: Progress | None = None) -> bool:
    """Download one of this config's models. Returns True when it's ready."""
    say = progress or (lambda message, fraction: None)
    if kind == "whisper":
        from localtc.stt.whisper import download

        model = whisper_model(cfg)
        say(f"Downloading Whisper {model} ...", None)
        download(model, Path(cfg.voice.models_dir) if cfg.voice.models_dir else None)
        say(f"Whisper {model} ready", 1.0)
        return True
    if kind == "voice":
        from localtc.tts.voices import download, voice_path

        voices_dir = Path(cfg.tts.voices_dir) if cfg.tts.voices_dir else None
        say(f"Downloading voice {cfg.tts.voice} ...", None)
        path = download(cfg.tts.voice, voices_dir)
        say(f"Voice ready ({path.stat().st_size // 2**20} MB)" if path.exists() else "Voice ready", 1.0)
        return voice_path(cfg.tts.voice, voices_dir).exists()
    if kind == "voice_lang":
        from localtc.languages import language
        from localtc.tts.voices import download, installed

        lang = language(cfg.crew.language)
        if lang is None or not lang.voices:
            return True
        voices_dir = Path(cfg.tts.voices_dir) if cfg.tts.voices_dir else None
        for i, voice in enumerate(lang.voices):
            say(f"Downloading the {lang.english} voices ...", i / len(lang.voices))
            download(voice, voices_dir, lambda f, i=i: say(f"Downloading the {lang.english} voices ...",
                                                           (i + f) / len(lang.voices)))
        say(f"The {lang.english} voices are ready", 1.0)
        return all(installed(v, voices_dir) for v in lang.voices)
    if kind == "kokoro":
        from localtc.tts import kokoro
        from localtc.tts.voices import default_voices_dir

        if why := kokoro.available():
            say(f"Kokoro can't run: {why}", None)
            return False
        voices_dir = Path(cfg.tts.voices_dir) if cfg.tts.voices_dir else default_voices_dir()
        kokoro.download(voices_dir, cfg.tts.kokoro_model, say)
        say("Kokoro ready", 1.0)
        return kokoro.installed(voices_dir, cfg.tts.kokoro_model)
    if kind == "llm":
        from localtc import components

        backend = _ollama(cfg)
        state = backend.status()
        if not state.reachable and sys.platform == "win32":
            # Ollama first: installed if it isn't (the setup leaves it to the app), started if it is.
            if not components.install_ollama(say):
                return False
            state = backend.status()
        if not state.reachable:
            say("Ollama isn't running: install it from ollama.com and start it", None)
            return False
        if state.has(cfg.llm.model):
            say(f"{cfg.llm.model} is installed", 1.0)
            return True
        return backend.pull(on_progress=lambda text, fraction: say(f"{cfg.llm.model}: {text}", fraction))
    raise ValueError(f"unknown model kind {kind!r}")


def _ollama(cfg):
    from localtc.llm import OllamaBackend

    return OllamaBackend(model=cfg.llm.model, base_url=cfg.llm.base_url, keep_alive=cfg.llm.keep_alive, num_ctx=cfg.llm.num_ctx)


def catalog() -> dict:
    return {"llm": [asdict(m) for m in LLM_MODELS], "whisper": [asdict(m) for m in WHISPER_MODELS],
            "voice": [asdict(m) for m in PIPER_VOICES], "profiles": [asdict(p) for p in PROFILES]}
