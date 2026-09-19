"""Rank the speakers of a multi-speaker Piper voice by how well Whisper understands them over the radio.

Each candidate reads a few ATC transmissions; the audio goes through the radio effect and back
through Whisper. The clearest speakers (lowest word error, a brisk but natural pace) are printed
as a tuple for ``localtc.tts.voices.SPEAKERS``.

    python tools/pick_speakers.py [--sample 200] [--keep 40]
"""

import argparse
import sys
import time

import numpy as np

from localtc.dsp.radio import radio_effect, resample
from localtc.stt.whisper import WhisperTranscriber
from localtc.tts.synth import PiperSynth
from localtc.tts.voices import DEFAULT_VOICE, download, speaker_count
from localtc.voice import token_error_rate

LINES = [
    "November one seven two lima tango, climb and maintain five thousand, squawk four five two one.",
    "Skyhawk two lima tango, runway three four left, cleared for takeoff, wind three three zero at eight.",
    "Cessna two lima tango, contact Seattle Approach one one niner point two, good day.",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--sample", type=int, default=200, help="how many speakers to try (evenly spread)")
    parser.add_argument("--keep", type=int, default=40)
    args = parser.parse_args()

    synth = PiperSynth(download(args.voice), rate=1.15)
    whisper = WhisperTranscriber("base.en", device="cpu", compute_type="int8")
    whisper.warm_up()
    count = speaker_count(synth.voice_file)
    candidates = sorted(set(np.linspace(0, count - 1, min(args.sample, count)).astype(int).tolist()))
    results = []
    started = time.monotonic()
    for n, speaker in enumerate(candidates, 1):
        errors, seconds, chars = [], 0.0, 0
        for i, line in enumerate(LINES):
            speech = synth.synthesize(line, speaker)
            radio = resample(radio_effect(speech.audio, speech.rate, seed=speaker * 10 + i), speech.rate, 16000)
            heard = whisper.transcribe(radio).text
            errors.append(token_error_rate(line, heard))
            seconds += speech.seconds
            chars += len(line)
        pace = seconds / chars
        results.append((float(np.mean(errors)), pace, speaker))
        if n % 20 == 0:
            print(f"{n}/{len(candidates)} speakers, {time.monotonic() - started:.0f} s", file=sys.stderr)
    # Clear first; among equally clear, a pace near a busy controller's (about 0.055 s per character).
    results.sort(key=lambda r: (round(r[0], 2), abs(r[1] - 0.055)))
    best = [r for r in results if r[1] < 0.075][: args.keep]
    for wer, pace, speaker in best:
        print(f"speaker {speaker:4d}  error {wer:5.1%}  {pace * 1000:.0f} ms/char", file=sys.stderr)
    print("SPEAKERS: tuple[int, ...] = (" + ", ".join(str(s) for _, _, s in best) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
