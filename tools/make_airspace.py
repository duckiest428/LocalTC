"""Build LocalTC's airspace data: the enroute centres (FIRs, ARTCCs) and approach areas (TRACONs) that ATC
hands flights between, and the Live Map draws.

    python tools/make_airspace.py            # download the sources and rebuild
    python tools/make_airspace.py --from DIR # use Boundaries.geojson, VATSpy.dat and TRACONBoundaries.geojson in DIR

The sources are the VATSpy Data Project (FIR boundaries and their names) and the SimAware TRACON
Project (approach areas), both by the VATSIM community and both CC BY-SA 4.0. The files written here
are an adaptation of them -- simplified outlines, rounded coordinates, names joined on -- and are
under the same licence: see src/localtc/atc_core/airspace/README.md.
"""

import argparse
import gzip
import json
import math
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "localtc" / "atc_core" / "airspace"

SOURCES = {
    "Boundaries.geojson": "https://raw.githubusercontent.com/vatsimnetwork/vatspy-data-project/master/Boundaries.geojson",
    "VATSpy.dat": "https://raw.githubusercontent.com/vatsimnetwork/vatspy-data-project/master/VATSpy.dat",
    "TRACONBoundaries.geojson":
        "https://github.com/vatsimnetwork/simaware-tracon-project/releases/latest/download/TRACONBoundaries.geojson",
}

# How far an outline may be moved to drop points, in degrees: about a mile for a centre, a third of one for an
# approach area. Handoffs happen at these lines, and neither is drawn finer than a pilot could tell.
CENTER_TOLERANCE, APPROACH_TOLERANCE = 0.015, 0.005
DECIMALS = 4  # about 11 m


def fetch(folder: Path | None) -> dict[str, bytes]:
    out = {}
    for name, url in SOURCES.items():
        if folder is not None:
            out[name] = (folder / name).read_bytes()
        else:
            print(f"downloading {url}")
            with urllib.request.urlopen(url, timeout=60) as response:
                out[name] = response.read()
    return out


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iteratively (outlines run to thousands of points).

    An outline is a closed ring, first point and last the same, and measured against a line of no length
    every point is on it. So a ring is split at its point farthest from the start and the two halves
    simplified on their own."""
    if len(points) < 4:
        return points
    if points[0] == points[-1]:
        far = max(range(1, len(points) - 1), key=lambda i: math.dist(points[0], points[i]))
        return simplify(points[: far + 1], tolerance)[:-1] + simplify(points[far:], tolerance)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = points[first], points[last]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        worst, index = 0.0, -1
        for i in range(first + 1, last):
            x, y = points[i]
            distance = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
            if distance > worst:
                worst, index = distance, i
        if worst > tolerance and index > 0:
            keep[index] = True
            stack += [(first, index), (index, last)]
    return [p for p, k in zip(points, keep) if k]


def rings(geometry: dict, tolerance: float) -> list[list[list[float]]]:
    """Outer rings only, as [lat, lon] pairs. Holes are dropped: none of them are airspace anyone flies through
    on a different frequency."""
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    out = []
    for polygon in polygons:
        outer = [(float(lon), float(lat)) for lon, lat, *_ in polygon[0]]
        small = simplify(outer, tolerance)
        if len(small) >= 4:
            out.append([[round(lat, DECIMALS), round(lon, DECIMALS)] for lon, lat in small])
    return out


def label_of(props: dict, area_rings: list) -> list[float]:
    if props.get("label_lat") not in (None, "") and props.get("label_lon") not in (None, ""):
        return [round(float(props["label_lat"]), 3), round(float(props["label_lon"]), 3)]
    biggest = max(area_rings, key=len)
    return [round(sum(p[0] for p in biggest) / len(biggest), 3), round(sum(p[1] for p in biggest) / len(biggest), 3)]


def sections(dat: str) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    current = None
    for line in dat.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = out.setdefault(line[1:-1], [])
        elif current is not None:
            current.append(line.split("|"))
    return out


def clean_place(place: str) -> str:
    """ "London TMA (Up to FL195) - London" is worked by London; "Roma NE - Roma" by Roma. The name a pilot
    calls is the unit after the dash, without the notes in brackets."""
    import re

    place = place.split(" - ")[-1]
    words = re.sub(r"\([^)]*\)", "", place).split()
    # "Brisbane Radio 123.2*": a sector named by its frequency. The place is what a pilot calls.
    without = [w for w in words if not re.fullmatch(r"\d+(\.\d+)?\*?", w)]
    if len(without) < len(words):
        without = [w for w in without if w.lower() != "radio"]
    return " ".join(without) or place


def facility_word(icao: str, countries: dict[str, tuple[str, str]]) -> str:
    """What the centre is called after its place: Center (US), Centre (Canada), else the country's own word."""
    country, suffix = countries.get(icao[:2], ("", ""))
    if suffix:
        return suffix
    return {"USA": "Center", "Canada": "Centre"}.get(country, "Control")


def centers(boundaries: dict, dat: str) -> list[dict]:
    tables = sections(dat)
    countries = {row[1]: (row[0], row[2] if len(row) > 2 else "") for row in tables["Countries"] if len(row) > 1}
    names: dict[str, tuple[str, str]] = {}  # boundary id -> (icao, name); the first line for an id wins
    for row in tables["FIRs"]:
        if len(row) >= 4 and row[3] and row[3] not in names:
            names[row[3]] = (row[0], row[1])
    merged: dict[str, dict] = {}
    whole = {f["properties"]["id"] for f in boundaries["features"]}
    for feature in boundaries["features"]:
        props = feature["properties"]
        ident = props["id"]
        if "-" in ident and ident.split("-")[0] in whole:
            continue  # a sector of a centre that is in the data whole: the whole one is what's handed to
        icao, place = names.get(ident, (ident, ident))
        place = clean_place(place)
        if "military" in place.lower():
            continue  # military areas overlap the civil ones; an IFR flight talks to the civil centre
        oceanic = props.get("oceanic") == "1"
        if "oceanic" in place.lower():
            name = place  # "Shanwick Oceanic" is the whole name
        elif oceanic:
            name = f"{place} Oceanic"
        else:
            name = f"{place} {facility_word(icao, countries)}"
        area_rings = rings(feature["geometry"], CENTER_TOLERANCE)
        if not area_rings:
            continue
        if ident in merged:  # one centre drawn as more than one feature
            merged[ident]["rings"] += area_rings
            continue
        merged[ident] = {"id": ident, "name": name, "kind": "center", "oceanic": oceanic,
                         "label": label_of(props, area_rings), "rings": area_rings}
    return sorted(merged.values(), key=lambda a: a["id"])


def approaches(tracons: dict) -> list[dict]:
    out = []
    for n, feature in enumerate(tracons["features"]):
        props = feature["properties"]
        area_rings = rings(feature["geometry"], APPROACH_TOLERANCE)
        if not area_rings:
            continue
        role = "departure" if str(props.get("suffix", "")).upper() == "DEP" else "approach"
        out.append({"id": f"{props['id']}:{n}", "name": props.get("name") or props["id"], "kind": role,
                    "airports": [str(p).upper() for p in props.get("prefix", [])],
                    "label": label_of(props, area_rings), "rings": area_rings})
    return out


def write(path: Path, areas: list[dict]) -> None:
    body = json.dumps({"areas": areas}, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))  # mtime 0: rebuilding the same data changes nothing
    print(f"{path.relative_to(ROOT)}: {len(areas)} areas, {len(body) // 1024} KB, {path.stat().st_size // 1024} KB gzipped")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="folder", type=Path, help="folder with the three source files")
    args = parser.parse_args()
    raw = fetch(args.folder)
    write(OUT / "centers.json.gz", centers(json.loads(raw["Boundaries.geojson"]), raw["VATSpy.dat"].decode("utf-8", "replace")))
    write(OUT / "approaches.json.gz", approaches(json.loads(raw["TRACONBoundaries.geojson"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
