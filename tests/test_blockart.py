"""Four-bit block art: a picture in a handful of mesh packets (2026-09-29).

The reproducible core of a technique the operator brought from a Reticulum
group — 48 columns, 16 colours, rounded blocks — with the byte arithmetic that
lets a screen say truthfully what a picture would cost to send.
"""

from PIL import Image

from ui import blockart as B


def _gradient(w=96, h=64):
    im = Image.new("RGB", (w, h))
    for x in range(w):
        for y in range(h):
            im.putpixel((x, y), (int(255 * x / w), int(255 * y / h), 128))
    return im


def test_the_grid_is_the_requested_width_and_keeps_the_aspect():
    g = B.quantise(_gradient(96, 64), cols=48)
    assert len(g[0]) == 48
    assert len(g) == 32, "cells are square in the source's aspect"


def test_every_cell_is_a_palette_index():
    g = B.quantise(_gradient(), cols=24, palette=B.DB16)
    assert all(0 <= i < 16 for row in g for i in row)


def test_the_post_s_arithmetic_holds():
    """48 x 64 cells at 4 bits is ~1.5 KB before compression — the number the
    post quotes, and the reason two of these fit in a text document."""
    g = [[0] * 48 for _ in range(64)]
    n = B.payload_bytes(g)
    assert 1500 <= n <= 1600
    assert B.packets(n) == 4


def test_an_odd_cell_count_rounds_the_nibble_up_not_down():
    assert B.payload_bytes([[0] * 3]) == 4 + 2, "three nibbles need two bytes"


def test_a_picture_is_never_zero_packets():
    assert B.packets(0) == 1
    assert B.packets(1) == 1
    assert B.packets(B.MTU_BYTES) == 1
    assert B.packets(B.MTU_BYTES + 1) == 2


def test_the_phosphor_ramp_is_sixteen_steps_from_ground_to_ink():
    """Sampled off the poster: dark end is its ground, light end its brightest
    ink. Sixteen, because that is the whole point — 4 bits."""
    assert len(B.PHOSPHOR16) == 16
    assert B.PHOSPHOR16[0] == (0x04, 0x10, 0x04)
    assert B.PHOSPHOR16[-1] == (0xdc, 0xf4, 0x6d)
    lum = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in B.PHOSPHOR16]
    assert lum == sorted(lum), "the ramp must be monotonic or a dither will shimmer"


def test_db16_is_the_public_palette_the_post_names():
    assert len(B.DB16) == 16
    assert B.DB16[0] == (0x14, 0x0c, 0x1c) and B.DB16[-1] == (0xde, 0xee, 0xd6)


def test_luma_mapping_is_hue_blind():
    """On a monochrome ramp hue distance is meaningless. Two colours of equal
    brightness — a red and a grey — must land on the SAME step, and a brighter
    red on a higher one. By RGB distance neither holds, and a red cable comes
    out darker than the grey beside it."""
    def step(rgb):
        return B.quantise(Image.new("RGB", (8, 8), rgb), cols=2,
                          palette=B.PHOSPHOR16, dither=False, by_luma=True)[0][0]
    red = (220, 40, 40)                     # luma ~78
    grey = (78, 78, 78)                     # luma  78
    assert step(red) == step(grey)
    assert step((250, 120, 120)) > step(red)


def test_render_is_one_block_per_cell():
    g = B.quantise(_gradient(), cols=12)
    im = B.render(g, cell=10, gap=2)
    assert im.size == (12 * 10, len(g) * 10)
