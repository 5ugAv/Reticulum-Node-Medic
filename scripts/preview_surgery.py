"""Render the card-writing theatre OFFLINE, with PIL, so it can be looked at.

The same practice as preview_sd_handover.py, for the same reason: staging that
reads fine in code has shipped wrong more than once, and the fault was obvious
the moment it was a picture. On 2026-09-22 the operator photographed this
scene and called the surgeon's arms "very rudimentary"; the redesign was drawn
here, frame by frame, before it went anywhere near the panel.

Every number comes from ``ui.surgery_layout`` — the same module the widget
draws from — so what is looked at here IS what ships. Kivy is not involved and
does not need to be installed (this Mac has no window provider for it).

    python3 scripts/preview_surgery.py [out_dir]

Frames land in docs/previews/surgery/ (or *out_dir*) at 2x the stage the
imager screen gives the widget on the 5" panel in portrait: about 700 x 230
dp. Each frame is a MOMENT of the loop — a still cannot judge a motion, so
several are taken across one pick-and-place cycle and across the write. A
contact sheet, a narrow-stage frame (self-sizing proof) and a portrait mock-up
of the panel are written alongside.

Kivy's ``Line(width=w)`` paints a stroke 2w thick; PIL's ``width`` is the full
thickness, so every width here is doubled to match the device.
"""

from __future__ import annotations

import math
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from provisioning.pi_imager import IMAGING_STAGES, current_stage_label, stages_upto  # noqa: E402
from ui import surgery_layout as sl, theme  # noqa: E402
from ui.organ_art import ORGANS, organ_file  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "previews", "surgery")
ANIM = os.path.join(ROOT, "assets", "ui", "anim")
CARD_PNG = os.path.join(ANIM, "sd_card_endurance.png")
MEDIC_PNG = os.path.join(ANIM, "node_medic_body.png")

SCALE = 2
STAGE_W, STAGE_H = 700, 230

#: (write fraction, loop phase, discharged, label) — what each frame proves.
MOMENTS = (
    (0.05, 0.00, 0.0, "grip"),        # bootloader in; kernel gripped on the tray
    (0.05, 0.35, 0.0, "carry"),       # carried, lifted, HELD in the forceps
    (0.05, 0.60, 0.0, "fit"),         # pressed into its seat
    (0.05, 0.87, 0.0, "withdraw"),    # forceps open, arm on its way back
    (0.50, 0.35, 0.0, "mid_write"),   # three in, reticulum on its way
    (0.90, 0.60, 0.0, "late"),        # four in, identity being fitted
    (1.00, 0.20, 0.0, "done"),        # all five, arm parked raised, smile
    (1.00, 0.20, 1.0, "discharged"),  # monitor off: tick and the word
)

# the widget's colours, verbatim
_TABLE = (0.16, 0.19, 0.22, 1)
_TABLE_EDGE = (0.38, 0.44, 0.50, 1)
_TRACE = (0.35, 0.95, 0.55, 1)
_OUTLINE = (0.10, 0.09, 0.08, 1)
_ARM = (0.33, 0.30, 0.27, 1)
_ARM_HI = (0.62, 0.56, 0.46, 1)
_STEEL = (0.80, 0.83, 0.86, 1)


def _c(rgba, alpha=None):
    r, g, b = rgba[:3]
    a = rgba[3] if alpha is None else alpha
    return (int(r * 255), int(g * 255), int(b * 255), int(a * 255))


def _hex(name, alpha=1.0):
    return _c(theme.hex_to_rgba(theme.COLORS[name], alpha))


class Frame:
    """One stage, y-UP like the widget; every draw call flips."""

    def __init__(self, w, h, scale):
        self.w, self.h, self.s = w, h, scale
        self.im = Image.new("RGBA", (w * scale, h * scale), _hex("background"))

    def dp(self, v):
        return v * self.s

    def _p(self, p):
        return (p[0] * self.s, (self.h - p[1]) * self.s)

    def _layer(self):
        return Image.new("RGBA", self.im.size, (0, 0, 0, 0))

    def rect(self, r, colour, radius=0.0):
        x, y, w, h = r
        x0, y0 = self._p((x, y + h))
        x1, y1 = self._p((x + w, y))
        lay = self._layer()
        ImageDraw.Draw(lay).rounded_rectangle([x0, y0, x1, y1],
                                              radius=radius * self.s, fill=colour)
        self.im.alpha_composite(lay)

    def rect_outline(self, r, colour, width, radius=0.0):
        x, y, w, h = r
        x0, y0 = self._p((x, y + h))
        x1, y1 = self._p((x + w, y))
        ImageDraw.Draw(self.im).rounded_rectangle(
            [x0, y0, x1, y1], radius=radius * self.s, outline=colour,
            width=max(1, int(round(width * 2 * self.s))))

    def ellipse(self, centre, r, colour):
        cx, cy = self._p(centre)
        rr = r * self.s
        lay = self._layer()
        ImageDraw.Draw(lay).ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=colour)
        self.im.alpha_composite(lay)

    def ring(self, centre, r, colour, width):
        cx, cy = self._p(centre)
        rr = r * self.s
        ImageDraw.Draw(self.im).ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                                        outline=colour,
                                        width=max(1, int(round(width * 2 * self.s))))

    def line(self, pts, width, colour):
        """A Kivy Line(width=width, cap='round', joint='round')."""
        th = max(1, int(round(width * 2 * self.s)))
        p = [self._p(q) for q in pts]
        d = ImageDraw.Draw(self.im)
        d.line(p, fill=colour, width=th, joint="curve")
        for q in (p[0], p[-1]):
            d.ellipse([q[0] - th / 2, q[1] - th / 2, q[0] + th / 2, q[1] + th / 2],
                      fill=colour)

    def stroke(self, pts, width, colour, outline=True):
        if outline:
            self.line(pts, width + 1.2, _c(_OUTLINE))
        self.line(pts, width, colour)

    def joint(self, p, r, colour=_c(_ARM)):
        self.ellipse(p, r + 1.2, _c(_OUTLINE))
        self.ellipse(p, r, colour)
        self.ellipse(p, r * 0.35, _c(_ARM_HI))

    def quad(self, pts, colour):
        lay = self._layer()
        ImageDraw.Draw(lay).polygon([self._p(q) for q in pts], fill=colour)
        self.im.alpha_composite(lay)

    def sprite(self, path, r, alpha=1.0):
        x, y, w, h = r
        im = Image.open(path).convert("RGBA").resize(
            (max(1, int(round(w * self.s))), max(1, int(round(h * self.s)))),
            Image.LANCZOS)
        if alpha < 1.0:
            im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
        x0, y0 = self._p((x, y + h))
        self.im.alpha_composite(im, (int(round(x0)), int(round(y0))))

    def text(self, at, s, colour):
        ImageDraw.Draw(self.im).text(self._p(at), s, fill=colour)


def _organ(fr, key, at, lay, glow=0.0, alpha=1.0):
    art = ORGANS.get(key)
    col = art[1] if art else (1, 1, 1, 1)
    r = lay.organ_r
    if glow > 0.0:
        fr.ellipse(at, r * 1.9, _c(col, glow * alpha))
    path = organ_file(key)
    if path and os.path.exists(path):
        im = Image.open(path)
        gw = sl.organ_diam(lay)
        gh = gw * (im.height / float(im.width))
        fr.sprite(path, (at[0] - gw / 2, at[1] - gh / 2, gw, gh), alpha)
    else:
        fr.ellipse(at, r, _c(col, alpha))


def _arm(fr, pose, lay, key):
    r = lay.organ_r
    w_up = max(fr.dp(4) / fr.s, r * 0.70)
    w_fore, w_fore2, w_rod = w_up * 0.72, w_up * 0.56, w_up * 0.42
    w_tine = max(1.6, r * 0.22)
    sh, el, sv, sv2, wr, tip = (lay.shoulder, pose.elbow, pose.sleeve,
                                pose.sleeve2, pose.wrist, pose.tip)
    fr.stroke([sh, el], w_up, _c(_ARM))
    fr.stroke([el, wr], w_rod, _c(_STEEL))
    fr.stroke([el, sv2], w_fore2, _c(_ARM))
    fr.stroke([el, sv], w_fore, _c(_ARM))
    fr.joint(sh, w_up * 1.1)
    fr.joint(el, w_up * 0.85)
    fr.stroke([wr, pose.tine_a], w_tine, _c(_STEEL))
    fr.stroke([wr, pose.tine_b], w_tine, _c(_STEEL))
    if pose.carrying and key:
        _organ(fr, key, tip, lay, glow=0.30)
        fr.line([wr, pose.tine_a], w_tine, _c(_STEEL))
        fr.line([wr, pose.tine_b], w_tine, _c(_STEEL))
    fr.joint(wr, max(2.2, w_fore * 0.55), _hex("red"))


def render(fraction, phase, discharged=0.0, stage=(STAGE_W, STAGE_H), scale=SCALE):
    """One frame of the theatre, as the widget would paint it."""
    w, h = stage
    fr = Frame(w, h, scale)
    card_im = Image.open(CARD_PNG)
    medic_im = Image.open(MEDIC_PNG)
    lay = sl.layout((0.0, 0.0, float(w), float(h)),
                    card_im.width / float(card_im.height),
                    medic_im.width / float(medic_im.height), pad=2.0)
    landed = [s["organ"] for s in stages_upto(fraction)]
    done = fraction >= 1.0
    nxt = next((s for s in IMAGING_STAGES if s["at"] > fraction), None)
    seat = sl.seat_point(lay.window, nxt["organ"]) if nxt else None
    pose = sl.arm_pose(phase, lay, seat)
    cx, cy, cw, ch = lay.card

    # the light
    top = lay.monitor[1] - 2.0
    fr.quad([(cx + cw * 0.30, top), (cx + cw * 0.70, top),
             (cx + cw * 1.15, cy), (cx - cw * 0.15, cy)], _c((1.0, 0.98, 0.85, 0.10)))
    # the table
    fr.rect(lay.base, _c((0.10, 0.12, 0.14, 1)), radius=3)
    fr.rect(lay.pedestal, _c((0.10, 0.12, 0.14, 1)))
    fr.rect(lay.table, _c(_TABLE), radius=4)
    tx, ty, tw, th = lay.table
    fr.line([(tx + 3, ty + th - 1), (tx + tw - 3, ty + th - 1)], 1.0, _c(_TABLE_EDGE))
    # the tray
    fr.rect(lay.tray, _c((0.08, 0.09, 0.10, 1)), radius=3)
    fr.rect_outline(lay.tray, _c(_TABLE_EDGE), 1.0, radius=3)
    # the patient
    fr.sprite(CARD_PNG, lay.card)
    # the lead
    fr.stroke(sl.lead_points(lay), 1.6, _c(_TRACE, 0.85))
    pr = max(3.0, lay.organ_r * 0.45)
    fr.ellipse(lay.lead_pad, pr + 1, _c(_OUTLINE))
    fr.ellipse(lay.lead_pad, pr, _c(_TRACE))
    # the window and the organs in it
    fr.rect(lay.window, _c((0.10, 0.10, 0.11, 1)), radius=3)
    for i, key in enumerate(landed):
        pulse = 0.5 + 0.5 * math.sin(phase * 2 * math.pi + i * 0.9)
        _organ(fr, key, sl.seat_point(lay.window, key), lay, glow=0.22 + 0.16 * pulse)
    if nxt and pose.fit > 0.0 and nxt["organ"] not in landed:
        _organ(fr, nxt["organ"], seat, lay, glow=0.0, alpha=0.35 + 0.55 * pose.fit)
    if nxt:
        wait = sl.tray_organ_alpha(phase)
        if wait > 0.0:
            _organ(fr, nxt["organ"], lay.pick, lay, glow=0.30 * wait, alpha=wait)
    # the surgeon and its arm
    fr.sprite(MEDIC_PNG, lay.medic)
    _arm(fr, pose, lay, nxt["organ"] if nxt else None)
    # the monitor
    mx, my, mw, mh = lay.monitor
    fr.rect(lay.monitor, _c((0.06, 0.09, 0.08, 1)), radius=6)
    fr.rect_outline(lay.monitor, _c((0.20, 0.30, 0.26, 1)), 1.2, radius=6)
    steady = fraction
    if discharged < 1.0:
        pts = sl.monitor_trace(lay.monitor, steady, phase)
        fr.line(pts, 1.8, _c(_TRACE, (0.55 + 0.45 * steady) * (1.0 - discharged)))
        if done:
            amp = mh * (0.16 + 0.26 * steady)
            fr.ring((mx + mw * 0.86, my + mh * 0.5 + amp * 0.9), mh * 0.18,
                    _c(_TRACE, 0.9 * (1.0 - discharged)), 2.0)
    if discharged > 0.0:
        p = min(1.0, (discharged - 0.45) / 0.55)
        if p > 0.0:
            s = min(mh * 0.30, mw * 0.06)
            cyy = my + mh * 0.5
            txx = mx + mw / 2.0 - s * 2.5
            fr.line([(txx - s, cyy + s * 0.05), (txx - s * 0.35, cyy - s * 0.55),
                     (txx - s * 0.35 + s * 1.35, cyy - s * 0.55 + s * 1.25)],
                    3.0, _c(_TRACE, p))
            if p > 0.9:
                fr.text((txx + s * 1.9, cyy + 5), "DISCHARGED", _c(_TRACE))
    if done:
        er = min(cw, ch) * 0.030
        for dx in (0.79, 0.89):
            fr.ellipse((cx + cw * dx, cy + ch * 0.87), er, _c((0.15, 0.13, 0.10, 1)))
        smw, smh = cw * 0.12, ch * 0.08
        x0, y0 = fr._p((cx + cw * 0.84 - smw / 2, cy + ch * 0.80 + smh / 2))
        x1, y1 = fr._p((cx + cw * 0.84 + smw / 2, cy + ch * 0.80 - smh / 2))
        ImageDraw.Draw(fr.im).arc([x0, y0, x1, y1], 10, 170,
                                  fill=_c((0.15, 0.13, 0.10, 1)),
                                  width=max(1, int(4 * scale / 2)))
    return fr.im


def portrait_mock(frame_1x, fraction):
    """The stage where it sits on the portrait panel, with the caption under
    it — so the picture is judged at the size and place it is seen."""
    im = Image.new("RGBA", (720, 1280), _hex("background"))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([20, 300, 700, 540], radius=6, fill=_hex("warning_yellow"),
                        outline=_hex("red"), width=3)
    d.text((40, 320), "Leave everything alone until this finishes", fill=(20, 20, 20, 255))
    d.text((40, 360), "Don't unplug anything from Node Medic, don't take the card out,",
           fill=(20, 20, 20, 255))
    d.text((40, 380), "and don't power Node Medic off.", fill=(20, 20, 20, 255))
    im.alpha_composite(frame_1x, (10, 560))
    d.text((24, 810), current_stage_label(fraction), fill=_hex("accent"))
    return im


def main(argv):
    out = argv[0] if argv else OUT
    os.makedirs(out, exist_ok=True)
    frames = []
    for i, (frac, ph, gone, label) in enumerate(MOMENTS):
        im = render(frac, ph, gone)
        path = os.path.join(out, f"surgery_{i}_{label}.png")
        im.convert("RGB").save(path)
        frames.append((label, im))
        print(path)
    # contact sheet: the whole cycle at a glance
    cols = 2
    rows = (len(frames) + cols - 1) // cols
    fw, fh = frames[0][1].size
    sheet = Image.new("RGB", (cols * fw + (cols + 1) * 12, rows * (fh + 30) + 12),
                      (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(frames):
        x = 12 + (i % cols) * (fw + 12)
        y = 12 + (i // cols) * (fh + 30)
        sheet.paste(im.convert("RGB"), (x, y))
        d.text((x, y + fh + 6), f"{i}: {label}", fill=(230, 230, 230))
    path = os.path.join(out, "surgery_contact_sheet.png")
    sheet.save(path)
    print(path)
    # a narrow stage: self-sizing proof
    im = render(0.5, 0.35, 0.0, stage=(360, 230))
    path = os.path.join(out, "surgery_narrow_360.png")
    im.convert("RGB").save(path)
    print(path)
    # in situ, on the portrait panel, at 1x
    im = render(0.05, 0.35, 0.0, scale=1)
    path = os.path.join(out, "surgery_portrait_mock.png")
    portrait_mock(im, 0.05).convert("RGB").save(path)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
