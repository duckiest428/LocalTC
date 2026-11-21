"""Build the world's radio navaids (src/localtc/atc_core/navaids.tsv.gz): the VORs and NDBs ATC can hold a flight
over or send it direct to by name ("request holding over Pisa VOR"), with no flight plan to find them in.

    python tools/make_navaids.py              # download OurAirports' navaids.csv and rebuild
    python tools/make_navaids.py --from FILE  # from a downloaded navaids.csv

The source is OurAirports (ourairports.com), public domain. Kept: VOR, VOR-DME, VORTAC, TACAN and NDB (and NDB-DME).
Written: ident, kind (VOR or NDB), latitude, longitude, name, country; tab separated, gzipped.
"""

import argparse
import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "localtc" / "atc_core" / "navaids.tsv.gz"
SOURCE = "https://davidmegginson.github.io/ourairports-data/navaids.csv"
KINDS = {"VOR": "VOR", "VOR-DME": "VOR", "VORTAC": "VOR", "TACAN": "VOR", "NDB": "NDB", "NDB-DME": "NDB"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path)
    args = parser.parse_args()
    raw = args.source.read_bytes() if args.source else urllib.request.urlopen(SOURCE, timeout=120).read()
    rows = set()
    clean = lambda s: str(s or "").replace("\t", " ").strip()  # noqa: E731
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        kind = KINDS.get(r["type"])
        ident = clean(r["ident"]).upper()
        if kind is None or not ident.isalnum() or not r["latitude_deg"] or not r["longitude_deg"]:
            continue
        rows.add(f"{ident}\t{kind}\t{float(r['latitude_deg']):.4f}\t{float(r['longitude_deg']):.4f}\t{clean(r['name'])}\t"
                 f"{clean(r['iso_country'])}")
    OUT.write_bytes(gzip.compress(("\n".join(sorted(rows)) + "\n").encode("utf-8"), mtime=0))
    print(f"{OUT}: {len(rows)} navaids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
