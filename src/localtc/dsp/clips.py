"""The transmissions kept to play again: what ATC and the copilot said (as heard, through the radio effect) and what the
pilot said into the microphone (the audio speech-to-text heard).

Off unless ``[ui] replay_audio`` is on. Each clip is filed under its event's id (``clip_id``: the event's kind, time
and words), the same id the radio log puts on the line, so the app, the phone and the website find it by the line.
Kept small, as a telephone line is: 8 kHz, 8 bits, the last ``KEEP`` of them, in memory only.
"""

import hashlib
import io
import wave
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import numpy as np

RATE = 8000
KEEP = 80
MAX_S = 20.0  # longer is cut (an ATIS isn't kept at all)


def clip_id(ev: Any) -> str:
    """The id an event's audio is filed under: its kind, its time and its words."""
    tag = getattr(getattr(ev, "__struct_config__", None), "tag", type(ev).__name__)
    return hashlib.sha1(f"{tag}|{float(ev.t):.3f}|{getattr(ev, 'text', '')}".encode()).hexdigest()[:16]


def to_wav(audio: Any, rate: int) -> bytes:
    """Mono audio (floats, -1..1; or int16) as an 8 kHz, 8-bit WAV, peaks at 90 percent."""
    x = np.asarray(audio)
    x = x.astype(np.float32) / 32768.0 if x.dtype == np.int16 else x.astype(np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x[: int(MAX_S * rate)]
    if rate != RATE and len(x):
        n = max(int(len(x) * RATE / rate), 1)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 1e-4:
        x = x * (0.9 / peak)
    pcm = np.clip(np.round(x * 127 + 128), 0, 255).astype(np.uint8)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


class ClipStore:
    def __init__(self, keep: int = KEEP) -> None:
        self.keep = keep
        self.enabled = False
        self.clips: OrderedDict[str, bytes] = OrderedDict()
        self.listeners: list[Callable[[str, bytes], None]] = []  # told of each new clip (the relay to the phone)

    def put(self, key: str, audio: Any, rate: int) -> bytes | None:
        if not self.enabled or not key:
            return None
        wav = to_wav(audio, rate)
        self.clips[key] = wav
        self.clips.move_to_end(key)
        while len(self.clips) > self.keep:
            self.clips.popitem(last=False)
        for listener in list(self.listeners):
            try:
                listener(key, wav)
            except Exception:  # noqa: BLE001 - a listener failing never stops the voice
                pass
        return wav

    def get(self, key: str) -> bytes | None:
        return self.clips.get(key)

    def clear(self) -> None:
        self.clips.clear()


CLIPS = ClipStore()
