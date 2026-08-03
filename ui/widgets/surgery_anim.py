"""The card's operation: a Pi in a head mirror, implanting organs into an SD card.

The operator's own scene (2026-08-02): *"The Raspberry Pi is dressed as a
miniature doctor. The SD card lies on an operating table. The Pi gently installs
little glowing components — kernel, filesystem, reticulum logo, bootloader —
like organs. A heart monitor gradually stabilises. At the end: the monitor beeps
happily, the SD card smiles, the Pi gives a thumbs up."*

Why it earns its screen. Writing a card takes minutes with nothing to look at,
and the most damaging thing an operator can do in that window is decide it has
hung and pull the card. A scene that visibly PROGRESSES — organs landing one by
one, a heart trace steadying — answers "is this still working?" without anyone
having to trust a number. The stage list it narrates
(``provisioning.pi_imager.IMAGING_STAGES``) follows the real order of
``flash()``, so it is a picture of the actual operation rather than a loading
toy.

Everything is drawn from primitives plus the existing board/card sprites: no new
artwork to commission, and no emoji — the glyph fonts on this panel render them
as tofu boxes (the on-screen keyboard learned that the hard way).
"""

from __future__ import annotations

import math
import os

from kivy.graphics import (Color, Ellipse, Line, Quad, Rectangle,
                           RoundedRectangle)
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.widget import Widget

from ui import theme
from ui.widgets.birth_anims import (PI_ZERO_PNG, SD_ENDURANCE_PNG, SD_PNG,
                                    _texture)

from ui.organ_art import (CARD_WINDOW as _CARD_WINDOW, ORGANS as _ORGANS,
                          ORGAN_SEATS as _ORGAN_SEATS,
                          SPARE_ORGANS as _SPARE_ORGANS, organ_file)


def _organ_texture(key):
    """The sprite for an organ, or None so the caller falls back to a disc."""
    path = organ_file(key)
    return _texture(path) if path else None


_TABLE = (0.16, 0.19, 0.22, 1)
_TRACE = (0.35, 0.95, 0.55, 1)


class SurgeryAnim(Widget):
    """Drive with :meth:`set_fraction` (0→1). ``pi_key`` picks the surgeon's art."""

    phase = NumericProperty(0.0)          # free-running, for the idle motion
    fraction = NumericProperty(0.0)       # the real progress of the write

    def __init__(self, pi_key: str = "", **kwargs):
        super().__init__(**kwargs)
        self._ev = None
        self._pi_png = ""
        if pi_key:
            try:
                from ui import board_images
                self._pi_png = board_images.image_for_pi(pi_key) or ""
            except Exception:
                self._pi_png = ""
        self._landed = []                 # organ keys already implanted
        self.bind(phase=self._redraw, fraction=self._redraw,
                  pos=self._redraw, size=self._redraw)

    # -- driving ----------------------------------------------------------
    def start(self):
        from kivy.clock import Clock
        self.stop()
        self._ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    def stop(self):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def _tick(self, dt):
        self.phase = (self.phase + dt * 0.6) % 1.0

    def set_fraction(self, f):
        from provisioning.pi_imager import stages_upto
        f = max(0.0, min(1.0, float(f or 0.0)))
        self._landed = [s["organ"] for s in stages_upto(f)]
        self.fraction = f

    # -- drawing ----------------------------------------------------------
    def _redraw(self, *_):
        self.canvas.clear()
        if self.width < dp(120) or self.height < dp(90):
            return                        # too small to read; draw nothing
        with self.canvas:
            self._draw_scene()

    # -- layout ------------------------------------------------------------
    # One place to move things. The first version scattered the pieces across
    # the widget with dead space between them and they read as loose objects
    # rather than a scene (operator, on the live screen, 2026-08-02). The stage
    # is wide and short (roughly 700x230 on the 5" panel), so: monitor as a band
    # across the top, table low and left, surgeon standing to its right and
    # LEANING OVER the card — overlapping the table so the two are related.
    _MON_H = 0.34            # monitor band, fraction of height
    _TABLE_X, _TABLE_W = 0.04, 0.50
    _TABLE_TOP = 0.22        # table surface height (card sits ON this)
    _PI_X, _PI_W = 0.52, 0.44

    def _draw_scene(self):
        x, y, w, h = self.x, self.y, self.width, self.height
        done = self.fraction >= 1.0

        # --- the operating table -------------------------------------------
        tw = w * self._TABLE_W
        tx = x + w * self._TABLE_X
        ty = y + h * self._TABLE_TOP           # the SURFACE
        th = h * 0.075
        Color(*_TABLE)
        RoundedRectangle(pos=(tx, ty - th), size=(tw, th), radius=[dp(5)] * 4)
        Color(0.10, 0.12, 0.14, 1)
        for leg in (tx + tw * 0.10, tx + tw * 0.82):
            Rectangle(pos=(leg, y), size=(dp(5), ty - th - y))

        # --- the patient: the microSD card, LYING ON the table ---------------
        card = _texture(SD_ENDURANCE_PNG) or _texture(SD_PNG)
        cw = tw * 0.60
        ch = cw * 0.72
        if card is not None:
            ch = cw * (card.height / float(card.width))
            if ch > h * 0.34:
                ch = h * 0.34
                cw = ch * (card.width / float(card.height))
        cx = tx + (tw - cw) / 2.0
        cy = ty                                 # resting on the surface
        self._card_box = (cx, cy, cw, ch)

        # --- the surgical light, tying the scene together --------------------
        # A soft cone from above onto the patient. Cheap, and it does the job
        # composition was failing at: it says these two things belong together.
        top = y + h * (1.0 - self._MON_H) - dp(2)
        Color(1.0, 0.98, 0.85, 0.07)
        Quad(points=[cx + cw * 0.30, top, cx + cw * 0.70, top,
                     cx + cw * 1.15, cy, cx - cw * 0.15, cy])

        if card is not None:
            Color(1, 1, 1, 1)
            Rectangle(texture=card, pos=(cx, cy), size=(cw, ch))
        else:
            Color(0.90, 0.90, 0.92, 1)
            RoundedRectangle(pos=(cx, cy), size=(cw, ch), radius=[dp(4)] * 4)

        # --- the window the organs live in ----------------------------------
        wx0, wy0, wx1, wy1 = _CARD_WINDOW
        win_x = cx + cw * wx0
        win_w = cw * (wx1 - wx0)
        win_h = ch * (wy1 - wy0)
        win_y = cy + ch * (1.0 - wy1)          # fractions are top-down
        Color(0.10, 0.10, 0.11, 1)
        RoundedRectangle(pos=(win_x, win_y), size=(win_w, win_h),
                         radius=[dp(3)] * 4)

        # --- the organs, seated in the patient ------------------------------
        # Sized so FIVE fit the row: the seats are ~0.19 apart, and an organ
        # is drawn at r*2.6, so r must be about win_w/14. At win_w/5.6 each
        # organ came out 46% of the window wide and they overlapped and spilled
        # off both ends (offline render, 2026-08-04).
        r = min(win_w / 14.0, win_h * 0.38)
        for key in self._landed:
            art = _ORGANS.get(key)
            col = art[1] if art else (1, 1, 1, 1)
            fx, fy = _ORGAN_SEATS.get(key, (0.5, 0.5))
            ox, oy = win_x + win_w * fx, win_y + win_h * fy
            # a soft glow that breathes, so an implanted organ reads as ALIVE
            pulse = 0.5 + 0.5 * math.sin(self.phase * 2 * math.pi + hash(key) % 7)
            Color(col[0], col[1], col[2], 0.22 + 0.16 * pulse)
            Ellipse(pos=(ox - r * 1.9, oy - r * 1.9), size=(r * 3.8, r * 3.8))
            tex = _organ_texture(key)
            if tex is not None:
                gw = r * 2.6
                gh = gw * (tex.height / float(tex.width))
                Color(1, 1, 1, 1)
                Rectangle(texture=tex, pos=(ox - gw / 2, oy - gh / 2),
                          size=(gw, gh))
            else:
                Color(*col)
                Ellipse(pos=(ox - r, oy - r), size=(r * 2, r * 2))

        self._draw_incoming(cx, cy, cw, ch)

        # --- the surgeon, leaning in over the table -------------------------
        self._draw_surgeon(x + w * self._PI_X, y + h * 0.06,
                           w * self._PI_W, h * (0.94 - self._MON_H), done)

        # --- the heart monitor, as a band across the top ---------------------
        self._draw_monitor(x + dp(2), y + h * (1.0 - self._MON_H),
                           w - dp(4), h * self._MON_H - dp(2), done)

        if done:
            self._draw_smile(cx, cy, cw, ch)

    def _draw_incoming(self, cx, cy, cw, ch):
        """The next organ, in the surgeon's hands, on its way to its seat."""
        from provisioning.pi_imager import IMAGING_STAGES
        nxt = next((s for s in IMAGING_STAGES if s["at"] > self.fraction), None)
        if nxt is None:
            return
        art = _ORGANS.get(nxt["organ"])
        col = art[1] if art else (1, 1, 1, 1)
        fx, fy = _ORGAN_SEATS.get(nxt["organ"], (0.5, 0.5))
        wx0, wy0, wx1, wy1 = _CARD_WINDOW
        # travel is the free-running phase, so it keeps moving even when the
        # write is between stages — a still picture reads as a hang
        t = 0.5 - 0.5 * math.cos(self.phase * 2 * math.pi)
        sx, sy = self.x + self.width * 0.72, self.y + self.height * 0.52
        tx_ = cx + cw * wx0 + cw * (wx1 - wx0) * fx
        ty_ = cy + ch * (1.0 - wy1) + ch * (wy1 - wy0) * fy
        px = sx + (tx_ - sx) * t
        py = sy + (ty_ - sy) * t
        r = min(cw, ch) * 0.11
        Color(col[0], col[1], col[2], 0.30)
        Ellipse(pos=(px - r * 2, py - r * 2), size=(r * 4, r * 4))
        tex = _organ_texture(nxt["organ"])
        if tex is not None:
            gw = r * 2.4
            gh = gw * (tex.height / float(tex.width))
            Color(1, 1, 1, 1)
            Rectangle(texture=tex, pos=(px - gw / 2, py - gh / 2), size=(gw, gh))
        else:
            Color(col[0], col[1], col[2], 0.95)
            Ellipse(pos=(px - r, py - r), size=(r * 2, r * 2))

    def _draw_surgeon(self, x, y, w, h, done):
        """The Pi, in a head mirror and mask, leaning over the patient.

        Drawn BIG. In the first version the board was ~30% of the stage and the
        mirror and mask were a few pixels across — invisible on the panel, so it
        read as a stray circuit board rather than a doctor.
        """
        tex = (_texture(self._pi_png) if self._pi_png else None) \
            or _texture(PI_ZERO_PNG)
        bw = w * 0.86
        bh = bw * 0.55
        if tex is not None:
            bh = bw * (tex.height / float(tex.width))
            if bh > h * 0.66:
                bh = h * 0.66
                bw = bh * (tex.width / float(tex.height))
        bx = x + (w - bw) / 2.0
        by = y + h * 0.06
        if tex is not None:
            Color(1, 1, 1, 1)
            Rectangle(texture=tex, pos=(bx, by), size=(bw, bh))
        else:
            Color(0.20, 0.55, 0.30, 1)
            RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[dp(5)] * 4)

        # head mirror — sized to actually be seen on a 5" panel
        mr = max(dp(9), min(bw, bh) * 0.26)
        mx = bx + bw * 0.5
        my = by + bh + mr * 0.72
        Color(0.87, 0.90, 0.94, 1)
        Ellipse(pos=(mx - mr, my - mr), size=(mr * 2, mr * 2))
        Color(0.13, 0.15, 0.17, 1)
        Ellipse(pos=(mx - mr * 0.40, my - mr * 0.40), size=(mr * 0.80, mr * 0.80))
        Color(0.87, 0.90, 0.94, 1)
        Line(points=[mx, my - mr, mx, by + bh], width=dp(2.0))

        # surgical mask across the board's lower edge
        Color(0.58, 0.86, 0.86, 0.95)
        RoundedRectangle(pos=(bx + bw * 0.14, by + bh * 0.05),
                         size=(bw * 0.72, bh * 0.26), radius=[dp(5)] * 4)
        Color(0.45, 0.72, 0.74, 1)
        Line(points=[bx + bw * 0.14, by + bh * 0.20,
                     bx, by + bh * 0.34], width=dp(1.4))
        Line(points=[bx + bw * 0.86, by + bh * 0.20,
                     bx + bw, by + bh * 0.34], width=dp(1.4))

        # the hands: two arms reaching down-left toward the patient, so the
        # surgeon is clearly WORKING ON the card rather than standing near it
        cb = getattr(self, "_card_box", None)
        if cb:
            cx, cy, cw, ch = cb
            hx, hy = cx + cw * 0.72, cy + ch * 0.86
            Color(0.98, 0.82, 0.64, 0.95)
            for dxy in (0.0, dp(7)):
                Line(points=[bx + bw * 0.10, by + bh * 0.45 - dxy,
                             hx + dxy, hy], width=dp(2.4))
            Ellipse(pos=(hx - dp(5), hy - dp(5)), size=(dp(10), dp(10)))

        if done:
            hx2, hy2 = bx + bw * 1.00, by + bh * 0.62
            s2 = max(dp(12), min(bw, bh) * 0.30)
            Color(0.98, 0.80, 0.62, 1)
            RoundedRectangle(pos=(hx2, hy2), size=(s2, s2 * 0.9),
                             radius=[s2 * 0.28] * 4)
            RoundedRectangle(pos=(hx2 + s2 * 0.30, hy2 + s2 * 0.72),
                             size=(s2 * 0.34, s2 * 0.74), radius=[s2 * 0.17] * 4)

    def _draw_monitor(self, x, y, w, h, done):
        """The trace: noise and dropouts early, a clean rhythm by the end."""
        Color(0.06, 0.09, 0.08, 1)
        RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(6)] * 4)
        Color(0.20, 0.30, 0.26, 1)
        Line(rounded_rectangle=(x, y, w, h, dp(6)), width=dp(1.2))

        steady = self.fraction                     # 0 = erratic, 1 = strong
        mid = y + h * 0.5
        amp = h * (0.16 + 0.26 * steady)
        pts = []
        n = 90
        scroll = self.phase * 2.0
        for i in range(n + 1):
            u = i / float(n)
            # beats per screen rises as the patient stabilises
            beat = (u * 2.2 + scroll) % 1.0
            v = 0.0
            if 0.10 < beat < 0.16:                 # P
                v = 0.22
            elif 0.20 < beat < 0.24:               # Q
                v = -0.30
            elif 0.24 < beat < 0.30:               # R, the spike
                v = 1.0
            elif 0.30 < beat < 0.35:               # S
                v = -0.45
            elif 0.45 < beat < 0.56:               # T
                v = 0.30
            # early on the rhythm is unreliable: the spike often fails to fire
            if steady < 0.9:
                flicker = math.sin((u * 13.0 + self.phase * 5.0) * math.pi)
                if flicker > (0.10 + 0.85 * steady):
                    v *= 0.15
                v += (1.0 - steady) * 0.10 * math.sin(u * 47.0 + scroll * 6.0)
            pts.extend([x + w * u, mid + amp * v])
        Color(_TRACE[0], _TRACE[1], _TRACE[2], 0.55 + 0.45 * steady)
        Line(points=pts, width=dp(1.8))

        if done:
            # a happy beep: a bright ring at the last spike
            Color(_TRACE[0], _TRACE[1], _TRACE[2], 0.9)
            r = h * 0.18
            Line(circle=(x + w * 0.86, mid + amp * 0.9, r), width=dp(2))

    def _draw_smile(self, cx, cy, cw, ch):
        """The patient, pleased with the outcome."""
        ex = cw * 0.16
        ey = ch * 0.72
        er = min(cw, ch) * 0.055
        Color(0.15, 0.13, 0.10, 1)
        for dx in (0.36, 0.64):
            Ellipse(pos=(cx + cw * dx - er, cy + ey - er), size=(er * 2, er * 2))
        # a curved mouth, drawn as the lower arc of an ellipse
        mw, mh = cw * 0.30, ch * 0.22
        Line(ellipse=(cx + cw * 0.5 - mw / 2, cy + ch * 0.42 - mh / 2,
                      mw, mh, 100, 260), width=dp(2.0))
