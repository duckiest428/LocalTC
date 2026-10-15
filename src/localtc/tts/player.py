"""Plays the radio and the intercom on the speakers or headset (sounddevice / PortAudio).

Two channels, as in a headset: the radio (ATC, the other flights, the ATIS, your own side) and the intercom (the
copilot beside you), each one line after another. Radio clips never overlap, as on a real frequency; the copilot
talks over the radio when it has to, as a real one does. The radio can be cut: the ATIS when the pilot tunes away,
everything from the old frequency the moment the pilot changes it, and whatever is being said when the pilot keys
the microphone.
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
    kind: str = "atc"  # atc, atis, pilot (the copilot on the radio), intercom (the copilot beside you)
    done: threading.Event = field(default_factory=threading.Event)
    station: str | None = None  # who's on the radio saying it: cut when the pilot tunes away from them


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


class _Lane:
    """One channel: its clips one after another."""

    def __init__(self, player: "AudioPlayer", name: str) -> None:
        self.player = player
        self.queue: "queue.Queue[Clip | None]" = queue.Queue()
        self.cut_now = threading.Event()
        self.playing: Clip | None = None
        self.thread = threading.Thread(target=self.run, name=f"localtc-tts-{name}", daemon=True)

    def drop(self, gone) -> None:
        """Stop the playing clip and drop the queued ones that ``gone(clip)`` says."""
        kept = []
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if item is not None and gone(item):
                item.done.set()
            else:
                kept.append(item)
        for item in kept:
            self.queue.put(item)
        if self.playing is not None and gone(self.playing):
            self.cut_now.set()

    def run(self) -> None:
        import sounddevice as sd

        try:
            rate = int(sd.query_devices(self.player.device, "output")["default_samplerate"])
        except (sd.PortAudioError, ValueError) as exc:
            log.warning("No audio output (%s): ATC will be text only", exc)
            return
        while (clip := self.queue.get()) is not None:
            if clip.done.is_set():
                continue  # dropped while it waited
            self.playing, audio = clip, resample(clip.audio, clip.rate, rate) * self.player.volume
            self.cut_now.clear()
            try:
                with sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=self.player.device) as stream:
                    block = int(rate * BLOCK_S)
                    for start in range(0, len(audio), block):
                        if self.cut_now.is_set():
                            break
                        stream.write(np.ascontiguousarray(audio[start : start + block], dtype=np.float32))
            except sd.PortAudioError as exc:
                log.warning("Audio output failed: %s", exc)
            finally:
                self.playing = None
                clip.done.set()


RADIO = ("atc", "pilot")  # what's said on the frequency (the ATIS is cut on its own)


class AudioPlayer:
    def __init__(self, device: str | int | None = None, *, volume: float = 0.8) -> None:
        self.device = find_output(device)
        self.volume = volume
        self.radio = _Lane(self, "radio")
        self.intercom = _Lane(self, "intercom")
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
            self.radio.thread.start()
            self.intercom.thread.start()

    def play(self, clip: Clip) -> Clip:
        self.start()
        (self.intercom if clip.kind == "intercom" else self.radio).queue.put(clip)
        return clip

    @property
    def busy(self) -> bool:
        return any(lane.playing is not None or not lane.queue.empty() for lane in (self.radio, self.intercom))

    def cut(self, kind: str) -> None:
        """Stop the playing clip and drop queued ones of this kind."""
        for lane in (self.radio, self.intercom):
            lane.drop(lambda c: c.kind == kind)

    def tuned(self, station: str | None) -> None:
        """The pilot changed frequency, to ``station``'s (None: nobody's): nothing more from the old one."""
        self.radio.drop(lambda c: c.kind in RADIO and c.station is not None and c.station != station)

    def keyed(self) -> None:
        """The pilot keyed the microphone: whatever is being said on the radio stops (the copilot's intercom
        doesn't)."""
        self.radio.drop(lambda c: c.kind in RADIO)

    def close(self) -> None:
        self.radio.queue.put(None)
        self.intercom.queue.put(None)
