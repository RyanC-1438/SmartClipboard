"""Generate the SmartClipboard .ico files in pure Python (no third-party libs).

Run from the project root:

    .venv\\Scripts\\python.exe tools\\make_icons.py

Writes assets/smartclipboard.ico (normal) and assets/smartclipboard_paused.ico
(greyed). Each .ico holds 32-bpp BMP images (BGRA + 1-bpp AND mask) at
16, 20, 24, 32, 40, 48, 64 and 256 px.

The glyph is a clipboard: a rounded board, a clip at the top and three text
lines on a sheet of paper. It is laid out on a 16-unit grid and re-drawn at
every size with its edges snapped to whole pixels, then anti-aliased with
4x4 supersampling, so the 16 px image stays crisp instead of being a blurry
shrink of the 256 px one.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

SIZES = (16, 20, 24, 32, 40, 48, 64, 256)

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]

# Calm blue/teal palette.
NORMAL = {
    "board_top": (41, 160, 196),
    "board_bottom": (26, 120, 165),
    "edge": (20, 92, 132),
    "paper": (250, 252, 253),
    "line": (58, 122, 156),
    "clip": (55, 71, 90),
}


def _grey(c: RGB) -> RGB:
    """Greyed-out version of a colour (luminance, lifted a little)."""
    lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    g = max(0, min(255, round(lum * 0.75 + 60)))
    return (g, g, g)


PAUSED = {name: _grey(c) for name, c in NORMAL.items()}


class RoundRect:
    """Axis-aligned rounded rectangle in pixel space: [x0, x1) x [y0, y1)."""

    def __init__(self, x0: float, y0: float, x1: float, y1: float, r: float,
                 color: Optional[Callable[[float, float], RGB]]) -> None:
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
        self.color = color  # None means "punch a transparent hole"

    def contains(self, x: float, y: float) -> bool:
        if x < self.x0 or x >= self.x1 or y < self.y0 or y >= self.y1:
            return False
        r = self.r
        if r <= 0:
            return True
        dx = max(self.x0 + r - x, 0.0, x - (self.x1 - r))
        dy = max(self.y0 + r - y, 0.0, y - (self.y1 - r))
        return dx * dx + dy * dy <= r * r


def _solid(c: RGB) -> Callable[[float, float], RGB]:
    return lambda _x, _y: c


def _layout(size: int, pal: dict) -> List[RoundRect]:
    """The glyph's shapes for one size, topmost first."""
    k = size / 16.0

    def p(v: float) -> int:  # snap a 16-grid coordinate to a whole pixel
        return int(round(v * k))

    shapes: List[RoundRect] = []

    # Clip: a hole in the tab (only where there is room), the tab, the base.
    if size >= 32:
        shapes.append(RoundRect(7.0 * k, 0.75 * k, 9.0 * k, 1.75 * k, 0.5 * k, None))
    shapes.append(RoundRect(p(6), p(0), p(10), p(2.5), 1.0 * k, _solid(pal["clip"])))
    shapes.append(RoundRect(p(5), p(1.5), p(11), p(4), 0.75 * k, _solid(pal["clip"])))

    # Text lines on the paper. Thickness is at least one pixel.
    t = max(1, p(1))
    for row, right in ((6, 11), (8, 11), (10, 9)):
        y = p(row)
        shapes.append(RoundRect(p(5), y, p(right), y + t, 0.25 * k if size >= 32 else 0,
                                _solid(pal["line"])))

    # Paper sheet.
    shapes.append(RoundRect(p(4), p(4), p(12), p(13), 0.5 * k, _solid(pal["paper"])))

    # Board body with a vertical gradient, inside a darker edge on bigger sizes.
    bx0, by0, bx1, by1 = p(2), p(2), p(14), p(15)
    top, bottom = pal["board_top"], pal["board_bottom"]

    def gradient(_x: float, y: float) -> RGB:
        f = min(1.0, max(0.0, (y - by0) / max(1, by1 - by0)))
        return tuple(round(a + (b - a) * f) for a, b in zip(top, bottom))  # type: ignore

    edge = 0 if size < 20 else max(1, int(round(0.5 * k)))
    radius = 2.0 * k
    if edge:
        shapes.append(RoundRect(bx0 + edge, by0 + edge, bx1 - edge, by1 - edge,
                                radius - edge, gradient))
        shapes.append(RoundRect(bx0, by0, bx1, by1, radius, _solid(pal["edge"])))
    else:
        shapes.append(RoundRect(bx0, by0, bx1, by1, radius, gradient))
    return shapes


def render(size: int, pal: dict, ss: int = 4) -> List[List[RGBA]]:
    """Render the glyph at ``size`` px; returns rows (top first) of RGBA."""
    shapes = _layout(size, pal)
    offsets = [(i + 0.5) / ss for i in range(ss)]
    n = ss * ss
    rows: List[List[RGBA]] = []
    for py in range(size):
        row: List[RGBA] = []
        for px in range(size):
            r = g = b = a = 0
            for oy in offsets:
                y = py + oy
                for ox in offsets:
                    x = px + ox
                    for s in shapes:
                        if s.contains(x, y):
                            if s.color is not None:
                                c = s.color(x, y)
                                r += c[0]
                                g += c[1]
                                b += c[2]
                                a += 1
                            break
            if a == 0:
                row.append((0, 0, 0, 0))
            else:
                # Opaque shapes only, so the average of the hit samples is the
                # straight (non-premultiplied) colour and coverage is alpha.
                row.append((round(r / a), round(g / a), round(b / a), round(255 * a / n)))
        rows.append(row)
    return rows


def _bmp_entry(rows: Sequence[Sequence[RGBA]]) -> bytes:
    """One ICO image: BITMAPINFOHEADER + bottom-up BGRA + 1-bpp AND mask."""
    h = len(rows)
    w = len(rows[0])
    xor = bytearray()
    for row in reversed(rows):  # bottom-up
        for r, g, b, a in row:
            xor += bytes((b, g, r, a))
    mask_stride = ((w + 31) // 32) * 4
    mask = bytearray()
    for row in reversed(rows):
        line = bytearray(mask_stride)
        for x, (_r, _g, _b, a) in enumerate(row):
            if a == 0:  # 1 = transparent
                line[x // 8] |= 0x80 >> (x % 8)
        mask += line
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        w,           # biWidth
        h * 2,       # biHeight: XOR + AND images stacked
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression = BI_RGB
        len(xor) + len(mask),  # biSizeImage
        0, 0, 0, 0,  # resolution, colours used/important
    )
    return header + bytes(xor) + bytes(mask)


def build_ico(images: Sequence[Tuple[int, bytes]]) -> bytes:
    """ICONDIR + ICONDIRENTRY[] + image data. ``images`` is (size, bmp)."""
    count = len(images)
    out = bytearray(struct.pack("<HHH", 0, 1, count))
    offset = 6 + 16 * count
    for size, data in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the directory
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _size, data in images:
        out += data
    return bytes(out)


def make_ico(pal: dict) -> bytes:
    return build_ico([(s, _bmp_entry(render(s, pal, ss=4 if s <= 64 else 2)))
                      for s in SIZES])


def main() -> None:
    assets = Path(__file__).resolve().parent.parent / "assets"
    assets.mkdir(exist_ok=True)
    for name, pal in (("smartclipboard.ico", NORMAL), ("smartclipboard_paused.ico", PAUSED)):
        data = make_ico(pal)
        (assets / name).write_bytes(data)
        print(f"wrote {assets / name} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
