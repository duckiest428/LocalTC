"""Make clean synthesized speech sound like it came over a VHF radio.

    band-pass (300-3000 Hz)  ->  compression + soft clipping  ->  static  ->  squelch bursts

Pure numpy, deterministic for a given seed, so tests can check it and replays sound the same.
"""

import numpy as np


def resample(audio: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target or len(audio) == 0:
        return audio.astype(np.float32)
    duration = len(audio) / rate
    t_new = np.arange(int(round(duration * target))) / target
    return np.interp(t_new, np.arange(len(audio)) / rate, audio).astype(np.float32)


def bandpass(audio: np.ndarray, rate: int, low: float = 300.0, high: float = 3000.0, *, presence_db: float = 3.0) -> np.ndarray:
    """A radio's pass band, with soft edges and a little presence around 1.5-2.5 kHz."""
    if len(audio) == 0:
        return audio
    n = 1 << int(np.ceil(np.log2(len(audio) * 2)))
    spectrum = np.fft.rfft(audio, n)
    f = np.fft.rfftfreq(n, 1 / rate)
    gain = np.clip((f - low * 0.6) / (low * 0.4), 0, 1) * np.clip((high * 1.25 - f) / (high * 0.25), 0, 1)
    gain = gain ** 2  # steeper skirts
    gain *= 1 + (10 ** (presence_db / 20) - 1) * np.exp(-(((f - 2000) / 600) ** 2))
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


def _noise(rng: np.random.Generator, n: int, rate: int) -> np.ndarray:
    return bandpass(rng.standard_normal(n).astype(np.float32), rate, 400.0, 4000.0, presence_db=0.0)


def radio_effect(audio: np.ndarray, rate: int, *, static: float = 0.35, seed: int = 0, squelch: bool = True) -> np.ndarray:
    """``audio`` (mono float, any level) as a radio transmission at the same rate, peak 0.9."""
    rng = np.random.default_rng(seed)
    if len(audio) == 0:
        return audio.astype(np.float32)
    voice = bandpass(audio.astype(np.float32), rate)
    voice = compress(voice, rate)
    # A carrier that isn't perfectly steady: slow, slight fading.
    t = np.arange(len(voice)) / rate
    voice *= 1 - 0.06 * (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(1.5, 3.0) * t + rng.uniform(0, 6.28)))
    hiss = _noise(rng, len(voice), rate)
    hiss /= np.max(np.abs(hiss)) or 1.0
    out = voice * 0.85 + hiss * 0.07 * static
    if squelch and static > 0:
        head, tail = int(0.07 * rate), int(0.18 * rate)
        burst = _noise(rng, head + tail, rate)
        burst /= np.max(np.abs(burst)) or 1.0
        key_up = burst[:head] * np.linspace(0.5, 0.15, head) * static
        release = burst[head:] * np.exp(-np.linspace(0, 5, tail)) * 0.8 * static  # the "kshh" when the mic is let go
        out = np.concatenate([key_up, out, release])
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak * 0.9).astype(np.float32)


def clean(audio: np.ndarray) -> np.ndarray:
    """No radio: just a sensible level."""
    peak = np.max(np.abs(audio)) if len(audio) else 0.0
    return (audio / peak * 0.8).astype(np.float32) if peak else audio.astype(np.float32)
