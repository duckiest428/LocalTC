"""Shrink a recorded flight into a test fixture: own-aircraft state at 1 Hz, traffic every 10 s, the
airports that have frequencies, and every radio, ATC and model event. Gzipped.

    python tools/thin_recording.py recordings/<session> tests/fixtures/real_<name>

Flights sent in from the app's developer mode (``LocalTC-report-*.zip``: unzip it first, the recording is
in ``recording/``) become regression tests this way.
"""

import gzip
import json
import sys
from pathlib import Path


def thin(source: Path, target: Path, *, own_every_s: float = 1.0, traffic_every_s: float = 10.0) -> tuple[int, int]:
    src = source / "session.jsonl" if source.is_dir() else source
    opener = gzip.open if src.suffix == ".gz" else open
    target.mkdir(parents=True, exist_ok=True)
    kept = total = 0
    last: dict[str, float] = {}
    with opener(src, "rt", encoding="utf-8") as fin, gzip.open(target / "session.jsonl.gz", "wt", encoding="utf-8") as fout:
        for line in fin:
            total += 1
            row = json.loads(line)
            kind, t = row.get("type"), row.get("t", 0.0)
            every = {"ownship_state": own_every_s, "traffic_snapshot": traffic_every_s}.get(kind)
            if every is not None:
                if t - last.get(kind, -1e9) < every:
                    continue
                last[kind] = t
            if kind == "airport_data" and not row["airport"].get("frequencies"):
                continue  # helipads and hospitals the nearest-airport scan passed over
            if kind == "ptt_released":
                row.pop("audio_ref", None)  # the WAVs aren't copied
            if kind == "transcript":
                row.pop("audio_ref", None)
            fout.write(json.dumps(row, separators=(",", ":")) + "\n")
            kept += 1
    return kept, total


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    kept, total = thin(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"kept {kept} of {total} events")
