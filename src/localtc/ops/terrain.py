"""The ground's height anywhere, for ATC's terrain-safe vectors and altitudes and the minimum safe altitudes shown:
the open Terrain Tiles on AWS (SRTM and other public elevation data, "terrarium" PNGs, no key), kept on this PC.

``Terrain.fetch_around`` downloads the tiles around a point (call it off the event loop); ``elevation_ft`` and
``highest_ft`` only read what's already here, so the ATC can ask them without ever reaching the network. Unknown
ground is None, never a guess.
"""

import logging
import math
import struct
import threading
import urllib.request
import zlib
from pathlib import Path

import numpy as np

from localtc.ops.fetch import USER_AGENT

log = logging.getLogger(__name__)

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ZOOM = 9  # ~300 m a pixel at the equator, 76 km a tile: fine enough for minimum altitudes, few tiles to fetch
M_TO_FT = 3.28084


def decode_png(data: bytes) -> np.ndarray:
    """An 8-bit RGB(A) non-interlaced PNG (what the terrain tiles are) as an array (height, width, channels)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, width = 8, [], 0
    height = depth = colour = 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour not in (2, 6) or interlace:
                raise ValueError("unsupported PNG")
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    channels = 3 if colour == 2 else 4
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    out = np.zeros((height, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.int32)
    for row in range(height):
        f = raw[row * (stride + 1)]
        line = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=row * (stride + 1) + 1).astype(np.int32)
        if f == 0:
            cur = line
        elif f == 1:  # Sub
            cur = line.copy()
            for i in range(channels, stride):
                cur[i] = (cur[i] + cur[i - channels]) & 0xFF
        elif f == 2:  # Up
            cur = (line + prev) & 0xFF
        elif f == 3:  # Average
            cur = line.copy()
            for i in range(stride):
                left = cur[i - channels] if i >= channels else 0
                cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif f == 4:  # Paeth
            cur = line.copy()
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        else:
            raise ValueError(f"PNG filter {f}")
        out[row] = cur
        prev = cur
    return out.reshape(height, width, channels)


def tile_xy(lat: float, lon: float, z: int = ZOOM) -> tuple[float, float]:
    """The tile coordinates (fractional) of a point (Web Mercator)."""
    n = 2 ** z
    lat = max(-85.05, min(85.05, lat))
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


class Terrain:
    def __init__(self, folder: Path | None = None, get=None) -> None:
        self.folder = folder
        self.get = get or self._download
        self._tiles: dict[tuple[int, int, int], np.ndarray | None] = {}
        self._lock = threading.Lock()

    # --- reading (no network) -------------------------------------------------------------------------------

    def _tile(self, z: int, x: int, y: int) -> np.ndarray | None:
        key = (z, x, y)
        with self._lock:
            if key in self._tiles:
                return self._tiles[key]
        heights = None
        path = self._path(z, x, y)
        if path is not None and path.exists():
            try:
                rgb = decode_png(path.read_bytes()).astype(np.float32)
                heights = (rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768) * M_TO_FT
                heights = np.maximum(heights, 0).astype(np.int32)  # the sea is sea level
            except (OSError, ValueError, zlib.error) as exc:
                log.info("Terrain tile %s unreadable: %s", path, exc)
        with self._lock:
            self._tiles[key] = heights
            if len(self._tiles) > 256:
                self._tiles.pop(next(iter(self._tiles)))
        return heights

    def elevation_ft(self, lat: float, lon: float) -> int | None:
        x, y = tile_xy(lat, lon)
        tile = self._tile(ZOOM, int(x), int(y))
        if tile is None:
            return None
        px, py = min(255, int((x % 1) * 256)), min(255, int((y % 1) * 256))
        return int(tile[py, px])

    def highest_ft(self, lat: float, lon: float, radius_nm: float) -> int | None:
        """The highest ground within ``radius_nm`` of a point (None: some of it isn't known here)."""
        dlat = radius_nm / 60.0
        dlon = radius_nm / 60.0 / max(0.1, math.cos(math.radians(lat)))
        x0, y0 = tile_xy(lat + dlat, lon - dlon)
        x1, y1 = tile_xy(lat - dlat, lon + dlon)
        cx, cy = tile_xy(lat, lon)
        scale_px = 256 * (2 ** ZOOM) / 360.0 * dlon  # pixels for dlon at this latitude
        best = None
        for tx in range(int(x0), int(x1) + 1):
            for ty in range(int(y0), int(y1) + 1):
                tile = self._tile(ZOOM, tx, ty)
                if tile is None:
                    return None
                gx = (tx + (np.arange(256) + 0.5) / 256 - cx) * 256
                gy = (ty + (np.arange(256) + 0.5) / 256 - cy) * 256
                mask = (gx[None, :] ** 2 + gy[:, None] ** 2) <= scale_px ** 2
                if mask.any():
                    top = int(tile[mask].max())
                    best = top if best is None else max(best, top)
        return best

    # --- fetching (blocking) --------------------------------------------------------------------------------

    def fetch_around(self, lat: float, lon: float, radius_nm: float = 30.0) -> int:
        """Download the tiles within ``radius_nm`` not here yet; how many were fetched."""
        dlat = radius_nm / 60.0
        dlon = radius_nm / 60.0 / max(0.1, math.cos(math.radians(lat)))
        x0, y0 = tile_xy(lat + dlat, lon - dlon)
        x1, y1 = tile_xy(lat - dlat, lon + dlon)
        n = 0
        for tx in range(int(x0), int(x1) + 1):
            for ty in range(int(y0), int(y1) + 1):
                path = self._path(ZOOM, tx, ty)
                if path is None or path.exists():
                    continue
                try:
                    data = self.get(TILE_URL.format(z=ZOOM, x=tx, y=ty))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    with self._lock:
                        self._tiles.pop((ZOOM, tx, ty), None)
                    n += 1
                except OSError as exc:
                    log.info("Terrain tile %d/%d/%d not fetched: %s", ZOOM, tx, ty, exc)
                    return n
        return n

    def _path(self, z: int, x: int, y: int) -> Path | None:
        return self.folder / str(z) / str(x) / f"{y}.png" if self.folder is not None else None

    @staticmethod
    def _download(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
