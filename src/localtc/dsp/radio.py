"""Make clean synthesized speech sound like it came over an aircraft's VHF radio, built the way the real chain is:

    the speaker's microphone (a noise-cancelling boom mic: thin lows, a forward midrange), with the room or cockpit
    behind them  ->  the transmitter's speech processor (AGC: it lifts the background between words, the "pumping" of
    a real transmission) and AM modulation, over-driven on the peaks (the carrier can't go below zero: asymmetric
    clipping, the radio's edge)  ->  the narrow AM channel (about 300-2,800 Hz, boxy in the middle)  ->  the receiver:
    the carrier's fading and the receiver's hiss, more of both for a weak or distant station  ->  the squelch: a click
    and a moment of carrier before the words, and the burst of noise when the carrier drops ("kssht").

The pilot's own side (``sidetone``, ``SidetoneFilter``) is the same microphone and channel live, without the receiver.
Pure numpy, deterministic for a given seed, so tests can check it and replays sound the same.
"""

import numpy as np


def resample(audio: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target or len(audio) == 0:
        return audio.astype(np.float32)
    duration = len(audio) / rate
    t_new = np.arange(int(round(duration * target))) / target
    return np.interp(t_new, np.arange(len(audio)) / rate, audio).astype(np.float32)


def bandpass(audio: np.ndarray, rate: int, low: float = 300.0, high: float = 3000.0, *, presence_db: float = 3.0,
             presence_hz: float = 2000.0) -> np.ndarray:
    """A radio's pass band, with soft edges and a little presence around ``presence_hz``."""
    if len(audio) == 0:
        return audio
    n = 1 << int(np.ceil(np.log2(len(audio) * 2)))
    spectrum = np.fft.rfft(audio, n)
    f = np.fft.rfftfreq(n, 1 / rate)
    gain = np.clip((f - low * 0.6) / (low * 0.4), 0, 1) * np.clip((high * 1.25 - f) / (high * 0.25), 0, 1)
    gain = gain ** 2  # steeper skirts
    gain *= 1 + (10 ** (presence_db / 20) - 1) * np.exp(-(((f - presence_hz) / 600) ** 2))
    return np.fft.irfft(spectrum * gain, n)[: len(audio)].astype(np.float32)


def compress(audio: np.ndarray, rate: int, *, threshold: float = 0.1, ratio: float = 4.0, drive: float = 2.2) -> np.ndarray:
    """Radio AGC: loud and soft syllables come out nearly equally loud, lightly overdriven."""
    if len(audio) == 0:
        return audio
    audio = audio / (np.max(np.abs(audio)) or 1.0)
    frame = max(1, int(rate * 0.01))
    padded = np.pad(audio, (0, (-len(audio)) % frame))
    level = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1)) + 1e-6
    smoothed = level.copy()
    for i in range(1, len(smoothed)):  # fast attack, slower release (a few hundred frames: cheap)
        prev = smoothed[i - 1]
        smoothed[i] = level[i] if level[i] > prev else prev * 0.85 + level[i] * 0.15
    gain = np.where(smoothed > threshold, (threshold + (smoothed - threshold) / ratio) / smoothed, 1.0)
    per_sample = np.interp(np.arange(len(audio)), np.arange(len(gain)) * frame + frame / 2, gain)
    out = audio * per_sample
    peak = np.max(np.abs(out)) or 1.0
    return (np.tanh(drive * out / peak) / np.tanh(drive)).astype(np.float32)


def level(audio: np.ndarray, rate: int, *, floor: float = 0.02, up_s: float = 0.6) -> np.ndarray:
    """The transmitter's automatic level control: soft passages brought up, with a slow rise (``up_s``) after a loud
    one, so the background between words swells as a real transmission's does. Gain capped at 1/``floor``."""
    if len(audio) == 0:
        return audio
    audio = audio / (np.max(np.abs(audio)) or 1.0)
    frame = max(1, int(rate * 0.01))
    padded = np.pad(audio, (0, (-len(audio)) % frame))
    env = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1)) + 1e-6
    rise = 1 - np.exp(-0.01 / up_s)
    held = env.copy()
    for i in range(1, len(held)):  # instant down on a loud syllable, a slow climb back afterwards
        held[i] = env[i] if env[i] > held[i - 1] else held[i - 1] + (env[i] - held[i - 1]) * rise
    gain = 0.3 / np.maximum(held, floor)
    gain = np.minimum(gain, 1 / floor)
    per_sample = np.interp(np.arange(len(audio)), np.arange(len(gain)) * frame + frame / 2, gain)
    return (audio * per_sample).astype(np.float32)


def modulate(audio: np.ndarray, *, drive: float = 1.8, floor: float = -0.82) -> np.ndarray:
    """AM over-modulation: the peaks soft-clipped, the troughs harder (the carrier bottoms out at zero), so the clip
    is lopsided, the even harmonics that give a radio voice its edge."""
    x = audio / (np.max(np.abs(audio)) or 1.0) * drive
    up = np.tanh(x)
    down = np.maximum(np.tanh(x * 1.3), floor)
    return np.where(x >= 0, up, down).astype(np.float32)


def pink(rng: np.random.Generator, n: int) -> np.ndarray:
    """Pink (1/f) noise, peak 1: a cockpit's or a room's rumble and air."""
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spectrum), dtype=np.float64)
    spectrum[1:] /= np.sqrt(f[1:])
    spectrum[0] = 0
    out = np.fft.irfft(spectrum, n)
    return (out / (np.max(np.abs(out)) or 1.0)).astype(np.float32)


def _noise(rng: np.random.Generator, n: int, rate: int) -> np.ndarray:
    return bandpass(rng.standard_normal(n).astype(np.float32), rate, 400.0, 4000.0, presence_db=0.0)


def _click(rng: np.random.Generator, rate: int, *, loud: float = 1.0) -> np.ndarray:
    """A relay's click: a few milliseconds of a damped, filtered impulse."""
    n = int(0.012 * rate)
    t = np.arange(n) / rate
    tick = np.sin(2 * np.pi * rng.uniform(1600, 2400) * t) * np.exp(-t * 900)
    tick += rng.standard_normal(n) * np.exp(-t * 1400) * 0.4
    return (tick / (np.max(np.abs(tick)) or 1.0) * loud).astype(np.float32)


# The channel: a VHF AM airband receiver's audio, narrower than a telephone, boxy around 1.4 kHz.
LOW_HZ, HIGH_HZ, BOX_HZ = 320.0, 2800.0, 1400.0


def radio_effect(audio: np.ndarray, rate: int, *, static: float = 0.35, seed: int = 0, squelch: bool = True,
                 strength: float = 1.0, cabin: float = 0.0) -> np.ndarray:
    """``audio`` (mono float, any level) as a radio transmission at the same rate, peak 0.9.

    ``static``: the receiver's hiss and squelch (0 none: your own side). ``strength``: the signal, 1 a strong nearby
    station, toward 0 a distant one (more hiss, deeper and quicker fading, a crackle now and then). ``cabin``: the
    background behind the speaker (0 a quiet tower cab, 1 a jet's flight deck), lifted between the words by the
    transmitter's level control as on a real radio."""
    rng = np.random.default_rng(seed)
    if len(audio) == 0:
        return audio.astype(np.float32)
    strength = float(np.clip(strength, 0.05, 1.0))
    voice = audio.astype(np.float32)
    voice = voice / (np.max(np.abs(voice)) or 1.0)
    pre = post = 0
    if squelch and static > 0:  # keyed a moment before the first word, let go a moment after the last
        pre, post = int(rng.uniform(0.08, 0.16) * rate), int(rng.uniform(0.05, 0.12) * rate)
        voice = np.concatenate([np.zeros(pre, np.float32), voice, np.zeros(post, np.float32)])
    # The microphone: a boom mic's thin lows and forward middle, the speaker's surroundings behind them.
    mic = bandpass(voice, rate, 180.0, 4500.0, presence_db=2.5, presence_hz=2600.0)
    if cabin > 0:
        rumble = bandpass(pink(rng, len(mic)), rate, 150.0, 2500.0, presence_db=0.0)
        mic = mic + rumble / (np.max(np.abs(rumble)) or 1.0) * 0.018 * cabin
    # The transmitter: levelled (the background swells between the words), then the lopsided AM clip.
    sent = modulate(level(mic, rate, floor=0.03 if cabin > 0 else 0.06))
    sent = compress(sent, rate, threshold=0.2, ratio=3.0, drive=1.4)
    voice = bandpass(sent, rate, LOW_HZ, HIGH_HZ, presence_db=4.0, presence_hz=BOX_HZ)
    voice /= np.max(np.abs(voice)) or 1.0
    # The receiver: the carrier fading a little (more and quicker when weak), a crackle on a weak one, its hiss.
    t = np.arange(len(voice)) / rate
    depth = 0.05 + 0.3 * (1 - strength)
    fade = 1 - depth * (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(1.0, 2.5 + 4 * (1 - strength)) * t + rng.uniform(0, 6.28)))
    voice = voice * fade
    if strength < 0.6:
        for _ in range(int(len(voice) / rate * (0.6 - strength) * 6)):
            at, span = int(rng.uniform(0, len(voice))), int(rng.uniform(0.01, 0.04) * rate)
            voice[at:at + span] *= rng.uniform(0.15, 0.5)
    hiss = _noise(rng, len(voice), rate)
    hiss /= np.max(np.abs(hiss)) or 1.0
    out = voice * 0.85 + hiss * static * (0.05 + 0.25 * (1 - strength))
    if squelch and static > 0:
        tail = int(rng.uniform(0.12, 0.2) * rate)
        burst = _noise(rng, tail, rate)
        burst /= np.max(np.abs(burst)) or 1.0
        # The carrier gone: the receiver's full noise until the squelch closes ("kssht"), then its click.
        release = burst * np.exp(-np.linspace(0, 4, tail)) * (0.35 + 0.45 * static)
        opening = _click(rng, rate, loud=0.35 * static)
        closing = _click(rng, rate, loud=0.25 * static)
        out = np.concatenate([opening, out, release, closing])
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak * 0.9).astype(np.float32)


def intercom_effect(audio: np.ndarray, rate: int) -> np.ndarray:
    """The crew intercom: a headset's wide band (200-5000 Hz), lightly levelled, no hiss and no squelch. Clearly
    a person beside you, not the radio."""
    if len(audio) == 0:
        return audio.astype(np.float32)
    voice = bandpass(audio.astype(np.float32), rate, 200.0, 5000.0, presence_db=1.5)
    voice = compress(voice, rate, threshold=0.2, ratio=2.0, drive=1.2)
    peak = np.max(np.abs(voice)) or 1.0
    return (voice / peak * 0.8).astype(np.float32)


def key_sound(rate: int, *, press: bool, seed: int = 0) -> np.ndarray:
    """Your push-to-talk: pressed, the switch's click and the transmitter coming up in your headset; let go, the
    click and a short rush of the receiver's noise before its squelch closes. Peak 0.9."""
    rng = np.random.default_rng(seed + (1 if press else 2))
    click = _click(rng, rate)
    n = int((0.05 if press else 0.09) * rate)
    rush = _noise(rng, n, rate)
    rush /= np.max(np.abs(rush)) or 1.0
    envelope = np.linspace(0.0, 1.0, n) ** 2 * np.exp(-np.linspace(0, 3, n)) if press else np.exp(-np.linspace(0, 5, n))
    out = np.concatenate([click, rush * envelope * (0.15 if press else 0.35)])
    return (out / (np.max(np.abs(out)) or 1.0) * 0.9).astype(np.float32)


def clean(audio: np.ndarray) -> np.ndarray:
    """No radio: just a sensible level."""
    peak = np.max(np.abs(audio)) if len(audio) else 0.0
    return (audio / peak * 0.8).astype(np.float32) if peak else audio.astype(np.float32)


class SidetoneFilter:
    """Your voice through the radio's channel, live, block by block, as the microphone gives it: what you hear of
    yourself in the headset while you transmit. A windowed-sinc band-pass (FIR, its last samples carried across
    blocks, so there are no seams) and a light clip, then to the output's rate."""

    def __init__(self, rate: int, out_rate: int, *, taps: int = 129) -> None:
        self.rate, self.out_rate = rate, out_rate
        n = np.arange(taps) - (taps - 1) / 2
        lo, hi = LOW_HZ / rate, HIGH_HZ / rate
        kernel = 2 * hi * np.sinc(2 * hi * n) - 2 * lo * np.sinc(2 * lo * n)
        self.kernel = (kernel * np.hamming(taps)).astype(np.float32)
        self.history = np.zeros(taps - 1, np.float32)
        self.phase = 0.0  # where the next output sample falls, in this block's samples (-1: on the last block's last)
        self.last = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.concatenate([self.history, block.astype(np.float32)])
        self.history = x[-(len(self.kernel) - 1):]
        y = np.convolve(x, self.kernel, mode="valid")
        y = np.tanh(y * 3.0) / np.tanh(3.0)
        if not len(y):
            return np.zeros(0, np.float32)
        # Linear resampling between the last block's last sample and this one's, the position carried over.
        step = self.rate / self.out_rate
        positions = np.arange(self.phase, len(y) - 1 + 1e-9, step)
        out = np.interp(positions + 1, np.arange(len(y) + 1), np.concatenate([[self.last], y]))
        self.phase = (positions[-1] if len(positions) else self.phase - step) + step - len(y)
        self.last = float(y[-1])
        return out.astype(np.float32)
