"""Build the copilot's list of places (src/localtc/crew/places.tsv.gz): towns and cities with their position, so
"where are we?" is answered from data ("20 miles north-northeast of Charlotte"), never guessed.

    python tools/make_places.py              # download Natural Earth's populated places and rebuild
    python tools/make_places.py --from FILE  # from ne_10m_populated_places_simple.geojson

The source is Natural Earth (naturalearthdata.com), public domain. Written: name, region, country code, latitude,
longitude, population; tab separated, gzipped.
"""

import argparse
import gzip
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "localtc" / "crew" / "places.tsv.gz"
SOURCE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_populated_places_simple.geojson"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path)
    args = parser.parse_args()
    raw = args.source.read_bytes() if args.source else urllib.request.urlopen(SOURCE, timeout=60).read()
    rows = []
    for feature in json.loads(raw)["features"]:
        p = feature["properties"]
        name = (p.get("nameascii") or p.get("name") or "").strip()
        if not name:
            continue
        clean = lambda s: str(s or "").replace("\t", " ").strip()  # noqa: E731
        rows.append(f"{clean(name)}\t{clean(p.get('adm1name'))}\t{clean(p.get('iso_a2'))}\t"
                    f"{p['latitude']:.3f}\t{p['longitude']:.3f}\t{int(p.get('pop_max') or 0)}")
    OUT.write_bytes(gzip.compress(("\n".join(sorted(rows)) + "\n").encode("utf-8"), mtime=0))
    print(f"{OUT}: {len(rows)} places")
    return 0


if __name__ == "__main__":
    sys.exit(main())
