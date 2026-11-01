"""The cabin's sounds, made here rather than recorded: the chimes, the boarding music, the applause, and the two ways the
cabin crew are heard on the flight deck.

- The **address system** (``pa_effect``): the cabin's ceiling speakers, a narrow, boxy band with the cabin's short
  echo behind it, heard through the flight deck door.
- The **interphone** (``interphone_effect``): a cabin crew member on the handset to the flight deck, dry and close, a
  telephone's band, no hiss and no squelch.
- The **chimes** (``chime``): struck bars, their partials a little out of tune with each other as a real bar's are.
  ``pa`` is the high-low pair before an announcement, ``seatbelt`` one low note when the sign changes, ``call`` two
  quick high notes when the cabin calls the flight deck.
- The **boarding music** (``boarding_music``): a slow, soft progression on an electric piano and a pad, through the
  cabin speakers, made the same for a given seed (each airline its own).
- The **applause** (``applause``): a cabin of people clapping, each at their own pace, starting and stopping raggedly.

Pure numpy and deterministic for a given seed, like ``dsp.radio``.
"""

import numpy as np

from localtc.dsp.radio import bandpass, compress


def _bar(rate: int, freq: float, seconds: float, *, loud: float = 1.0) -> np.ndarray:
    """One struck bar: a fast attack, the partials of a bar (not a string's), each dying away at its own rate."""
    n = int(rate * seconds)
    t = np.arange(n) / rate
    out = np.zeros(n)
    for ratio, gain, decay in ((1.0, 1.0, 1.6), (2.01, 0.35, 2.8), (2.76, 0.22, 3.6), (5.40, 0.08, 6.0)):
        out += gain * np.sin(2 * np.pi * freq * ratio * t + ratio) * np.exp(-decay * t)
    attack = np.minimum(1.0, t / 0.004)
    # A slow beat between two nearly equal bars: the shimmer of a real chime.
    shimmer = 1 + 0.06 * np.sin(2 * np.pi * 3.1 * t)
    return (out * attack * shimmer * loud).astype(np.float32)


CHIMES = ("pa", "seatbelt", "call")


def chime(rate: int, kind: str = "pa") -> np.ndarray:
    """``pa`` the high and the low note, ``seatbelt`` one low note, ``call`` two quick high ones. Peak 0.6."""
    if kind == "seatbelt":
        notes = [(0.0, 659.3, 1.8, 1.0)]
    elif kind == "call":
        notes = [(0.0, 1046.5, 0.6, 0.8), (0.28, 1046.5, 1.4, 0.8)]
    else:
        notes = [(0.0, 880.0, 1.4, 1.0), (0.62, 698.5, 2.0, 0.9)]
    n = int(rate * max(start + length for start, _, length, _ in notes))
    out = np.zeros(n, dtype=np.float32)
    for start, freq, length, loud in notes:
        bar = _bar(rate, freq, length, loud=loud)
        at = int(rate * start)
        out[at:at + len(bar)] += bar[: n - at]
    out = bandpass(out, rate, 250.0, 6000.0, presence_db=0.0)  # through the ceiling speakers
    return (out / (np.max(np.abs(out)) or 1.0) * 0.6).astype(np.float32)


def _echo(audio: np.ndarray, rate: int, *, seconds: float = 0.22, mix: float = 0.16, seed: int = 0) -> np.ndarray:
    """A long, narrow room's short echo: the voice convolved with a little decaying noise."""
    rng = np.random.default_rng(seed)
    n = int(rate * seconds)
    tail = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    tail[0] = 0.0
    wet = np.convolve(audio, tail / (np.sum(np.abs(tail)) or 1.0) * 6)[: len(audio)]
    return audio + mix * wet


def pa_effect(audio: np.ndarray, rate: int) -> np.ndarray:
    """The address system as the flight deck hears it: the ceiling speakers' narrow, boxy band, the cabin's echo, and
    the levelling of a microphone held close. Peak 0.75."""
    if len(audio) == 0:
        return audio.astype(np.float32)
    voice = bandpass(audio.astype(np.float32), rate, 280.0, 4200.0, presence_db=4.0, presence_hz=1300.0)
    voice = compress(voice, rate, threshold=0.18, ratio=3.0, drive=1.5)
    voice = _echo(voice, rate)
    return (voice / (np.max(np.abs(voice)) or 1.0) * 0.75).astype(np.float32)


def interphone_effect(audio: np.ndarray, rate: int) -> np.ndarray:
    """A cabin crew member on the handset: a telephone's band, close and dry. Peak 0.8."""
    if len(audio) == 0:
        return audio.astype(np.float32)
    voice = bandpass(audio.astype(np.float32), rate, 300.0, 3400.0, presence_db=2.0, presence_hz=1800.0)
    voice = compress(voice, rate, threshold=0.2, ratio=2.5, drive=1.3)
    return (voice / (np.max(np.abs(voice)) or 1.0) * 0.8).astype(np.float32)


# Chords as semitones above the key: a slow, warm progression (major sevenths and ninths, nothing that resolves hard).
PROGRESSIONS = (
    ((0, 4, 7, 11), (9, 12, 16, 19), (5, 9, 12, 16), (7, 11, 14, 17)),
    ((0, 4, 7, 14), (5, 9, 12, 16), (2, 5, 9, 12), (7, 11, 14, 19)),
    ((2, 5, 9, 12), (7, 11, 14, 17), (0, 4, 7, 11), (9, 12, 16, 19)),
)


def _note(rate: int, freq: float, seconds: float, *, soft: bool) -> np.ndarray:
    n = int(rate * seconds)
    t = np.arange(n) / rate
    if soft:  # the pad: a slow swell and fade, two slightly detuned tones
        env = np.minimum(1.0, t / 1.2) * np.minimum(1.0, (seconds - t) / 1.0).clip(0, 1)
        return (env * (np.sin(2 * np.pi * freq * t) + np.sin(2 * np.pi * freq * 1.003 * t)) * 0.5).astype(np.float32)
    # the electric piano: a bell-like attack that mellows
    env = np.exp(-2.2 * t) * np.minimum(1.0, t / 0.006)
    tone = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-6 * t)
    return (env * tone).astype(np.float32)


def boarding_music(rate: int, *, seconds: float = 64.0, seed: int = 0) -> np.ndarray:
    """A slow, soft progression through the cabin's speakers, to loop under the boarding. Peak 0.5."""
    rng = np.random.default_rng(seed)
    key = 196.0 * 2 ** (int(rng.integers(0, 7)) / 12)  # somewhere from G3 up
    chords = PROGRESSIONS[int(rng.integers(0, len(PROGRESSIONS)))]
    bar_s = 4.0
    n = int(rate * seconds)
    out = np.zeros(n + int(rate * bar_s), dtype=np.float32)
    beats = int(seconds / bar_s)
    for bar in range(beats):
        chord = chords[bar % len(chords)]
        at = int(rate * bar * bar_s)
        for i, semis in enumerate(chord):  # the pad holds the chord
            pad = _note(rate, key / 2 * 2 ** (semis / 12), bar_s + 0.8, soft=True) * 0.18
            out[at:at + len(pad)] += pad[: len(out) - at]
        # the piano picks the chord out, its rhythm changing from bar to bar
        steps = rng.choice((4, 6, 8))
        for step in range(steps):
            if rng.random() < 0.2:
                continue
            semis = chord[int(rng.integers(0, len(chord)))] + (12 if rng.random() < 0.35 else 0)
            note = _note(rate, key * 2 ** (semis / 12), 1.6, soft=False) * (0.22 + 0.1 * rng.random())
            start = at + int(rate * step * bar_s / steps)
            out[start:start + len(note)] += note[: len(out) - start]
    out = out[:n]
    fade = int(rate * 1.5)  # in and out, so the loop's seam isn't heard as a click
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    out = bandpass(out, rate, 180.0, 5000.0, presence_db=0.0)  # the cabin's ceiling speakers
    return (out / (np.max(np.abs(out)) or 1.0) * 0.5).astype(np.float32)


def applause(rate: int, *, seconds: float = 6.0, people: int = 40, seed: int = 0) -> np.ndarray:
    """A cabin clapping: each person at their own pace, joining in and stopping at their own moment, behind the
    flight deck door. Peak 0.5."""
    rng = np.random.default_rng(seed)
    n = int(rate * seconds)
    out = np.zeros(n, dtype=np.float32)
    clap_n = int(rate * 0.03)
    decay = np.exp(-np.linspace(0, 9, clap_n))
    for _ in range(people):
        start = rng.uniform(0.0, 0.9)
        stop = rng.uniform(seconds * 0.45, seconds - 0.6)
        pace = rng.uniform(3.0, 5.5)  # claps a second
        loud = rng.uniform(0.3, 1.0)
        t = start
        while t < stop:
            at = int(rate * t)
            clap = rng.standard_normal(clap_n) * decay * loud * rng.uniform(0.7, 1.0)
            out[at:at + clap_n] += clap[: n - at].astype(np.float32)
            t += 1 / pace * rng.uniform(0.85, 1.15)
    out = bandpass(out, rate, 600.0, 4000.0, presence_db=2.0, presence_hz=1500.0)  # hands, through a door
    envelope = np.minimum(1.0, np.arange(n) / (rate * 0.4))
    return (out * envelope / (np.max(np.abs(out)) or 1.0) * 0.5).astype(np.float32)
