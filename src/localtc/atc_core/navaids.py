"""The world's VORs and NDBs (OurAirports, public domain; ``tools/make_navaids.py``), for a fix the pilot names that
isn't on any route of theirs: "request holding over Pisa VOR" on a flight with no plan, "direct Boston VOR". Found by
their ident ("PIS") or their name ("Pisa"), only near the aircraft, the nearer first."""

import gzip
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("navaids.tsv.gz")
NEAR_NM = 150.0  # a navaid further than this isn't the one meant
KIND_WORDS = {"vor": "VOR", "vortac": "VOR", "tacan": "VOR", "dme": "", "vordme": "VOR", "ndb": "NDB", "beacon": "NDB",
              "locator": "NDB"}


@dataclass(frozen=True)
class Navaid:
    ident: str
    kind: str  # VOR (with VOR-DME, VORTAC, TACAN) or NDB
    lat: float
    lon: float
    name: str
    country: str

    @property
    def said(self) -> str:
        """How ATC names it: "Pisa VOR"."""
        return f"{self.name} {self.kind}" if self.name else self.ident


@lru_cache(maxsize=1)
def navaids() -> tuple[Navaid, ...]:
    out = []
    for line in gzip.decompress(DATA.read_bytes()).decode("utf-8").splitlines():
        ident, kind, lat, lon, name, country = (line.split("\t") + [""] * 6)[:6]
        out.append(Navaid(ident, kind, float(lat), float(lon), name, country))
    return tuple(out)


def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3440.065 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def find(said: str, lat: float, lon: float, max_nm: float = NEAR_NM) -> Navaid | None:
    """The navaid ``said`` names ("Pisa VOR", "PIS", "Pisa"), within ``max_nm`` of the aircraft, the nearest; a VOR
    before an NDB of the same name unless the NDB was said. None: no navaid of that name near."""
    words = _plain(said).split()
    kind = next((KIND_WORDS[w] for w in reversed(words) if KIND_WORDS.get(w)), "")
    name = " ".join(w for w in words if w not in KIND_WORDS)
    if not name:
        return None
    found = []
    for n in navaids():
        if n.ident.lower() != name.replace(" ", "") and _plain(n.name) != name:
            continue
        if kind and n.kind != kind:
            continue
        d = _nm(lat, lon, n.lat, n.lon)
        if d <= max_nm:
            found.append((n.kind != "VOR", d, n))
    return min(found, key=lambda x: (x[0], x[1]))[2] if found else None
