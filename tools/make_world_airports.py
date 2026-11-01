"""Build the Live Map's world airports (src/localtc/airports/world.tsv.gz): the airports with airline service, their
codes and size, so the map shows the airports around you (before a flight too) without asking the sim.

    python tools/make_world_airports.py              # download OurAirports' airports.csv and rebuild
    python tools/make_world_airports.py --from FILE  # from a downloaded airports.csv

The source is OurAirports (ourairports.com), public domain. Kept: the large and medium airports with scheduled
service or an IATA code. Written: ICAO, IATA, size (L large, M medium), latitude, longitude, name, city, country
code; tab separated, gzipped.
"""

import argparse
import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "localtc" / "airports" / "world.tsv.gz"
SOURCE = "https://davidmegginson.github.io/ourairports-data/airports.csv"
SIZES = {"large_airport": "L", "medium_airport": "M"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path)
    args = parser.parse_args()
    raw = args.source.read_bytes() if args.source else urllib.request.urlopen(SOURCE, timeout=120).read()
    rows = []
    clean = lambda s: str(s or "").replace("\t", " ").strip()  # noqa: E731
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        size = SIZES.get(r["type"])
        icao = clean(r.get("icao_code") or r.get("gps_code") or r["ident"]).upper()
        iata = clean(r.get("iata_code")).upper()
        if size is None or not (r.get("scheduled_service") == "yes" or iata) or len(icao) != 4 or not icao.isalnum():
            continue
        rows.append(f"{icao}\t{iata}\t{size}\t{float(r['latitude_deg']):.4f}\t{float(r['longitude_deg']):.4f}\t"
                    f"{clean(r['name'])}\t{clean(r.get('municipality'))}\t{clean(r['iso_country'])}")
    OUT.write_bytes(gzip.compress(("\n".join(sorted(rows)) + "\n").encode("utf-8"), mtime=0))
    print(f"{OUT}: {len(rows)} airports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
