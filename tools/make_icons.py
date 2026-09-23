"""Render the LocalTC mark to the raster sizes Windows and the web need.

The logo is `src/localtc/ui/static/logo.svg`; everything here is generated from the same path data,
so the .ico and the PNGs can never drift from it. No image library is involved: the shapes are a
rounded rectangle and one closed outline, which a scanline fill with 4x supersampling handles in a
page of code, and PNG and ICO are both simple enough containers to write directly.

    python tools/make_icons.py

Writes install/localtc.ico (the Windows shortcut icon), the companion app's iOS icon, site/icon-180.png
(apple-touch-icon) and
site/og.png (the link preview card).
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TILE = "#0a0e0c"     # the rounded square behind the aircraft
MARK = "#f5f7f6"     # the aircraft itself
RADIUS = 56 / 256    # the tile's corner radius, as a fraction of its side

# The aircraft, in the logo's own 256-unit space with the origin moved to the centre, exactly as
# logo.svg draws it. Curves are given as cubic control points and flattened below.
NOSE_LEFT = (0, -70, -5, -70, -8, -64, -8, -56)
NOSE_RIGHT = (0, -70, 5, -70, 8, -64, 8, -56)
TAIL_RIGHT = (4, 54, 4, 60, 2, 64, 0, 66)
TAIL_LEFT = (0, 66, -2, 64, -4, 60, -4, 54)
STRAIGHT_RIGHT = [(8, -22), (74, 8), (74, 18), (8, 2), (8, 28), (26, 40), (26, 48), (4, 42), (4, 54)]
STRAIGHT_LEFT = [(-4, 42), (-26, 48), (-26, 40), (-8, 28), (-8, 2), (-74, 18), (-74, 8), (-8, -22)]


def cubic(p: tuple[float, ...], steps: int = 12) -> list[tuple[float, float]]:
    """A cubic Bezier as points, the start excluded so segments join without duplicates."""
    x0, y0, x1, y1, x2, y2, x3, y3 = p
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        out.append((u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
                    u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3))
    return out


def aircraft(size: int) -> list[tuple[float, float]]:
    """The outline, scaled to an image `size` pixels square and centred in it."""
    points = [(0.0, -70.0)]
    points += cubic(NOSE_RIGHT)
    points += [(float(x), float(y)) for x, y in STRAIGHT_RIGHT]
    points += cubic(TAIL_RIGHT)
    points += cubic(TAIL_LEFT)
    points += [(float(x), float(y)) for x, y in STRAIGHT_LEFT]
    points += cubic(NOSE_LEFT)[::-1]
    k = size / 256
    return [(x * k + size / 2, y * k + size / 2) for x, y in points]


def rounded_rect(size: int) -> list[tuple[float, float]]:
    r = RADIUS * size
    pts: list[tuple[float, float]] = []
    corners = [(size - r, size - r, 0), (r, size - r, 90), (r, r, 180), (size - r, r, 270)]
    for cx, cy, start in corners:
        for i in range(13):
            a = math.radians(start + i * 90 / 12)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def coverage(polygon: list[tuple[float, float]], size: int, ss: int = 4) -> list[float]:
    """Antialiased fill: scanline crossings at `ss` subsamples per pixel, averaged back down."""
    acc = [0.0] * (size * size)
    edges = [(polygon[i], polygon[(i + 1) % len(polygon)]) for i in range(len(polygon))]
    edges = [((x0, y0), (x1, y1)) for (x0, y0), (x1, y1) in edges if y0 != y1]
    weight = 1.0 / (ss * ss)
    for sy in range(size * ss):
        y = (sy + 0.5) / ss
        xs = []
        for (x0, y0), (x1, y1) in edges:
            if (y0 <= y < y1) or (y1 <= y < y0):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        if not xs:
            continue
        xs.sort()
        row = (sy // ss) * size
        for i in range(0, len(xs) - 1, 2):
            left, right = xs[i] * ss, xs[i + 1] * ss
            for sx in range(max(0, int(left)), min(size * ss, int(right) + 1)):
                # partial coverage where the span starts or ends inside this subpixel
                lit = min(sx + 1.0, right) - max(float(sx), left)
                if lit > 0:
                    px = sx // ss
                    if 0 <= px < size:
                        acc[row + px] += lit * weight
    return [min(1.0, v) for v in acc]


def rgb(colour: str) -> tuple[int, int, int]:
    c = colour.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def render(size: int, *, square: bool = False) -> bytes:
    """The icon at `size`, as raw RGBA rows. `square`: full-bleed, for iOS, which rounds the corners itself."""
    tile, mark = rgb(TILE), rgb(MARK)
    back = [1.0] * (size * size) if square else coverage(rounded_rect(size), size)
    plane = coverage(aircraft(size), size)
    out = bytearray()
    for i in range(size * size):
        a, p = back[i], plane[i]
        # the aircraft over the tile, both premultiplied into straight RGBA
        alpha = a
        if alpha <= 0:
            out += b"\0\0\0\0"
            continue
        px = [round(tile[c] * (1 - p) + mark[c] * p) for c in range(3)]
        out += bytes(px) + bytes([round(alpha * 255)])
    return bytes(out)


def png(pixels: bytes, width: int, height: int | None = None) -> bytes:
    height = width if height is None else height
    raw = b"".join(b"\0" + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico(images: dict[int, bytes]) -> bytes:
    """A PNG-compressed .ico, which every Windows since Vista reads."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = len(head) + 16 * len(images)
    entries, blobs = b"", b""
    for size in sorted(images):
        data = images[size]
        entries += struct.pack("<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0,
                               0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def og_card() -> bytes:
    """1200x630 link preview: the mark alone, centred on the site's near-black."""
    w, h, mark_size = 1200, 630, 280
    tile = rgb("#0e1013")
    canvas = bytearray()
    for _ in range(w * h):
        canvas += bytes(tile) + b"\xff"
    icon = render(mark_size)
    ox, oy = (w - mark_size) // 2, (h - mark_size) // 2
    for y in range(mark_size):
        for x in range(mark_size):
            s = (y * mark_size + x) * 4
            a = icon[s + 3] / 255
            if a <= 0:
                continue
            d = ((oy + y) * w + ox + x) * 4
            for c in range(3):
                canvas[d + c] = round(canvas[d + c] * (1 - a) + icon[s + c] * a)
    return png(bytes(canvas), w, h)


def main() -> None:
    sizes = (16, 32, 48, 64, 128, 256)
    images = {}
    for size in sizes:
        images[size] = png(render(size), size)
        print(f"  {size}x{size}")
    (ROOT / "install" / "localtc.ico").write_bytes(ico(images))
    print("install/localtc.ico")
    (ROOT / "site" / "icon-180.png").write_bytes(png(render(180), 180))
    print("site/icon-180.png")
    (ROOT / "site" / "og.png").write_bytes(og_card())
    print("site/og.png")
    icon = ROOT / "ios" / "LocalTC Companion" / "Assets.xcassets" / "AppIcon.appiconset" / "icon-1024.png"
    icon.write_bytes(png(render(1024, square=True), 1024))
    print(icon.relative_to(ROOT))


if __name__ == "__main__":
    main()
