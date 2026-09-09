"""Cut the white studio background off a board photo, so it is a board in a
scene and not a sticker on the screen.

The rule the operator set (2026-09-09, looking at the guided birth on the
panel): "if there is a white background, clear it so the boards look like
animations instead of stickers". Half the catalogue was already cut — the Pi
photos, the T114, the XIAO, the EoRa illustration — and the rest were opaque
rectangles of studio white sitting on a black UI.

FLOOD-FILLED FROM THE EDGES, never "delete every white pixel". A board has
white silkscreen, white connector shells and white text on it; a colour-key
would eat those and leave holes in the middle of the hardware. Filling inward
from the border only reaches background that is actually connected to the
outside, which is the same method assets/ui/anim/pi_zero_2w_cut.png was made
with in 2026-08-04.

    python3 scripts/cut_board_art.py [--dry-run] [--thresh N] [files...]

With no files it does every PNG under assets/boards plus the animation Pi.
Already-cut art is skipped (it reports how much of each file is opaque, so a
re-run is a check as much as a conversion). Originals are recoverable from git
— that is deliberately the only backup, so there is one file per board and no
"_cut" twin to keep in sync.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: A colour to paint the background with before turning it transparent. Checked
#: against the image first — if the photo happens to contain it, the file is
#: skipped rather than silently holed.
MAGIC = (255, 0, 255)

#: How far from the corner colour still counts as background. 40 keeps a soft
#: studio gradient together without crossing onto a light-grey connector.
DEFAULT_THRESH = 40


def default_files():
    d = os.path.join(ROOT, "assets", "boards")
    out = [os.path.join(d, f) for f in sorted(os.listdir(d))
           if f.endswith(".png")]
    out.append(os.path.join(ROOT, "assets", "ui", "anim", "pi_zero_2w.png"))
    return out


def opaque_fraction(im):
    a = im.convert("RGBA").split()[3]
    hist = a.histogram()
    total = sum(hist) or 1
    return sum(hist[201:]) / total


def cut(path, thresh=DEFAULT_THRESH, dry_run=False):
    im = Image.open(path).convert("RGBA")
    frac = opaque_fraction(im)
    if frac < 0.97:
        return f"skip (already cut, {frac:.0%} opaque)"
    rgb = im.convert("RGB")
    if MAGIC in rgb.getcolors(maxcolors=1 << 24) and False:
        pass
    colours = {c for _n, c in (rgb.getcolors(maxcolors=1 << 24) or [])}
    if MAGIC in colours:
        return "skip (image contains the fill colour)"
    w, h = rgb.size
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    px = rgb.load()
    if not all(min(px[c]) > 200 for c in corners):
        return "skip (corners are not a light background)"
    for c in corners:
        ImageDraw.floodfill(rgb, c, MAGIC, thresh=thresh)
    # Every pixel the fill reached becomes transparent...
    filled = rgb.point(lambda v: v)          # copy
    mask = Image.new("L", (w, h), 255)
    fp = filled.load()
    mp = mask.load()
    for yy in range(h):
        for xx in range(w):
            if fp[xx, yy] == MAGIC:
                mp[xx, yy] = 0
    # ...and the boundary gets one pixel of softness, so the cut edge does not
    # read as a cardboard cut-out. Blur only pulls the edge in; the interior is
    # re-clamped to fully opaque afterwards.
    soft = mask.filter(ImageFilter.GaussianBlur(1.2))
    soft = Image.eval(soft, lambda v: 255 if v > 245 else v)
    out = im.copy()
    out.putalpha(soft)
    if not dry_run:
        out.save(path)
    return f"cut ({opaque_fraction(out):.0%} opaque now)"


def main(argv):
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    thresh = DEFAULT_THRESH
    if "--thresh" in argv:
        i = argv.index("--thresh")
        thresh = int(argv[i + 1])
        del argv[i:i + 2]
    files = argv or default_files()
    for f in files:
        print(f"{os.path.basename(f):30s} {cut(f, thresh, dry)}")


if __name__ == "__main__":
    main(sys.argv[1:])
