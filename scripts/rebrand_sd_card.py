"""Make the SD-card illustration carry no third-party branding.

The operator, 2026-09-09, reading the imaging screen off the panel: "just to
be safe legally I'm not sure if we're allowed to use a company's name, so
let's remove SanDisk ... no proprietary names that we haven't got any
permission from".

The source art was a rendering of the operator's own card, and it carried
that manufacturer's wordmark plus the SD Association's stylised logo cluster
(microSDXC, U3, V30, C10). Both are other people's marks. What the picture is
FOR survives without either: the operator has to recognise a high-endurance
card and know the class to buy, and words say that as well as logos do.

  * the manufacturer wordmark becomes Node Medic's own mark — our cross, our
    name, so the card still reads as a branded product and the brand is ours;
  * "MAX ENDURANCE" stays. It is a description of the card type, which is the
    single most important thing on the label for a node that writes to its
    card for years (see ui/sd_reliability notes);
  * the capacity becomes 32 GB — what we would actually tell someone to buy,
    rather than the size of the card this art was drawn from;
  * the logo cluster becomes plain text: the same information, none of the
    stylised marks.

Re-runnable: it works from the ORIGINAL art, which stays in git history.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

CARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "assets", "ui", "anim", "sd_card_endurance.png")

RED = (196, 42, 38, 255)
GOLD = (166, 138, 74, 255)
DARK = (28, 28, 30, 255)


def _font(size, bold=True):
    """A face that exists on the medic as well as here, so a re-run on either
    machine produces the same card. Kivy ships Roboto-Bold with itself; the
    DejaVu bold it also names is NOT in the wheel, which is worth knowing
    before writing a path from memory."""
    import kivy
    d = os.path.join(os.path.dirname(kivy.__file__), "data", "fonts")
    face = "Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf"
    return ImageFont.truetype(os.path.join(d, face), size)


def _face_mask(im):
    """Where the CARD FACE is, with its dark outline and the transparent
    surround excluded.

    Painting a plain rectangle put white over the card's own edge and out into
    the space beside it — the card came back with a slab hanging off its
    right-hand side. The face is the alpha mask eroded by more than the
    outline is thick, so nothing this script draws can reach the border.
    """
    from PIL import ImageFilter
    a = im.split()[3].point(lambda v: 255 if v > 250 else 0)
    for _ in range(3):
        a = a.filter(ImageFilter.MinFilter(9))     # ~12 px in, well past the rim
    return a


def _wipe(im, box, face):
    """Clear a band back to the card face, and leave no seam.

    Two earlier tries showed up immediately on the panel. A flat white fill
    left a brighter rectangle, because the art carries a faint gradient. Then
    sampling one clean strip from the LEFT margin fixed the rows but not the
    columns, so a vertical seam appeared down the right-hand side of the wipe.

    So the fill is taken from the card ITSELF, just above and just below the
    band, per column, and interpolated down — whatever gradient the art has in
    either direction is reproduced rather than approximated. Nothing is drawn
    outside *face*, so the card's own edge is never painted over.
    """
    x0, y0, x1, y1 = box
    px = im.load()
    fp = face.load()
    top = {x: px[x, max(0, y0 - 6)] for x in range(x0, x1)}
    bot = {x: px[x, min(im.size[1] - 1, y1 + 6)] for x in range(x0, x1)}
    span = float(max(1, y1 - y0))
    feather = 10.0            # blend the fill into the art at the boundary,
    for y in range(y0, y1):   # or the box's own edge draws a faint line
        f = (y - y0) / span
        for x in range(x0, x1):
            if not fp[x, y]:
                continue
            a, b = top[x], bot[x]
            fill = (a[0] + (b[0] - a[0]) * f,
                    a[1] + (b[1] - a[1]) * f,
                    a[2] + (b[2] - a[2]) * f)
            edge = min(x - x0, x1 - 1 - x, y - y0, y1 - 1 - y) / feather
            k = 1.0 if edge >= 1.0 else edge
            cur = px[x, y]
            px[x, y] = (int(cur[0] + (fill[0] - cur[0]) * k),
                        int(cur[1] + (fill[1] - cur[1]) * k),
                        int(cur[2] + (fill[2] - cur[2]) * k), 255)


def main(path=CARD):
    im = Image.open(path).convert("RGBA")
    face = _face_mask(im)
    d = ImageDraw.Draw(im)

    # --- the manufacturer wordmark -> our own mark ---------------------
    _wipe(im, (200, 86, 962, 252), face)
    cx, cy, r = 268, 170, 62
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RED, width=13)
    d.rectangle([cx - 11, cy - 38, cx + 11, cy + 38], fill=RED)
    d.rectangle([cx - 38, cy - 11, cx + 38, cy + 11], fill=RED)
    d.text((cx + r + 34, cy), "NODE MEDIC", font=_font(96), fill=DARK,
           anchor="lm")

    # --- the stylised class logos -> the same facts, in words ----------
    # --- the capacity: 32 GB, not 64 --------------------------------------
    # THE NUMBER IS ADVICE, and the advice is 32 GB high-endurance (operator,
    # 2026-09-10). A node's rootfs is grown to 6 GB and never given more than
    # half the card — the rest is left unallocated as the controller's
    # wear-levelling spare pool (provisioning/pi_imager: NODE_ROOTFS_BYTES,
    # MAX_USED_FRACTION), so 32 GB is 6 used and ~26 spare, and 64 buys a node
    # nothing. The picture is the first place most people will read the
    # recommendation, so it should say what we would tell them.
    #
    # Drawn in the REGULAR weight: the original digits are a light geometric
    # face, and bold ones sat on the card like a different label. The box and
    # the baseline are measured off the original art, not eyeballed.
    _wipe(im, (206, 412, 806, 812), face)
    d.text((231, 790), "32", font=_font(500, bold=False), fill=GOLD,
           anchor="ls")

    _wipe(im, (824, 392, 1310, 906), face)
    # Two lines, not three: the big "64" already says the size, and a third
    # line pushed the block into the card's rounded corner.
    d.text((864, 520), "microSD", font=_font(88), fill=GOLD, anchor="lm")
    d.text((864, 660), "V30  U3  C10", font=_font(62), fill=GOLD, anchor="lm")
    im.save(path)
    print(f"rebranded {os.path.basename(path)}")


if __name__ == "__main__":
    main(*sys.argv[1:])

