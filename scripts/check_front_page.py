"""Does the painted front page still agree with the tap-map?

The front page is artwork that IS the interface: ui/home_zones.py holds
fraction coordinates the code taps against, and nothing in the build checks
that those fractions still land on the thing they are named after. On
2026-09-28 a redesign moved the connectivity emblems from a centre column to
a four-point arrangement — so the Wi-Fi shortcut pointed at empty globe and
the credits Easter egg pointed below the cross, and neither failed loudly.
They just stopped working.

    python3 scripts/check_front_page.py [poster.png] [--overlay out.png]

Reports, and exits non-zero if anything is wrong:

  * the canvas is the panel's native 720x1280 (anything else letterboxes, and
    a painted corner control then separates from the app's own button);
  * and it PRINTS where the tap zones fall, plus an overlay to look at.

ONLY THE CANVAS SIZE IS A PASS/FAIL. Two automatic tests for "is this zone
on its emblem" were tried and both were wrong on real artwork: "is there any
ink" sailed through the 2026-09-28 redesign, whose globe graticule covers the
page, and "is it denser than the page average" then failed the SHIPPED poster,
which is light-ground — so ink density means opposite things depending on
whether the design is dark-on-light or light-on-dark. A check that cries wolf
on known-good artwork is worse than no check.

So the ink figures are printed as INFORMATION and the real test is the
overlay: it draws the zones onto the artwork so a person can see in one
second whether they sit on their emblems. Same doctrine as the animation
previews — render the geometry and LOOK.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT = os.path.join(ROOT, "assets", "ui", "front_page.png")
PANEL = (720, 1280)


def _ink(a, x0, x1, y0, y1):
    """Fraction of the box that is lit phosphor/ink rather than background."""
    import numpy as np
    sub = a[max(0, y0):y1, max(0, x0):x1]
    if sub.size == 0:
        return 0.0
    lit = (sub.max(axis=-1) > 150)
    return float(lit.mean())


def check(path=DEFAULT, overlay=None):
    import numpy as np
    from PIL import Image

    from ui.home_zones import (CARDS_LEFT, CARDS_RIGHT, CARDS_TOP, CARD_ORDER,
                               CROSS_CX, CROSS_CY, CROSS_R, WIFI_BOTTOM,
                               WIFI_LEFT, WIFI_RIGHT, WIFI_TOP)
    im = Image.open(path).convert("RGB")
    W, H = im.size
    a = np.asarray(im).astype(int)
    bad = []
    page = _ink(a, 0, W, 0, H)
    print("  page average    ink %.0f%%   (figures below are advisory — see "
          "the module docstring)" % (page * 100))

    if (W, H) != PANEL:
        bad.append("canvas is %dx%d, the panel is %dx%d — it will letterbox, "
                   "and any painted corner control will separate from the "
                   "app's own button" % (W, H, *PANEL))

    cx, cy, r = CROSS_CX * W, CROSS_CY * H, CROSS_R * W
    ink = _ink(a, int(cx - r), int(cx + r), int(cy - r), int(cy + r))
    print("  cross zone      ink %.0f%%" % (ink * 100))


    ink = _ink(a, int(WIFI_LEFT * W), int(WIFI_RIGHT * W),
               int(WIFI_TOP * H), int(WIFI_BOTTOM * H))
    print("  wi-fi zone      ink %.0f%%" % (ink * 100))


    row = _ink(a, int(CARDS_LEFT * W), int(CARDS_RIGHT * W),
               int(CARDS_TOP * H), H)
    print("  card row        ink %.0f%% across %d columns"
          % (row * 100, len(CARD_ORDER)))


    if overlay:
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        d.line([(0, CARDS_TOP * H), (W, CARDS_TOP * H)], fill=(255, 40, 40), width=4)
        for i in range(1, len(CARD_ORDER)):
            x = CARDS_LEFT * W + (CARDS_RIGHT - CARDS_LEFT) * W * i / len(CARD_ORDER)
            d.line([(x, CARDS_TOP * H), (x, H)], fill=(255, 40, 40), width=3)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 160, 0), width=4)
        d.rectangle([WIFI_LEFT * W, WIFI_TOP * H, WIFI_RIGHT * W, WIFI_BOTTOM * H],
                    outline=(0, 160, 255), width=4)
        im.save(overlay)
        print("  overlay written: %s" % overlay)

    for b in bad:
        print("  FAIL: %s" % b)
    return 0 if not bad else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = None
    if "--overlay" in sys.argv:
        out = sys.argv[sys.argv.index("--overlay") + 1]
    raise SystemExit(check(args[0] if args else DEFAULT, out))
