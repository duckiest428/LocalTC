"""Kokoro-82M on this PC: more natural voices than Piper, offline, at a cost in time (about 0.3 s of work per second of
speech on a fast CPU, where Piper takes 0.03).

The model is hexgrad's Kokoro-82M (Apache-2.0), run through ONNX Runtime by the ``kokoro-onnx`` package (MIT; it
brings ``phonemizer`` and espeak-ng, both GPL-3.0, as Piper does). The model files are kokoro-onnx's conversions
of the v1.0 weights, downloaded once to the voices folder (``[tts] voices_dir``) and checked against their SHA-256:
``kokoro-v1.0.onnx`` (310 MB, full precision: the quicker of the two on the CPUs tried) or ``kokoro-v1.0.int8.onnx``
(88 MB), and ``voices-v1.0.bin`` (27 MB, every voice).

Kokoro's English is American or British only: those voices, sorted by its own grades, the weakest left out. It has
few good male voices, so the pools add blends of two (a style vector mixed from both), each a voice of its own.
Stations are cast from the pool of their sex and region (``persona.Casting``): no two people share a voice while
another is free.
"""

import hashlib
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

from localtc.tts.persona import Casting, Persona
from localtc.tts.providers import TtsError
from localtc.tts.synth import Speech

log = logging.getLogger(__name__)

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {  # name: (bytes, sha256)
    "kokoro-v1.0.onnx": (325532387, "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
    "kokoro-v1.0.int8.onnx": (92361271, "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    "voices-v1.0.bin": (28214398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
MODELS = {"fp32": "kokoro-v1.0.onnx", "int8": "kokoro-v1.0.int8.onnx"}
VOICES_FILE = "voices-v1.0.bin"
SAMPLE_RATE = 24000
USER_AGENT = "LocalTC (+https://localtc.tech)"

# The pools, best first (Kokoro's VOICES.md grades; D and below left out, but for a blend). "a+b" is a blend: 60 % a.
POOLS: dict[tuple[str, str], tuple[str, ...]] = {
    ("en-us", "F"): ("af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore", "af_sarah", "af_nova", "af_alloy",
                     "af_heart+af_nicole", "af_bella+af_kore"),
    ("en-us", "M"): ("am_michael", "am_fenrir", "am_puck", "am_michael+am_fenrir", "am_puck+am_michael",
                     "am_fenrir+am_puck", "am_michael+am_echo", "am_fenrir+am_onyx", "am_puck+am_eric",
                     "am_michael+am_liam"),
    ("en-gb", "F"): ("bf_emma", "bf_isabella", "bf_emma+bf_isabella", "bf_isabella+bf_alice", "bf_emma+bf_lily"),
    ("en-gb", "M"): ("bm_george", "bm_fable", "bm_george+bm_fable", "bm_fable+bm_lewis", "bm_george+bm_daniel",
                     "bm_lewis"),
}
BRITISH = ("en-gb", "en-ie", "en-au", "en-nz", "en-za", "en-in", "en-sg", "en-hk")  # nearer British than American
BLEND = 0.6


def model_path(voices_dir: Path, model: str = "fp32") -> Path:
    return voices_dir / "kokoro" / MODELS.get(model, MODELS["fp32"])


def voices_path(voices_dir: Path) -> Path:
    return voices_dir / "kokoro" / VOICES_FILE


def installed(voices_dir: Path, model: str = "fp32") -> bool:
    return all(p.is_file() and p.stat().st_size == FILES[p.name][0]
               for p in (model_path(voices_dir, model), voices_path(voices_dir)))


def available() -> str:
    """"" when the kokoro-onnx package is installed; else what to install."""
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return "the kokoro-onnx package isn't installed (run Install LocalTC again)"
    return ""


def download(voices_dir: Path, model: str = "fp32", progress=None) -> Path:
    """The model and the voices, once, checked against their SHA-256; returns the model's path."""
    folder = voices_dir / "kokoro"
    folder.mkdir(parents=True, exist_ok=True)
    for name in (MODELS.get(model, MODELS["fp32"]), VOICES_FILE):
        size, digest = FILES[name]
        target = folder / name
        if target.is_file() and target.stat().st_size == size:
            continue
        part = target.with_suffix(target.suffix + ".part")
        h = hashlib.sha256()
        req = urllib.request.Request(RELEASE + name, headers={"User-Agent": USER_AGENT})
        log.info("Downloading Kokoro %s (%d MB) ...", name, size // 2**20)
        with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:
            got = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(f"Downloading Kokoro {name} ...", got / size)
        if h.hexdigest() != digest:
            part.unlink(missing_ok=True)
            raise OSError(f"{name}: the download doesn't match its checksum")
        part.replace(target)
    return model_path(voices_dir, model)


def pool_for(persona: Persona) -> tuple[str, list[str]]:
    """Kokoro's language ("en-us"/"en-gb") and the voices a persona is cast from: its sex, and its region's English
    (American for the US and Canada and none in particular, British for the rest of the English-speaking world)."""
    locale = persona.locale.lower()
    lang = "en-gb" if locale in BRITISH else "en-us"
    return lang, list(POOLS[(lang, persona.sex if persona.sex in ("F", "M") else "M")])


class KokoroVoices:
    id = "kokoro"
    name = "Kokoro"
    local = True

    def __init__(self, voices_dir: Path, *, model: str = "fp32", rate: float = 1.0, threads: int = 4,
                 engine: Any = None) -> None:
        """``rate``: the speaking speed (1 Kokoro's own: about Piper's 1.15). ``threads``: the CPU threads it may
        use (the sim needs the rest). ``engine``: a loaded ``kokoro_onnx.Kokoro`` (tests)."""
        self.voices_dir, self.model, self.rate, self.threads = voices_dir, model, rate, threads
        self.engine = engine
        self.casting = Casting()
        self._styles: dict[str, Any] = {}
        self.load_s = 0.0
        self.error = ""

    def ready(self) -> str:
        if self.engine is not None:
            return ""
        if self.error:
            return self.error
        if why := available():
            return why
        if not installed(self.voices_dir, self.model):
            return "the Kokoro model isn't downloaded (Settings > Voices, or localtc tts kokoro)"
        return ""

    def load(self) -> None:
        """Load the model (about half a second; a few hundred MB of memory)."""
        if self.engine is not None:
            return
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        started = time.monotonic()
        try:
            options = ort.SessionOptions()
            if self.threads:
                options.intra_op_num_threads = self.threads
            session = ort.InferenceSession(str(model_path(self.voices_dir, self.model)), options,
                                           providers=["CPUExecutionProvider"])
            self.engine = Kokoro.from_session(session, str(voices_path(self.voices_dir)))
        except Exception as exc:
            self.error = f"Kokoro didn't load: {type(exc).__name__}: {exc}"
            raise
        self.load_s = time.monotonic() - started
        log.info("Kokoro %s loaded (%.1f s, %d threads)", self.model, self.load_s, self.threads)

    def style(self, voice: str) -> Any:
        """A voice's style vector (a blend's mixed from its two)."""
        if voice not in self._styles:
            a, _, b = voice.partition("+")
            style = self.engine.get_voice_style(a)
            if b:
                style = BLEND * style + (1 - BLEND) * self.engine.get_voice_style(b)
            self._styles[voice] = style
        return self._styles[voice]

    def voice_for(self, persona: Persona) -> tuple[str, str]:
        lang, pool = pool_for(persona)
        return lang, self.casting.cast(persona, pool)

    def synthesize(self, text: str, persona: Persona) -> Speech:
        if why := self.ready():
            raise TtsError("unavailable", why)
        self.load()
        lang, voice = self.voice_for(persona)
        started = time.monotonic()
        speed = float(np.clip(self.rate * persona.pace, 0.6, 1.8))
        audio, rate = self.engine.create(text, self.style(voice), speed=speed, lang=lang)
        return Speech(np.asarray(audio, dtype=np.float32), int(rate), (time.monotonic() - started) * 1000, "kokoro", voice)
