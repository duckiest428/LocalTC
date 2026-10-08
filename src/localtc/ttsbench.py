"""``localtc tts bench``: every voice on the same ATC lines, on this PC: how long it takes to load and to speak each
line, its real-time factor (time to synthesize / length of the speech: under 1 is faster than it's spoken), the
memory it adds, and with ``--whisper`` how well Whisper understands it through the radio effect (the word error
rate, after the readback parser's normalization), the measure LocalTC picked its Piper speakers by.

Azure's run costs its characters (about 500 for the lines below) and is skipped without a key.
"""

import statistics
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from localtc.config import Config
from localtc.tts.aviation import speakable
from localtc.tts.persona import persona_for

LINES = (
    ("Delta two three four, climb and maintain flight level 350, squawk 4521.", "Atlanta Center", "center"),
    ("Speedbird 12, runway 27L, cleared for takeoff, wind 270 at 8.", "Heathrow Tower", "tower"),
    ("American 45, turn left heading 250, descend and maintain 6,000, contact approach 119.2.", "Potomac Approach", "approach"),
    ("Cessna 2LT, taxi to runway 34R via A, B, hold short of runway 34L.", "Paine Ground", "ground"),
    ("United 9, cleared to land runway 16C, altimeter 29.92.", "Seattle Tower", "tower"),
)


def _rss_mb() -> float:
    """This process's memory (peak on macOS and Linux, now on Windows), in MB."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        c = Counters()
        c.cb = ctypes.sizeof(c)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.WorkingSetSize / 2**20 if ok else 0.0
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 1024


def bench(cfg: Config, *, providers: list[str] | None = None, whisper: bool = False) -> Iterator[str]:
    from localtc.app import voice_chain
    from localtc.dsp.radio import radio_effect, resample
    from localtc.tts.providers import TtsError, check_audio
    from localtc.tts.synth import PiperSynth
    from localtc.tts.voices import download

    t = cfg.tts
    wanted = providers or ["piper", "kokoro", "azure"]
    transcriber = None
    if whisper:
        from localtc.stt.whisper import WhisperTranscriber

        transcriber = WhisperTranscriber(cfg.voice.model if cfg.voice.model != "auto" else "base.en", device="cpu",
                                         compute_type="int8",
                                         models_dir=Path(cfg.voice.models_dir) if cfg.voice.models_dir else None)
        transcriber.warm_up()
    from localtc.voice import token_error_rate

    for pid in wanted:
        before = _rss_mb()
        started = time.monotonic()
        try:
            if pid == "piper":
                cfg.tts.provider, cfg.tts.kokoro = "piper", False
                chain = voice_chain(cfg, PiperSynth(download(t.voice, Path(t.voices_dir) if t.voices_dir else None), rate=t.rate))
            else:
                cfg.tts.provider, cfg.tts.kokoro = pid, False
                chain = voice_chain(cfg, None)
        except Exception as exc:  # noqa: BLE001
            yield f"{pid}: can't load ({type(exc).__name__}: {exc})"
            continue
        provider = next((p for p in chain.providers if p.id == pid), None) if chain else None
        if provider is None or (why := provider.ready()):
            yield f"{pid}: not ready ({why if provider else 'not configured'})"
            continue
        if hasattr(provider, "load"):
            provider.load()
        load_s = time.monotonic() - started
        provider.synthesize("Radio check.", persona_for("warm up"))
        latencies, rtfs, errors = [], [], []
        for text, station, role in LINES:
            words = speakable(text)
            try:
                speech = check_audio(provider.synthesize(words, persona_for(station, "atc", manner=role)), words)
            except TtsError as exc:
                yield f"  {pid}: {station}: {exc}"
                continue
            seconds = len(speech.audio) / speech.rate
            latencies.append(speech.latency_ms)
            rtfs.append(speech.latency_ms / 1000 / seconds)
            line = f"  {pid:6s} {speech.voice:28s} {speech.latency_ms:6.0f} ms  {seconds:4.1f} s  RTF {rtfs[-1]:.2f}"
            if transcriber is not None:
                heard = transcriber.transcribe(resample(radio_effect(speech.audio, speech.rate, static=t.static, seed=1),
                                                        speech.rate, 16000)).text
                errors.append(token_error_rate(words, heard))
                line += f"  WER {errors[-1]:.2f}  \"{heard}\""
            yield line
        if latencies:
            summary = (f"{pid}: load {load_s:.1f} s, +{max(0.0, _rss_mb() - before):.0f} MB, latency median "
                       f"{statistics.median(latencies):.0f} ms (max {max(latencies):.0f}), RTF {statistics.mean(rtfs):.2f}")
            if errors:
                summary += f", WER {statistics.mean(errors):.3f}"
            yield summary
