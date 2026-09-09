"""Make the card in the reader sprite say 32 GB, like the other one.

`sd_card_endurance.png` was rebranded on 2026-09-09 and its capacity changed
to 32 GB on 2026-09-10 — the size a propagation node should actually be built
on. `sd_reader.png` draws a SECOND card, on the step that puts a card into the
medic, and it still read 64GB: two screens of one walkthrough disagreeing
about what to buy.

The lettering sits at +33 degrees in the sprite, so the edit is done in a
rotated frame: level the text, replace the digits there, and rotate ONLY the
patch back. The sprite itself is never resampled — a rotate-and-rotate-back of
the whole image would soften every edge on the card and the reader.

Nothing else on that card is touched. It carries no manufacturer's name — the
word "Endurance" is the card TYPE, which is the thing worth keeping.

    python3 scripts/rebrand_sd_reader.py [--dry-run]

Re-runnable: it refuses once the digits already read 32, so a second run is a
no-op rather than a smear.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ANIM = os.path.join(ROOT, "assets", "ui", "anim")

#: Every sprite that draws this card, with the numbers measured off each one:
#: the angle its lettering sits at, a point on the capacity line to rotate
#: about, the "64" box in the LEVELLED frame, and the type size and nudge that
#: put the replacement where the old digits were. Read off 3x previews, not
#: guessed — the two sprites are different crops of the same card.
CARDS = (
    {"path": os.path.join(_ANIM, "sd_reader.png"), "text": "32",
     "angle": -33.0, "centre": (250, 900), "digits": (170, 852, 254, 922),
     "size": 86, "dx": -3, "dy": -8},
)

#: NOT sd_card.png, and deliberately. That sprite draws the same card at a
#: different angle in a tighter crop: its glyphs TOUCH — no clean column
#: between the "4" and the "G" — and no single rotation levels its two text
#: lines at once. Every cut tried left either a hook of the old 4 or a chewed
#: "A1", and a chewed glyph is worse than an old number on a file nobody sees:
#: it is only the FALLBACK behind sd_card_endurance.png
#: (birth_anims._card_texture), used if that file ever goes missing. If it is
#: ever redrawn, it should be redrawn as art, not patched.
#: The cream the lettering is printed in, measured off the glyphs themselves.
INK = (234, 216, 180)


def _font(size):
    import kivy
    d = os.path.join(os.path.dirname(kivy.__file__), "data", "fonts")
    return ImageFont.truetype(os.path.join(d, "Roboto-Bold.ttf"), size)


def _inpaint(arr, mask, passes=24):
    """Fill *mask* from its surroundings by diffusion.

    There is no clean patch of card face big enough to clone from — the card
    is small and every part of it is printed on. Diffusion needs no source: it
    grows the surrounding shading inward, which is exactly what a blank stretch
    of that face looks like.
    """
    out = arr.astype(float).copy()
    m = mask.astype(bool)
    out[m] = np.nan
    for _ in range(passes):
        pad = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        stack = np.stack([pad[:-2, 1:-1], pad[2:, 1:-1],
                          pad[1:-1, :-2], pad[1:-1, 2:]])
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(stack, axis=0)
        out[m] = mean[m]
        if not np.isnan(out[m]).any():
            pass
    out[np.isnan(out)] = float(np.nanmedian(out))
    return out


def _one(card, dry_run=False):
    im = Image.open(card["path"]).convert("RGBA")
    angle, centre = card["angle"], card["centre"]
    lev = im.rotate(angle, resample=Image.BICUBIC, center=centre,
                    expand=False)
    x0, y0, x1, y1 = card["digits"]
    region = np.asarray(lev.crop((x0, y0, x1, y1)).convert("RGB")).astype(int)
    bright = region.min(axis=-1) > 150
    if bright.mean() < 0.05:
        print(f"{os.path.basename(card['path'])}: nothing to replace "
              "— already done?")
        return
    # grow the glyph mask so the dark outline around the lettering goes too
    mask = bright.copy()
    for _ in range(3):
        mask |= np.roll(mask, 1, 0) | np.roll(mask, -1, 0) \
            | np.roll(mask, 1, 1) | np.roll(mask, -1, 1)
    filled = _inpaint(region, mask)
    # the face is speckled; a perfectly smooth patch reads as a sticker
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 4.5, filled.shape[:2])[..., None]
    filled = np.clip(filled + noise, 0, 255).astype(np.uint8)

    patch = Image.new("RGBA", lev.size, (0, 0, 0, 0))
    patch.paste(Image.fromarray(filled).convert("RGBA"), (x0, y0))
    d = ImageDraw.Draw(patch)
    # sized to the glyphs it replaces: cap height is the box height less the
    # margin the mask was given.
    # Sized and placed against the glyphs it replaces, read off a 3x preview:
    # at 96 the digits crowded the "GB" that follows them.
    d.text(((x0 + x1) // 2 + card["dx"], y1 + card["dy"]), card["text"],
           font=_font(card["size"]), fill=INK + (255,), anchor="ms")
    back = patch.rotate(-angle, resample=Image.BICUBIC, center=centre,
                        expand=False)
    out = im.copy()
    out.alpha_composite(back)
    # never paint outside the card itself
    out.putalpha(im.split()[3])
    if not dry_run:
        out.save(card["path"])
    print(f"{os.path.basename(card['path'])}: now reads 32GB")


def main(dry_run=False):
    for card in CARDS:
        _one(card, dry_run)


if __name__ == "__main__":
    main("--dry-run" in sys.argv[1:])
