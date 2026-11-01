"""Fetch the cabin's recorded sounds into src/localtc/dsp/sounds/ (dsp/cabin.py plays them):

- chime.ogg: an airliner's cabin chime, the high and the low note ("Airplane Chime Sound Effect", by Sharelk, CC0,
  Wikimedia Commons). Kept as it is.
- boarding.ogg: "Airport Lounge" by Kevin MacLeod (incompetech.com), licensed under Creative Commons: By Attribution
  3.0 (https://creativecommons.org/licenses/by/3.0/), from Wikimedia Commons. Made mono and smaller (Opus, 48 kbit/s).

    python tools/make_cabin_sounds.py
"""

import sys
import urllib.request
from pathlib import Path

import av
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "localtc" / "dsp" / "sounds"
CHIME = "https://upload.wikimedia.org/wikipedia/commons/c/ce/Airplane_Chime_Sound_Effect.ogg"
MUSIC = "https://upload.wikimedia.org/wikipedia/commons/0/05/Airport_Lounge_%28ISRC_USUAN1100806%29.mp3"
RATE = 48000  # (Opus works at 48 kHz)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "LocalTC tools/make_cabin_sounds.py"})
    return urllib.request.urlopen(request, timeout=120).read()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "chime.ogg").write_bytes(fetch(CHIME))
    source = OUT / "music.tmp.mp3"
    source.write_bytes(fetch(MUSIC))
    try:
        with av.open(str(source)) as inp:
            stream = inp.streams.audio[0]
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=RATE)
            with av.open(str(OUT / "boarding.ogg"), "w") as out:
                enc = out.add_stream("libopus", rate=RATE)
                enc.layout = "mono"
                enc.bit_rate = 48000
                for frame in inp.decode(stream):
                    for r in resampler.resample(frame):
                        out.mux(enc.encode(r))
                for r in resampler.resample(None):
                    out.mux(enc.encode(r))
                out.mux(enc.encode(None))
    finally:
        source.unlink(missing_ok=True)
    for name in ("chime.ogg", "boarding.ogg"):
        print(OUT / name, (OUT / name).stat().st_size // 1024, "KB")
    return 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
