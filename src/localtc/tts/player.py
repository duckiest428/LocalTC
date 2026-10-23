"""Plays the radio and the intercom on the speakers or headset (sounddevice / PortAudio).

Two channels, as in a headset: the radio (ATC, the other flights, the ATIS, your own side) and the intercom (the
copilot beside you), each one line after another; and the radio's own sounds (your push-to-talk's click), which never
wait for either. Radio clips never overlap, as on a real frequency; the copilot talks over the radio when it has to,
as a real one does. The radio can be cut: the ATIS when the pilot tunes away, everything from the old frequency the
moment the pilot changes it, and whatever is being said when the pilot keys the microphone.

Each kind of sound has its own volume (the mixer: ``mix``, under the master ``volume``), applied as it plays, so a
slider moved mid-sentence is heard at once. The sidetone (``Sidetone``) is you on the radio in your headset while you
hold the key.
"""

import logging
import queue
import threading
from dataclasses import dataclass, field

import numpy as np

from localtc.dsp.radio import resample

log = logging.getLogger(__name__)
BLOCK_S = 0.05

# The mixer's channels: who is heard, each with its own volume ([mixer]).
CHANNELS = ("atc", "atis", "chatter", "copilot_radio", "copilot", "effects", "sidetone")
# A clip's channel when it doesn't say: by its kind.
KIND_CHANNEL = {"atc": "atc", "atis": "atis", "pilot": "copilot_radio", "intercom": "copilot", "effect": "effects"}


@dataclass
class Clip:
    audio: np.ndarray
    rate: int
    kind: str = "atc"  # atc, atis, pilot (the copilot on the radio), intercom (the copilot beside you), effect
    done: threading.Event = field(default_factory=threading.Event)
    station: str | None = None  # who's on the radio saying it: cut when the pilot tunes away from them
    channel: str = ""  # the mixer's channel (CHANNELS); "" by its kind (another flight's exchange is "chatter")

    @property
    def mixer_channel(self) -> str:
        return self.channel or KIND_CHANNEL.get(self.kind, "atc")


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
            self.playing, audio = clip, resample(clip.audio, clip.rate, rate)
            self.cut_now.clear()
            try:
                with sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=self.player.device) as stream:
                    block = int(rate * BLOCK_S)
                    for start in range(0, len(audio), block):
                        if self.cut_now.is_set():
                            break
                        gain = self.player.gain(clip.mixer_channel)  # each block: the mixer moved mid-line is heard
                        stream.write(np.ascontiguousarray(audio[start : start + block] * gain, dtype=np.float32))
            except sd.PortAudioError as exc:
                log.warning("Audio output failed: %s", exc)
            finally:
                self.playing = None
                clip.done.set()


RADIO = ("atc", "pilot")  # what's said on the frequency (the ATIS is cut on its own)


class AudioPlayer:
    def __init__(self, device: str | int | None = None, *, volume: float = 0.8, mix: dict[str, float] | None = None) -> None:
        self.device = find_output(device)
        self.volume = volume  # the master
        self.mix: dict[str, float] = dict(mix or {})  # each channel's volume (CHANNELS; 1 when not given, 0 muted)
        self.radio = _Lane(self, "radio")
        self.intercom = _Lane(self, "intercom")
        self.effects = _Lane(self, "effects")
        self._started = False

    def gain(self, channel: str) -> float:
        """What a channel plays at now: its own volume times the master."""
        return self.volume * self.mix.get(channel, 1.0)

    @property
    def lanes(self) -> tuple[_Lane, ...]:
        return (self.radio, self.intercom, self.effects)

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
            for lane in self.lanes:
                lane.thread.start()

    def play(self, clip: Clip) -> Clip:
        self.start()
        (self.intercom if clip.kind == "intercom" else self.effects if clip.kind == "effect" else self.radio).queue.put(clip)
        return clip

    @property
    def busy(self) -> bool:
        return any(lane.playing is not None or not lane.queue.empty() for lane in (self.radio, self.intercom))

    def cut(self, kind: str) -> None:
        """Stop the playing clip and drop queued ones of this kind."""
        for lane in self.lanes:
            lane.drop(lambda c: c.kind == kind)

    def tuned(self, station: str | None) -> None:
        """The pilot changed frequency, to ``station``'s (None: nobody's): nothing more from the old one."""
        self.radio.drop(lambda c: c.kind in RADIO and c.station is not None and c.station != station)

    def keyed(self) -> None:
        """The pilot keyed the microphone: whatever is being said on the radio stops (the copilot's intercom
        doesn't)."""
        self.radio.drop(lambda c: c.kind in RADIO)

    def close(self) -> None:
        for lane in self.lanes:
            lane.queue.put(None)


class Sidetone:
    """You on the radio, in your headset, while you hold the key: the microphone's blocks through the radio's channel
    (``dsp.radio.SidetoneFilter``) to the output, live. One stream, opened the first time it's wanted and kept (one
    opened per press would lose the first word), silent while the key is up. Nothing at volume 0: with speakers, the
    microphone would hear it."""

    def __init__(self, player: AudioPlayer) -> None:
        self.player = player
        self.in_rate = 0  # the microphone's (from its blocks)
        self.keyed = False
        self._buffer: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=50)
        self._pending = np.zeros(0, np.float32)
        self._stream = None
        self._filter = None

    @property
    def wanted(self) -> bool:
        return self.player.gain("sidetone") > 0

    def start(self) -> bool:
        import sounddevice as sd

        from localtc.dsp.radio import SidetoneFilter

        if self._stream is not None:
            return True
        try:
            out_rate = int(sd.query_devices(self.player.device, "output")["default_samplerate"])
            self._filter = SidetoneFilter(self.in_rate, out_rate)
            self._stream = sd.OutputStream(samplerate=out_rate, channels=1, dtype="float32", device=self.player.device,
                                           latency="low", callback=self._play)
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - no sidetone is never a reason to stop the voice
            log.warning("Sidetone unavailable: %s", exc)
            self._stream = None
            return False
        return True

    def feed(self, block: np.ndarray, rate: int) -> None:
        """A block from the microphone at rate (on its thread), while the key is held."""
        if not self.keyed or not self.wanted:
            return
        if rate != self.in_rate:  # the first block, or the microphone reopened at another rate
            self.close()
            self.in_rate = rate
        if self._stream is None and not self.start():
            return
        try:
            self._buffer.put_nowait(self._filter.process(block))
        except queue.Full:  # the output stalled: never let it fall seconds behind
            pass

    def key(self, down: bool) -> None:
        self.keyed = down
        if not down:
            while True:
                try:
                    self._buffer.get_nowait()
                except queue.Empty:
                    break

    def _play(self, outdata, frames, time_info, status) -> None:
        have = self._pending
        while len(have) < frames:
            try:
                have = np.concatenate([have, self._buffer.get_nowait()])
            except queue.Empty:
                break
        out, self._pending = have[:frames], have[frames:]
        if not self.keyed:
            self._pending = np.zeros(0, np.float32)
        outdata[:, 0] = 0.0
        outdata[: len(out), 0] = out * (self.player.gain("sidetone") if self.keyed else 0.0)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001 - a stream that died on its own may not stop cleanly
                pass
            self._stream = None
