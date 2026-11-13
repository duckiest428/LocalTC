"""Fetch the cabin's recorded sounds into src/localtc/dsp/sounds/ (dsp/cabin.py plays them):

- chime.ogg: an airliner's cabin chime, the high and the low note ("Airplane Chime Sound Effect", by Sharelk, CC0,
  Wikimedia Commons). Kept as it is.
- boarding*.ogg: the boarding music, a different track each flight. All by Kevin MacLeod (incompetech.com), licensed
  under Creative Commons: By Attribution 3.0 (https://creativecommons.org/licenses/by/3.0/), from Wikimedia Commons:
  "Airport Lounge" (boarding.ogg), "Lobby Time", "Bossa Antigua", "Smooth Lovin" and
  "Easy Lemon". Made mono and smaller (Opus: 48 kbit/s for the first, 32 for the rest; the cabin's speakers don't need more).

    python tools/make_cabin_sounds.py            # everything
    python tools/make_cabin_sounds.py --music    # the music only (the tracks not made yet; --force for all)
"""

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import av
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "localtc" / "dsp" / "sounds"
CHIME = "https://upload.wikimedia.org/wikipedia/commons/c/ce/Airplane_Chime_Sound_Effect.ogg"
COMMONS = "https://upload.wikimedia.org/wikipedia/commons/"
# (file, source on Commons, Opus bit rate)
MUSIC = (
    ("boarding.ogg", COMMONS + "0/05/Airport_Lounge_%28ISRC_USUAN1100806%29.mp3", 48000),
    ("boarding-lobby-time.ogg", COMMONS + "e/e9/Lobby_Time_%28ISRC_USUAN1600054%29.mp3", 32000),
    ("boarding-bossa-antigua.ogg", COMMONS + "6/60/Bossa_Antigua_%28ISRC_USUAN1700069%29.mp3", 32000),
    ("boarding-smooth-lovin.ogg", COMMONS + "9/99/Smooth_Lovin_%28ISRC_USUAN1700077%29.mp3", 32000),
    ("boarding-easy-lemon.ogg", COMMONS + "0/0c/Easy_Lemon_%28ISRC_USUAN1200076%29.mp3", 32000),
)
RATE = 48000  # (Opus works at 48 kHz)


def fetch(url: str, tries: int = 6) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "LocalTC-tools/1.0 (make_cabin_sounds.py)"})
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(request, timeout=120).read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == tries - 1:
                raise
            wait = int(exc.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
            print(f"Commons says too many requests: trying again in {wait} s", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def opus(source: Path, target: Path, bit_rate: int) -> None:
    with av.open(str(source)) as inp:
        stream = inp.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=RATE)
        with av.open(str(target), "w") as out:
            enc = out.add_stream("libopus", rate=RATE)
            enc.layout = "mono"
            enc.bit_rate = bit_rate
            for frame in inp.decode(stream):
                for r in resampler.resample(frame):
                    out.mux(enc.encode(r))
            for r in resampler.resample(None):
                out.mux(enc.encode(r))
            out.mux(enc.encode(None))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    names = []
    if "--music" not in sys.argv:
        (OUT / "chime.ogg").write_bytes(fetch(CHIME))
        names.append("chime.ogg")
    source = OUT / "music.tmp.mp3"
    try:
        for name, url, bit_rate in MUSIC:
            if (OUT / name).exists() and "--force" not in sys.argv:
                continue  # made already (--force: again)
            source.write_bytes(fetch(url))
            opus(source, OUT / name, bit_rate)
            names.append(name)
            time.sleep(2)  # (Commons turns away a quick run of requests)
    finally:
        source.unlink(missing_ok=True)
    for name in names:
        print(OUT / name, (OUT / name).stat().st_size // 1024, "KB")
    return 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
