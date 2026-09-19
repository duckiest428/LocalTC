"""Microphone capture for push-to-talk, plus WAV helpers. Uses sounddevice (PortAudio, bundled on
Windows and macOS).

The stream stays open for the whole session and keeps a short ring buffer, so pressing the
push-to-talk key a moment after starting to speak doesn't cut the first syllable.
"""

import threading
import wave
from collections import deque
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def resample(audio: np.ndarray, rate: int, target: int = SAMPLE_RATE) -> np.ndarray:
    if rate == target or len(audio) == 0:
        return audio.astype(np.float32)
    if rate % target == 0:  # 48 kHz -> 16 kHz: average each group (a crude low-pass, fine for speech)
        n = rate // target
        usable = len(audio) - len(audio) % n
        return audio[:usable].reshape(-1, n).mean(axis=1).astype(np.float32)
    duration = len(audio) / rate
    t_new = np.linspace(0, duration, int(duration * target), endpoint=False)
    return np.interp(t_new, np.arange(len(audio)) / rate, audio).astype(np.float32)


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def read_wav(path: str | Path) -> np.ndarray:
    """Mono float32 at 16 kHz from a 16-bit PCM WAV of any rate and channel count."""
    with wave.open(str(path), "rb") as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"{path}: only 16-bit PCM is supported")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return resample(audio, rate)


def write_wav(path: str | Path, audio: np.ndarray, rate: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(to_pcm16(audio))


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0


def trim_silence(audio: np.ndarray, rate: int = SAMPLE_RATE, pad_s: float = 0.4) -> np.ndarray:
    """The clip without the quiet before and after the words (the switch held while thinking).
    Whisper is faster on less audio, and makes up fewer words from silence."""
    frame = int(rate * 0.02)
    if len(audio) < frame * 10:
        return audio
    usable = len(audio) - len(audio) % frame
    levels = np.sqrt(np.mean(np.square(audio[:usable].reshape(-1, frame)), axis=1))
    threshold = max(float(np.percentile(levels, 10)) * 3, float(levels.max()) * 0.05, 0.002)
    loud = np.flatnonzero(levels > threshold)
    if len(loud) == 0:
        return audio
    pad = int(pad_s * rate)
    start, end = max(0, loud[0] * frame - pad), min(len(audio), (loud[-1] + 1) * frame + pad)
    return audio[start:end]


def input_devices() -> list[tuple[int, str, int]]:
    """(index, name, default sample rate) of every device with an input."""
    import sounddevice as sd

    return [(i, d["name"], int(d["default_samplerate"])) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0]


def find_device(spec: str | int | None) -> int | None:
    """``spec``: None/"" for the system default, an index, or part of a device name."""
    if spec in (None, ""):
        return None
    if isinstance(spec, int) or str(spec).isdigit():
        return int(spec)
    matches = [i for i, name, _ in input_devices() if str(spec).lower() in name.lower()]
    if not matches:
        raise ValueError(f"no input device matching {spec!r}; see: localtc voice devices")
    return matches[0]


class AudioCapture:
    def __init__(self, device: str | int | None = None, *, pre_roll_s: float = 0.3, max_clip_s: float = 30.0) -> None:
        self.spec = device  # None/"": whatever the system's default input is
        self.device = find_device(device)
        self.pre_roll_s = pre_roll_s
        self.max_clip_s = max_clip_s
        self.rate = SAMPLE_RATE
        self._lock = threading.Lock()
        self._pre: deque[np.ndarray] = deque()
        self._pre_len = 0
        self._clip: list[np.ndarray] | None = None
        self._clip_len = 0
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=self.device,
                                          callback=self._callback)
        except sd.PortAudioError:
            # Some devices (WASAPI) only run at their own rate: capture at that and resample.
            self.rate = int(sd.query_devices(self.device, "input")["default_samplerate"])
            self._stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32", device=self.device,
                                          callback=self._callback)
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    @property
    def name(self) -> str:
        """The microphone in use."""
        import sounddevice as sd

        try:
            return str(sd.query_devices(self.device, "input")["name"])
        except (sd.PortAudioError, ValueError):
            return "unknown microphone"

    def refresh(self) -> str:
        """Reopen the microphone. With the system default, this picks up a default changed since the
        start (PortAudio only reads the device list once). Returns the microphone's name."""
        import sounddevice as sd

        self.close()
        if self.spec in (None, ""):
            sd._terminate()
            sd._initialize()
        self.rate = SAMPLE_RATE
        self.start()
        return self.name

    def begin(self) -> None:
        """Push-to-talk pressed: start a clip with the last ``pre_roll_s`` already in it."""
        with self._lock:
            self._clip = list(self._pre)
            self._clip_len = self._pre_len

    def end(self) -> np.ndarray:
        """Push-to-talk released: the clip at 16 kHz."""
        with self._lock:
            blocks, self._clip = self._clip or [], None
        audio = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
        return resample(audio, self.rate)

    def _callback(self, indata, frames, time_info, status) -> None:
        block = indata[:, 0].copy()
        with self._lock:
            if self._clip is not None and self._clip_len < self.max_clip_s * self.rate:
                self._clip.append(block)
                self._clip_len += len(block)
            self._pre.append(block)
            self._pre_len += len(block)
            while self._pre and self._pre_len - len(self._pre[0]) >= self.pre_roll_s * self.rate:
                self._pre_len -= len(self._pre.popleft())
