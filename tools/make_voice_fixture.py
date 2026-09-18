"""Generate the synthetic voice fixtures with macOS speech (``say``): pilots with different accents,
through a radio-like filter with cockpit noise. macOS only; the fixtures play on any OS.

    python tools/make_voice_fixture.py

- tests/fixtures/voice_kpae/: a recording of the KPAE departure (clearance through the departure
  check-in) flown with spoken transmissions: sim events, push-to-talk, WAVs, and the script as the
  transcripts. ``localtc voice eval tests/fixtures/voice_kpae`` re-transcribes it with Whisper.
- tests/fixtures/voice_clips/: the edge-case sentences from localtc/llm/eval_cases.toml, spoken.
"""

import asyncio
import hashlib
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import msgspec
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from localtc import __version__  # noqa: E402
from localtc.airports import load_airport_dir  # noqa: E402
from localtc.recorder import SCHEMA_VERSION, Recorder, RecordingHeader  # noqa: E402
from localtc.scenario import load_scenario, run  # noqa: E402
from localtc.sim_api import AirportData, SessionInfo  # noqa: E402
from localtc.stt.audio import read_wav, resample, write_wav  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
VOICES = ["Samantha", "Daniel", "Karen", "Rishi", "Moira", "Reed (English (US))", "Eddy (English (UK))", "Albert"]
STORE_RATE = 8000  # radio audio has nothing above ~3.4 kHz; half the size of 16 kHz


def synthesize(text: str, voice: str, rate_wpm: int = 190) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "s.aiff", Path(tmp) / "s.wav"
        for attempt in range(3):  # the macOS speech service occasionally hangs on a call
            try:
                subprocess.run(["say", "-v", voice, "-r", str(rate_wpm), "-o", str(aiff), text], check=True, timeout=30)
                break
            except subprocess.TimeoutExpired:
                print(f"  say timed out ({voice}: {text!r}), retrying", flush=True)
        else:
            raise RuntimeError(f"say kept hanging on {text!r}")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        return read_wav(wav)


def radio(audio: np.ndarray, seed: int, snr_db: float = 18.0) -> np.ndarray:
    """Headset-and-cockpit: 300-3400 Hz, engine-ish noise, a little clipping; 0.3 s of noise either side."""
    rng = np.random.default_rng(seed)
    pad = np.zeros(int(0.3 * 16000), dtype=np.float32)
    audio = np.concatenate([pad, audio, pad])
    spectrum = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(len(audio), 1 / 16000)
    spectrum[(freqs < 300) | (freqs > 3400)] = 0
    speech = np.fft.irfft(spectrum, len(audio))
    noise = np.cumsum(rng.standard_normal(len(audio)))  # brown noise, like an engine
    noise -= np.convolve(noise, np.ones(400) / 400, mode="same")
    noise += 0.3 * rng.standard_normal(len(audio))
    level = np.sqrt(np.mean(speech[len(pad):-len(pad)] ** 2)) / (10 ** (snr_db / 20))
    noise *= level / np.sqrt(np.mean(noise**2))
    mixed = np.tanh(2.0 * (speech + noise)) / 2.0
    return (0.6 * mixed / np.max(np.abs(mixed))).astype(np.float32)


class SynthVoice:
    """The scenario's pilot, speaking: each transmission becomes a clip in ``audio/``."""

    def __init__(self, out: Path) -> None:
        self.out, self.n = out, 0

    def speak(self, text: str, t: float):
        self.n += 1
        voice = VOICES[(self.n - 1) % len(VOICES)]
        audio = radio(synthesize(text, voice), seed=self.n)
        ref = f"audio/{self.n:04d}.wav"
        write_wav(self.out / ref, resample(audio, 16000, STORE_RATE), STORE_RATE)

        class Clip:
            duration_s = round(len(audio) / 16000, 2)
            audio_ref = ref
            confidence = None

        Clip.text = text  # the script: the recording's reference transcript
        return Clip


async def departure() -> None:
    out = FIXTURES / "voice_kpae"
    for old in [*out.glob("session.jsonl*"), *out.glob("audio/*.wav")]:
        old.unlink()
    (out / "audio").mkdir(parents=True, exist_ok=True)
    path = ROOT / "tests" / "scenarios" / "ifr_happy_path.toml"
    scenario = load_scenario(path)
    scenario.scenario.end_t = 640
    scenario.scenario.tail_s = 0
    events = [AirportData(t=0.0, airport=a) for a in load_airport_dir(FIXTURES / "airports")]
    result = run(scenario, path.parent, voice=SynthVoice(out), on_input=events.append)
    header = RecordingHeader(
        schema=SCHEMA_VERSION, localtc_version=__version__, created="2026-09-18T00:00:00+00:00",
        session=SessionInfo(source_kind="live", sim_product="MSFS 2024", sim_version="synthetic"),
        config={"note": "synthetic voice KPAE departure from tools/make_voice_fixture.py",
                "flight": msgspec.to_builtins(scenario.flight), "atc": msgspec.to_builtins(scenario.atc)},
    )
    recorder = Recorder(out, header, compress=True)
    async with recorder:
        for event in events:
            recorder.record(event)
    print(result.transcript)
    print(f"Wrote {recorder.events_written} events and {len(list(out.glob('audio/*.wav')))} clips to {out}")


def clips() -> None:
    out = FIXTURES / "voice_clips"
    for old in out.glob("*.wav"):
        old.unlink()
    out.mkdir(parents=True, exist_ok=True)
    cases = tomllib.loads((ROOT / "src" / "localtc" / "llm" / "eval_cases.toml").read_text(encoding="utf-8"))["case"]
    lines = ["# Spoken edge cases (tools/make_voice_fixture.py): name -> the WAV, the voice, and the words.\n"]
    for i, case in enumerate(cases):
        voice = VOICES[i % len(VOICES)]
        name = hashlib.sha1(case["pilot"].encode()).hexdigest()[:10]
        audio = radio(synthesize(case["pilot"], voice), seed=1000 + i)
        write_wav(out / f"{name}.wav", resample(audio, 16000, STORE_RATE), STORE_RATE)
        lines.append(f'[[clip]]\ncase = "{case["name"]}"\nfile = "{name}.wav"\nvoice = "{voice}"\n'
                     f'text = "{case["pilot"]}"\n')
    (out / "clips.toml").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(cases)} clips to {out}")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("make_voice_fixture.py needs macOS (say, afconvert)")
    asyncio.run(departure())
    clips()
