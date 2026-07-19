#!/usr/bin/env python3
"""Generate the boot-splash assets: the RNS logo (circular-cropped onto
transparency) and 25 ring-fill frames (ring-0.png .. ring-24.png) that the
Plymouth theme steps through as boot progresses.

Run on the medic (needs python3-pil):  python3 scripts/make_splash.py OUTDIR
"""

import math
import sys

from PIL import Image, ImageDraw, ImageFilter

SRC = "assets/ui/rns_logo.jpg"
LOGO_PX = 360           # rendered logo diameter
RING_PX = 460           # ring canvas (logo sits inside)
RING_W = 10             # ring stroke width
FRAMES = 24             # ring-0 (empty) .. ring-24 (full)
TRACK_ALPHA = 46        # faint full-circle track under the fill
FILL = (240, 240, 240, 255)          # near-white, matches the logo line-work


def make_logo(outdir: str) -> None:
    img = Image.open(SRC).convert("RGB")
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize(
        (LOGO_PX, LOGO_PX), Image.LANCZOS)
    # circular alpha mask (feathered) so the charcoal photo ground vanishes
    # into the pure-black splash background
    mask = Image.new("L", (LOGO_PX, LOGO_PX), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((6, 6, LOGO_PX - 6, LOGO_PX - 6), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    out = Image.new("RGBA", (LOGO_PX, LOGO_PX), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    out.save(f"{outdir}/logo.png")


def make_rings(outdir: str) -> None:
    pad = RING_W + 2
    box = (pad, pad, RING_PX - pad, RING_PX - pad)
    for n in range(FRAMES + 1):
        im = Image.new("RGBA", (RING_PX, RING_PX), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.arc(box, 0, 360, fill=(240, 240, 240, TRACK_ALPHA), width=RING_W)
        if n > 0:
            sweep = 360.0 * n / FRAMES
            d.arc(box, -90, -90 + sweep, fill=FILL, width=RING_W)
        im.save(f"{outdir}/ring-{n}.png")


if __name__ == "__main__":
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    make_logo(outdir)
    make_rings(outdir)
    print(f"splash assets written to {outdir}: logo.png + ring-0..{FRAMES}.png")
