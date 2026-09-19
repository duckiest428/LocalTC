"""Plays radio clips one after another on the speakers or headset (sounddevice / PortAudio).

Clips never overlap: a second transmission waits for the first, as on a real frequency. A clip
can be cut off (the ATIS, when the pilot tunes away).
"""

import logging
import queue
import threading
from dataclasses import dataclass, field

import numpy as np

from localtc.dsp.radio import resample

log = logging.getLogger(__name__)
BLOCK_S = 0.05


@dataclass
class Clip:
    audio: np.ndarray
    rate: int
    kind: str = "atc"  # atc, atis, pilot
    done: threading.Event = field(default_factory=threading.Event)


def output_devices() -> list[tuple[int, str, int]]:
    import sounddevice as sd

    return [(i, d["name"], int(d["default_samplerate"])) for i, d in enumerate(sd.query_devices())
            if d["max_output_channels"] > 0]


def find_output(spec: str | int | None) -> int | None:
    if spec in (None, ""):
        return None
    if isinstance(spec, int) or str(spec).isdigit():
        return int(spec)
    matches = [i for i, name, _ in output_devices() if str(spec).lower() in name.lower()]
    if not matches:
        raise ValueError(f"no output device matching {spec!r}; see: localtc tts devices")
    return matches[0]


class AudioPlayer:
    def __init__(self, device: str | int | None = None, *, volume: float = 0.8) -> None:
        self.device = find_output(device)
        self.volume = volume
        self._queue: "queue.Queue[Clip | None]" = queue.Queue()
        self._cut = threading.Event()
        self._playing: Clip | None = None
        self._thread = threading.Thread(target=self._run, name="localtc-tts-player", daemon=True)
        self._started = False

    @property
    def name(self) -> str:
        import sounddevice as sd

        try:
            return str(sd.query_devices(self.device, "output")["name"])
        except (sd.PortAudioError, ValueError):
            return "unknown output"

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def play(self, clip: Clip) -> Clip:
        self.start()
        self._queue.put(clip)
        return clip

    @property
    def busy(self) -> bool:
        return self._playing is not None or not self._queue.empty()

    def cut(self, kind: str) -> None:
        """Stop the playing clip and drop queued ones of this kind."""
        kept = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None and item.kind == kind:
                item.done.set()
            else:
                kept.append(item)
        for item in kept:
            self._queue.put(item)
        if self._playing is not None and self._playing.kind == kind:
            self._cut.set()

    def close(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        import sounddevice as sd

        try:
            rate = int(sd.query_devices(self.device, "output")["default_samplerate"])
        except (sd.PortAudioError, ValueError) as exc:
            log.warning("No audio output (%s): ATC will be text only", exc)
            return
        while (clip := self._queue.get()) is not None:
            self._playing, audio = clip, resample(clip.audio, clip.rate, rate) * self.volume
            self._cut.clear()
            try:
                with sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=self.device) as stream:
                    block = int(rate * BLOCK_S)
                    for start in range(0, len(audio), block):
                        if self._cut.is_set():
                            break
                        stream.write(np.ascontiguousarray(audio[start : start + block], dtype=np.float32))
            except sd.PortAudioError as exc:
                log.warning("Audio output failed: %s", exc)
            finally:
                self._playing = None
                clip.done.set()
