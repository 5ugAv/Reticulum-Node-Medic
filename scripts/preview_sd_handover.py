"""Render the SD-handover animation OFFLINE, with PIL, so it can be looked at.

Standing practice, and it has teeth: geometry that reads fine in code has
shipped wrong twice — a portrait-oriented medic filling a landscape stage, and
a sprite carrying 82 px of invisible transparent padding that threw every
alignment out. Neither was visible in the source. Both were obvious in a
picture.

This draws the same frames the device will, using the same pure functions from
``ui.pi_sd_geometry`` and the same sprite files, so what is looked at here IS
what ships. Kivy is not involved and does not need to be installed.

    python3 scripts/preview_sd_handover.py                 # every board with art
    python3 scripts/preview_sd_handover.py pi_4b pi_zero_2w

Frames land in docs/previews/sd_handover/<board>_<n>_<label>.png at 2x the
5" panel's real stage, which is about 760 x 150 dp once the wizard's title,
body text and buttons have taken their share. That shape is the whole
difficulty: wide and short, so nothing may be stacked.
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import pi_sd_geometry as sdgeo, theme   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "previews", "sd_handover")

#: The wizard's stage on the 5" panel, doubled so a person can see it.
SCALE = 2
STAGE_W, STAGE_H = 760 * SCALE, 150 * SCALE

READER_PNG = os.path.join(ROOT, "assets", "ui", "anim", "sd_reader_body.png")
MEDIC_PNG = os.path.join(ROOT, "assets", "ui", "anim", "node_medic_body.png")
CARD_PNG = os.path.join(ROOT, "assets", "ui", "anim", "sd_card_endurance.png")

#: The moments worth looking at, and what each one has to prove.
MOMENTS = (
    (0.02, "in_reader"),      # the card starts INSIDE the reader, not floating
    (0.26, "out"),            # it has come out along the reader's own axis
    (0.40, "travel"),         # mid-flight, turning toward the slot
    (0.48, "turning"),        # edge-on: the card is being turned OVER
    (0.60, "contacts"),       # contact face now toward us (underside boards)
    (0.70, "at_slot"),        # lined up with the right edge of the right board
    (0.78, "entering"),       # visibly going IN — clipped, or under the board
    (1.00, "seated"),         # home
)


def _rgba(hexcol, alpha=1.0):
    r, g, b, a = theme.hex_to_rgba(hexcol, alpha)
    return (int(r * 255), int(g * 255), int(b * 255), int(a * 255))


def _load(path):
    return Image.open(path).convert("RGBA")


def _flip(y, h):
    """y-UP stage coordinate -> PIL's y-DOWN row."""
    return STAGE_H - (y + h)


def _paste(canvas, sprite, rect):
    x, y, w, h = rect
    im = sprite.resize((max(1, int(round(w))), max(1, int(round(h)))),
                       Image.LANCZOS)
    canvas.alpha_composite(im, (int(round(x)), int(round(_flip(y, h)))))


def _card_sprite(card, frame, card_len, card_w):
    """The card as it is at this instant: the label face, or — once it has
    turned over — its CONTACT face, drawn from ui.pi_sd_geometry's shapes.

    The widget does exactly this; if the two ever disagree the preview stops
    being evidence, so both take the geometry's word for what the reverse of a
    microSD looks like.
    """
    w = max(1, int(round(card_len)))
    h = max(1, int(round(card_w * frame.face_scale)))
    if not frame.showing_back:
        return card.resize((w, h), Image.LANCZOS)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    body, pads = sdgeo.card_back_shapes(w, h)
    back = tuple(int(c * 255) for c in sdgeo.CARD_BACK_RGB) + (255,)
    gold = tuple(int(c * 255) for c in sdgeo.CARD_PAD_RGB) + (255,)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=max(1, int(h * 0.22)),
                        fill=back)
    for px, py, pw, ph in pads:                  # local -> sprite coordinates
        x0 = w / 2.0 + px
        y0 = h / 2.0 - py - ph                   # local y is UP, PIL's is down
        d.rectangle([x0, y0, x0 + pw, y0 + ph], fill=gold)
    return im


def _card_layer(card, frame, card_len, card_w, ghost=False):
    """The card alone, on a full-stage transparent layer, rotated and clipped.

    Clipping is done here rather than by cropping the sprite because the device
    does it with a stencil over the whole stage — same shape, same result, and
    a mismatch between the two would defeat the point of previewing.
    """
    layer = Image.new("RGBA", (STAGE_W, STAGE_H), (0, 0, 0, 0))
    im = _card_sprite(card, frame, card_len, card_w)
    im = im.rotate(frame.angle, expand=True, resample=Image.BICUBIC)
    layer.alpha_composite(im, (int(round(frame.cx - im.width / 2.0)),
                               int(round(_flip(frame.cy, 0) - im.height / 2.0))))
    if frame.clip_point is not None:
        # the ghost is the COMPLEMENT: the part hidden under the board
        n = frame.clip_normal
        if ghost:
            n = (-n[0], -n[1])
        pts = sdgeo.half_plane(frame.clip_point, n, (0, 0, STAGE_W, STAGE_H))
        poly = [(pts[i], _flip(pts[i + 1], 0)) for i in range(0, len(pts), 2)]
        mask = Image.new("L", (STAGE_W, STAGE_H), 0)
        ImageDraw.Draw(mask).polygon(poly, fill=255)
        layer.putalpha(Image.composite(layer.getchannel("A"),
                                       Image.new("L", layer.size, 0), mask))
    elif ghost:
        return None
    if ghost:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * 0.30)))
    return layer


def render(pi_key, t):
    """One frame, as a PIL image, or None when this board has no artwork."""
    geo = sdgeo.geometry_for(pi_key)
    # the geometry names its own sprite, exactly as the widget does — pairing
    # the fractions with another crop of the same board misplaces the slot
    pi_png = sdgeo.sprite_path(geo) if geo else None
    if geo is None or not pi_png:
        return None
    pi = _load(pi_png)
    card = _load(CARD_PNG)
    reader = _load(READER_PNG)
    medic = _load(MEDIC_PNG)
    ra = reader.width / float(reader.height)
    # the same fraction the widget computes, from the same two slot points
    sl, sr = (0.10, 0.505), (0.50, 0.705)
    slot_frac = ((sr[0] - sl[0]) * ra) ** 2 + (sr[1] - sl[1]) ** 2
    slot_frac = slot_frac ** 0.5
    lay = sdgeo.handover_layout((0, 0, STAGE_W, STAGE_H), geo,
                                pi.width / float(pi.height),
                                card.width / float(card.height), ra, slot_frac,
                                medic.width / float(medic.height))
    frame = sdgeo.handover_frame(t, geo, lay.board, lay.card_len,
                                 lay.reader_seat, sdgeo.READER_AXIS_DEG)

    canvas = Image.new("RGBA", (STAGE_W, STAGE_H), _rgba(theme.COLORS["background"]))
    if lay.medic:
        _paste(canvas, medic, lay.medic)
    _paste(canvas, reader, lay.reader)
    layer = _card_layer(card, frame, lay.card_len, lay.card_w)
    if frame.behind_board:
        canvas.alpha_composite(layer)
        _paste(canvas, pi, lay.pi)
        ghost = _card_layer(card, frame, lay.card_len, lay.card_w, ghost=True)
        if ghost is not None:
            canvas.alpha_composite(ghost)
    else:
        _paste(canvas, pi, lay.pi)
        canvas.alpha_composite(layer)
    if frame.seated >= 1.0:
        mx, my, _span = sdgeo.slot_mouth(geo, lay.board)
        r = lay.card_w * 0.9
        ImageDraw.Draw(canvas).ellipse(
            [mx - r, _flip(my, 0) - r, mx + r, _flip(my, 0) + r],
            outline=_rgba(theme.COLORS["green"]), width=2 * SCALE)
    # a hairline on the BOARD box, so the preview also proves the sprite's
    # transparent padding has been taken off correctly
    bx, by, bw, bh = lay.board
    ImageDraw.Draw(canvas).rectangle(
        [bx, _flip(by, bh), bx + bw, _flip(by, bh) + bh],
        outline=_rgba(theme.COLORS["accent"], 0.35))
    return canvas


def main(argv):
    keys = argv or ["pi_zero_2w", "pi_3a_plus", "pi_4b", "pi_5"]
    os.makedirs(OUT, exist_ok=True)
    for key in keys:
        geo = sdgeo.geometry_for(key)
        flag = "PROVISIONAL" if (geo and geo.provisional) else "measured"
        for i, (t, label) in enumerate(MOMENTS):
            im = render(key, t)
            if im is None:
                print(f"{key}: no artwork or no geometry — skipped")
                break
            path = os.path.join(OUT, f"{key}_{i}_{label}.png")
            im.convert("RGB").save(path)
            print(f"{path}  ({flag}, edge={geo.edge}, face={geo.face})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
