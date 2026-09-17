"""Local airport data cache: one JSON file per ICAO.

App wiring writes every ``AirportData`` event here, so airports fetched once
from the sim are available offline (replay, tests, next session).
"""

import os
import sys
from pathlib import Path

import msgspec

from localtc.sim_api import Airport

_encoder = msgspec.json.Encoder()
_decoder = msgspec.json.Decoder(Airport)


def default_cache_dir() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "LocalTC" / "airports"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "localtc" / "airports"


def load_airport(path: str | Path) -> Airport:
    airport = _decoder.decode(Path(path).read_bytes())
    if abs(airport.magvar) > 180:  # cached before magvar normalization (the sim's 0-360, east negative)
        airport = msgspec.structs.replace(airport, magvar=round(-(((airport.magvar + 180) % 360) - 180), 1) + 0.0)
    return airport


def dump_airport(airport: Airport, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(msgspec.json.format(_encoder.encode(airport), indent=1))
    tmp.replace(path)


def load_airport_dir(directory: str | Path) -> list[Airport]:
    return [load_airport(path) for path in sorted(Path(directory).glob("*.json"))]


class AirportCache:
    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else default_cache_dir()

    def path_for(self, icao: str) -> Path:
        return self.directory / f"{icao.upper()}.json"

    def get(self, icao: str) -> Airport | None:
        path = self.path_for(icao)
        if not path.is_file():
            return None
        try:
            return load_airport(path)
        except (msgspec.DecodeError, msgspec.ValidationError):
            return None  # stale or corrupt; the sim will be asked again

    def put(self, airport: Airport) -> Path:
        path = self.path_for(airport.icao)
        dump_airport(airport, path)
        return path


__all__ = ["AirportCache", "default_cache_dir", "dump_airport", "load_airport", "load_airport_dir"]
