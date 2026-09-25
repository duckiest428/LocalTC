"""Corrections to the sim's airport data, and a guard for the mistakes nobody has written down yet.

The taxiways ATC names come straight from the sim's scenery, and scenery is sometimes wrong. MSFS's Montreal
names two unconnected taxiways "A4": the real one, at the 06R threshold, and the connector from the apron
to 06L's, so a flight to 06L was sent "via A4". Two ways round that:

- **A correction file** (``airport_fixes.toml``: the one shipped here, then the pilot's own in the LocalTC
  data folder, which wins). Per airport, rename a stretch of taxiway, or say which taxiway a runway end's
  holding point is on::

      [CYUL]
      rename = [{ taxiway = "A4", near = "06L", name = "" }]   # the A4 nearest 06L's threshold: unnamed
      hold = { "06L" = "C" }                                  # 06L's holding point is the one on C

  ``near`` picks, of the stretches (connected pieces) with that name, the one closest to that runway end's
  threshold. ``name = ""`` leaves it unnamed: ATC then doesn't say it.

- **The guard.** A numbered taxiway ("A4", "B2": an entry or a connector) exists once at an airport. When the
  data has one name in two unconnected places that lead to holding points of different runways, nobody
  knows which is real, so neither is said: "runway 06L, taxi to the runway" rather than a wrong name.
  Lettered taxiways ("A", "EA") are left alone: a long parallel taxiway really does reach several runways,
  and the sim often breaks it into pieces at intersections.
"""

import logging
import math
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from localtc.sim_api import Airport

log = logging.getLogger(__name__)

SHIPPED = Path(__file__).with_name("airport_fixes.toml")
NUMBERED = re.compile(r"^[A-Z]{1,2}\d+[A-Z]?$")  # A4, B12, DW3: an entry or connector


@dataclass(frozen=True)
class Rename:
    taxiway: str
    near: str  # a runway end ident: of that name's stretches, the one closest to its threshold
    name: str  # the new name; "" leaves it unnamed


@dataclass
class AirportFix:
    renames: list[Rename] = field(default_factory=list)
    holds: dict[str, str] = field(default_factory=dict)  # runway end -> the taxiway its holding point is on


def load(extra: str | Path | None = None) -> dict[str, AirportFix]:
    """The shipped corrections, then ``extra`` (the pilot's own file) over them."""
    fixes: dict[str, AirportFix] = {}
    for path in (SHIPPED, Path(extra) if extra else None):
        if path is None or not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.warning("Couldn't read the airport corrections in %s: %s", path, exc)
            continue
        for icao, entry in data.items():
            if not isinstance(entry, dict):
                continue
            fix = fixes.setdefault(icao.upper(), AirportFix())
            for r in entry.get("rename", []):
                if isinstance(r, dict) and r.get("taxiway") and r.get("near"):
                    fix.renames.append(Rename(str(r["taxiway"]), str(r["near"]).upper(), str(r.get("name", ""))))
            for end, taxiway in (entry.get("hold") or {}).items():
                fix.holds[str(end).upper()] = str(taxiway)
    return fixes


def _stretches(airport: Airport, name: str) -> list[set[int]]:
    """The connected pieces of taxiway called ``name``, as sets of taxi point indexes."""
    adj: dict[int, set[int]] = defaultdict(set)
    for p in airport.taxi_paths:
        if p.kind != "parking" and p.name == name:
            adj[p.start].add(p.end)
            adj[p.end].add(p.start)
    left, pieces = set(adj), []
    while left:
        stack, piece = [left.pop()], set()
        while stack:
            x = stack.pop()
            if x not in piece:
                piece.add(x)
                left.discard(x)
                stack.extend(adj[x])
        pieces.append(piece)
    return pieces


def _renamed(airport: Airport, points: set[int], old: str, new: str) -> Airport:
    paths = tuple(msgspec.structs.replace(p, name=new) if p.name == old and p.kind != "parking" and p.start in points
                  else p for p in airport.taxi_paths)
    return msgspec.structs.replace(airport, taxi_paths=paths)


def apply(airport: Airport, fix: AirportFix | None, hold_runways: dict[int, str], thresholds: dict[str, tuple[float, float]],
          xy) -> Airport:
    """The airport with its corrections made and its ambiguous numbered taxiways unnamed.

    ``hold_runways``: hold-short point index -> its runway's name; ``thresholds``: runway end -> threshold
    position; ``xy(lat, lon)``: the airport's local frame (all from its ``AirportGeometry``)."""
    where = {p.index: xy(p.lat, p.lon) for p in airport.taxi_points}
    for r in fix.renames if fix else ():
        pieces = [s for s in _stretches(airport, r.taxiway) if any(i in where for i in s)]
        threshold = thresholds.get(r.near)
        if not pieces or threshold is None:
            log.info("%s: no taxiway %s near %s to rename", airport.icao, r.taxiway, r.near)
            continue
        piece = min(pieces, key=lambda s: min(math.dist(where[i], threshold) for i in s if i in where))
        airport = _renamed(airport, piece, r.taxiway, r.name)
    names = {p.name for p in airport.taxi_paths if p.name and NUMBERED.match(p.name)}
    for name in sorted(names):
        pieces = _stretches(airport, name)
        runways = [{hold_runways[i] for i in s if i in hold_runways} for s in pieces]
        leading = [r for r in runways if r]
        if len(leading) > 1 and any(a.isdisjoint(b) for a in leading for b in leading if a is not b):
            log.info("%s: taxiway %s is in %d places leading to different runways; not naming it", airport.icao, name, len(pieces))
            for piece in pieces:
                airport = _renamed(airport, piece, name, "")
    return airport


__all__ = ["AirportFix", "Rename", "apply", "load"]
