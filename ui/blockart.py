"""Four-bit block art — a picture that fits in a handful of mesh packets.

Homage, and a real tool. On 2026-09-29 the operator brought a post from a
Reticulum group: a demo-scene veteran shipping pictures over LoRa as a 48-column
4-bit block image on the DawnBringer 16 palette, 3 MB of PNG down to ~1.5 KB
and still recognisable — "enough to get a visual impression across over just
2-4 mesh packets". The operator wants Node Medic's animations to nod to what may
become the way pictures move on the mesh.

This module is the reproducible core of that idea, not that author's private
format: downscale to a column grid, quantise every cell to a 16-colour palette,
render as rounded blocks on a dark ground. Two palettes ship —

  * DB16, the public DawnBringer 16 the post names; and
  * PHOSPHOR16, a 16-step ramp of the tool's own green, because thirteen
    revisions of poster work settled on ONE green and a full-colour palette
    beside it would be the only multicolour thing on the glass. Same 4-bit
    constraint, same blocks, same airtime — the tool's own ink.

Pure PIL and integers, no Kivy: it runs on the medic, on the Mac, and in the
tests. ``payload_bytes`` and ``packets`` exist so any screen that shows one of
these can say, truthfully, what it would cost to send.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

Colour = Tuple[int, int, int]

#: DawnBringer's 16-colour palette (public; widely used in pixel art).
DB16: Tuple[Colour, ...] = (
    (0x14, 0x0c, 0x1c), (0x44, 0x24, 0x34), (0x30, 0x34, 0x6d), (0x4e, 0x4a, 0x4e),
    (0x85, 0x4c, 0x30), (0x34, 0x65, 0x24), (0xd0, 0x46, 0x48), (0x75, 0x71, 0x61),
    (0x59, 0x7d, 0xce), (0xd2, 0x7d, 0x2c), (0x85, 0x95, 0xa1), (0x6d, 0xaa, 0x2c),
    (0xd2, 0xaa, 0x99), (0x6d, 0xc2, 0xca), (0xda, 0xd4, 0x5e), (0xde, 0xee, 0xd6),
)


def _ramp(dark: Colour, light: Colour, steps: int = 16) -> Tuple[Colour, ...]:
    out = []
    for i in range(steps):
        t = i / (steps - 1)
        out.append(tuple(int(round(dark[k] + (light[k] - dark[k]) * t)) for k in range(3)))
    return tuple(out)


#: Sixteen steps from the poster's ground to its brightest ink (both sampled
#: off assets/ui/front_page.png on 2026-09-28).
PHOSPHOR16: Tuple[Colour, ...] = _ramp((0x04, 0x10, 0x04), (0xdc, 0xf4, 0x6d))

#: Reticulum's MTU. A block image is worth talking about in packets, not bytes.
MTU_BYTES = 500

#: The post's width, and half of an 80-column terminal — chosen so two fit
#: side by side in a text document.
DEFAULT_COLS = 48


def _nearest(c: Colour, palette: Sequence[Colour]) -> int:
    r, g, b = c
    best, best_d = 0, None
    for i, (pr, pg, pb) in enumerate(palette):
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def _luma(c: Colour) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def quantise(image, cols: int = DEFAULT_COLS, palette: Sequence[Colour] = DB16,
             dither: bool = True, by_luma: bool = False) -> List[List[int]]:
    """A PIL image -> a grid of palette indices, ``rows`` x ``cols``.

    Cells are square in the source's aspect. Floyd-Steinberg dithering by
    default: at 48 columns a hard nearest-colour cut posterises faces into
    three flat tones, and the dither is what makes the sample images read.
    ``by_luma`` maps on brightness alone — for a monochrome ramp, where hue
    distance is meaningless and a red would otherwise land on a dark step.
    """
    from PIL import Image
    im = image.convert("RGB")
    w, h = im.size
    rows = max(1, int(round(h * cols / w)))
    small = im.resize((cols, rows), Image.LANCZOS)
    px = [[small.getpixel((x, y)) for x in range(cols)] for y in range(rows)]
    if by_luma:
        lum_pal = [_luma(c) for c in palette]
        def pick(c):
            L = _luma(c)
            return min(range(len(palette)), key=lambda i: abs(lum_pal[i] - L))
        def col_of(i):
            return palette[i]
    else:
        pick = lambda c: _nearest(c, palette)          # noqa: E731
        col_of = lambda i: palette[i]                   # noqa: E731

    grid = [[0] * cols for _ in range(rows)]
    buf = [[list(map(float, c)) for c in row] for row in px]
    for y in range(rows):
        for x in range(cols):
            old = tuple(max(0, min(255, int(round(v)))) for v in buf[y][x])
            i = pick(old)
            grid[y][x] = i
            if not dither:
                continue
            new = col_of(i)
            err = [old[k] - new[k] for k in range(3)]
            for dx, dy, wgt in ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < cols and 0 <= ny < rows:
                    for k in range(3):
                        buf[ny][nx][k] += err[k] * wgt
    return grid


def payload_bytes(grid: Sequence[Sequence[int]]) -> int:
    """Raw size at 4 bits a cell, plus a 4-byte header (cols, rows, palette
    id, flags). Before any zlib — the honest floor, not the best case."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    return 4 + (rows * cols + 1) // 2


def packets(nbytes: int, mtu: int = MTU_BYTES) -> int:
    return max(1, (nbytes + mtu - 1) // mtu)


def render(grid: Sequence[Sequence[int]], palette: Sequence[Colour] = DB16,
           cell: int = 12, gap: int = 2, ground: Colour = (0x08, 0x06, 0x0a),
           radius: int = 3):
    """The grid -> a PIL image of rounded blocks with gaps, the look in the
    samples. ``cell`` is the block pitch; the block itself is ``cell - gap``."""
    from PIL import Image, ImageDraw
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    im = Image.new("RGB", (cols * cell, rows * cell), ground)
    d = ImageDraw.Draw(im)
    for y, row in enumerate(grid):
        for x, i in enumerate(row):
            x0, y0 = x * cell, y * cell
            d.rounded_rectangle([x0, y0, x0 + cell - gap - 1, y0 + cell - gap - 1],
                                radius=radius, fill=palette[i])
    return im
