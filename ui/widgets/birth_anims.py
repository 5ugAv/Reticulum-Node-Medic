"""Animations for the guided birth wizard.

Each animation shows the physical action (plug a board in, insert an SD card) with
a looping motion. If a cartoon PNG is present in ``assets/ui/anim/`` it's used;
otherwise a schematic vector fallback draws in its place — so the flow works now
and simply gets prettier when the artwork is dropped in (no code change):

    assets/ui/anim/node_medic.png    # the Node Medic body
    assets/ui/anim/sd_card.png       # the SD card
    assets/ui/anim/radio_board.png   # the radio board

Sophie's artwork replaces the placeholders by filename.
"""

from __future__ import annotations

import math
import os

from kivy.animation import Animation
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme

_ANIM_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "anim"))
MEDIC_PNG = os.path.join(_ANIM_DIR, "node_medic.png")             # angled medic (SD step)
MEDIC_CABLE_PNG = os.path.join(_ANIM_DIR, "node_medic_cable.png")  # medic w/ USB cable
LORA_PNG = os.path.join(_ANIM_DIR, "lora32.png")                   # the radio board
SD_READER_PNG = os.path.join(_ANIM_DIR, "sd_reader.png")           # microSD + card reader (combined)
SD_READER_BODY_PNG = os.path.join(_ANIM_DIR, "sd_reader_body.png")  # card reader alone
SD_PNG = os.path.join(_ANIM_DIR, "sd_card.png")                    # the microSD card alone
BOARD_PNG = os.path.join(_ANIM_DIR, "radio_board.png")

#: The medic's USB plug tip within node_medic_cable.png (normalised, from top-left).
#: The board's bottom USB port descends onto this point.
_PLUG_TIP = (0.041, 0.583)

_TEX_CACHE: dict = {}


def _texture(path):
    """A GL texture for *path*, or None if the file is absent/unreadable. Cached
    (per run) so the placeholder check isn't repeated every frame."""
    if path in _TEX_CACHE:
        return _TEX_CACHE[path]
    tex = None
    if path and os.path.exists(path):
        try:
            from kivy.core.image import Image as CoreImage
            tex = CoreImage(path).texture
        except Exception:
            tex = None
    _TEX_CACHE[path] = tex
    return tex


class _LoopAnim(Widget):
    """Base: a ``phase`` 0→1 that loops while the step is shown. Subclasses draw
    themselves from ``phase`` in ``_draw``. Text (fallback labels) uses child
    Labels repositioned each frame since canvas text is awkward."""

    phase = NumericProperty(0.0)

    def __init__(self, duration=2.2, **kwargs):
        super().__init__(**kwargs)
        self._duration = duration
        self._anim = None
        self._labels = {}
        self.bind(phase=self._redraw, pos=self._redraw, size=self._redraw)

    def _label(self, key, **kw):
        lbl = self._labels.get(key)
        if lbl is None:
            lbl = Label(**kw)
            lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
            self._labels[key] = lbl
            self.add_widget(lbl)
        lbl.opacity = 1
        return lbl

    def _hide_label(self, key):
        lbl = self._labels.get(key)
        if lbl is not None:
            lbl.opacity = 0

    def start(self):
        self.stop()
        self.phase = 0.0
        self._anim = Animation(phase=1.0, duration=self._duration, t="in_out_sine")
        self._anim.repeat = True
        self._anim.start(self)

    def stop(self):
        if self._anim is not None:
            self._anim.cancel(self)
            self._anim = None

    def _redraw(self, *_):
        self.canvas.clear()
        if self.width < dp(40) or self.height < dp(40):
            return
        self._draw()

    def _draw(self):
        raise NotImplementedError


def _draw_medic_vector(x, y, w, h):
    """Fallback Node Medic: a rounded slab with a red cross."""
    Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
    RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(10)] * 4)
    Color(*theme.hex_to_rgba(theme.COLORS["red"]))
    t = min(w, h) * 0.14
    cx, cy = x + w / 2, y + h / 2
    arm = min(w, h) * 0.28
    RoundedRectangle(pos=(cx - t / 2, cy - arm), size=(t, 2 * arm), radius=[t / 2] * 4)
    RoundedRectangle(pos=(cx - arm, cy - t / 2), size=(2 * arm, t), radius=[t / 2] * 4)


class ConnectBoardAnim(_LoopAnim):
    """The LoRa32 radio board descends from above onto the Node Medic's USB plug.
    Uses the illustrated sprites (medic-with-cable on the right, board small on the
    left, docking on the plug tip); falls back to a schematic if the art is absent."""

    burst = NumericProperty(0.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connected = False
        self.bind(burst=self._redraw)

    def mark_connected(self):
        """The medic sensed a board on USB — stop looping, dock the board, and fire
        a one-shot green 'Connected!' burst from the plug/board junction."""
        if self._connected:
            return
        self._connected = True
        self.stop()                                   # halt the descend loop
        self.phase = 1.0                              # freeze the board docked
        Animation(burst=1.0, duration=0.8, t="out_quad").start(self)

    def _draw(self):
        medic_tex, board_tex = _texture(MEDIC_CABLE_PNG), _texture(LORA_PNG)
        if medic_tex is None or board_tex is None:
            return self._draw_fallback()
        x, y, w, h = self.x, self.y, self.width, self.height
        # medic: anchored right, scaled to fill the height (capped so it never
        # eats more than 60% of the width), aspect preserved.
        ma = medic_tex.width / float(medic_tex.height)
        mh = h * 0.96
        mw = mh * ma
        if mw > w * 0.60:
            mw = w * 0.60
            mh = mw / ma
        mx = x + w - mw - dp(4)
        my = y + (h - mh) / 2.0
        tipx = mx + _PLUG_TIP[0] * mw
        tipy = my + (1.0 - _PLUG_TIP[1]) * mh        # norm-from-top -> kivy y-up
        # board: small (¼ the medic height), bottom USB port descends onto the tip
        ba = board_tex.width / float(board_tex.height)
        bh = mh * 0.25
        bw = bh * ba
        p = min(1.0, self.phase / 0.9)
        start_y = tipy + h * 0.42                     # begins above, moves down
        by = start_y - (start_y - tipy) * p
        bx = tipx - bw / 2.0
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=medic_tex, pos=(mx, my), size=(mw, mh))
            Color(1, 1, 1, 1)
            Rectangle(texture=board_tex, pos=(bx, by), size=(bw, bh))
            if self._connected:                      # green burst from the junction
                fade = max(0.0, 1.0 - self.burst)
                Color(0.2, 0.9, 0.4, fade)
                Line(circle=(tipx, tipy, dp(8) + self.burst * dp(54)), width=dp(3.5))
                Color(0.2, 0.9, 0.4, fade * 0.55)
                Line(circle=(tipx, tipy, dp(8) + self.burst * dp(32)), width=dp(2.5))
        if self._connected:
            lbl = self._label("connected", text="Connected!", font_size="21sp",
                              bold=True, halign="center", valign="middle",
                              color=theme.hex_to_rgba(theme.COLORS["green"]))
            lbl.size = (dp(180), dp(30))
            lbl.pos = (tipx - dp(90), tipy - dp(50))
        else:
            self._hide_label("connected")
        self._hide_label("medic")
        self._hide_label("board")

    def _draw_fallback(self):
        """Schematic (no art): a board slides in from the left into the medic."""
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        mw, mh = w * 0.40, h * 0.62
        mx, my = x + w - mw, cy - mh / 2
        bw, bh = w * 0.24, h * 0.30
        start_x = x + w * 0.04
        dock_x = mx - dp(16) - bw
        bx = start_x + (dock_x - start_x) * min(1.0, self.phase / 0.85)
        with self.canvas:
            _draw_medic_vector(mx, my, mw, mh)
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            RoundedRectangle(pos=(bx, cy - bh / 2), size=(bw, bh), radius=[dp(6)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            Line(points=[bx + bw, cy, mx, cy], width=dp(2))
        board = self._label("board", text="radio\nboard", font_size="13sp",
                            bold=True, halign="center", valign="middle",
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
        board.size = (bw, bh)
        board.pos = (bx, cy - bh / 2)


class InsertSdAnim(_LoopAnim):
    """Two-phase: the microSD card slides into the card reader, then the reader +
    card move together toward the Node Medic (it has no native card slot). Uses the
    illustrated sprites; falls back to a schematic if the art is absent."""

    # card position RELATIVE to the reader top-left, in the SOURCE image px the
    # sprites were cut from (y DOWN): start = card sitting below-left of the reader;
    # inserted = slid up into the slot. Scaled by the reader's screen scale.
    _CARD_START = (-235.0, 618.0)
    _CARD_IN = (-30.0, 250.0)

    def __init__(self, **kwargs):
        kwargs.setdefault("duration", 3.8)           # three phases -> a touch slower
        super().__init__(**kwargs)

    def _blit(self, tex, tlx, tly, w, h):
        """Draw *tex* given its TOP-LEFT in a y-DOWN widget frame (0,0 = top-left)."""
        Color(1, 1, 1, 1)
        Rectangle(texture=tex, pos=(self.x + tlx, self.y + self.height - tly - h),
                  size=(w, h))

    def _kv(self, x, y_down):
        """y-DOWN widget point -> Kivy (y-up) window point."""
        return (self.x + x, self.y + self.height - y_down)

    def _draw(self):
        medic = _texture(MEDIC_PNG)
        reader = _texture(SD_READER_BODY_PNG)
        card = _texture(SD_PNG)
        if medic is None or reader is None or card is None:
            return self._draw_fallback()
        w, h = self.width, self.height
        # medic anchored upper-right (smaller, so the arrow has room to U-turn below)
        ma = medic.width / float(medic.height)
        mh = h * 0.64
        mw = mh * ma
        if mw > w * 0.42:
            mw = w * 0.42
            mh = mw / ma
        m_tlx, m_tly = w - mw - dp(6), 0.03 * h
        # reader + card, left / upper-middle; card scaled by the same source scale
        s = (0.34 * h) / reader.height
        rw, rh = reader.width * s, reader.height * s
        cw, ch = card.width * s, card.height * s
        rtx, rty = 0.05 * w, 0.20 * h
        start_rel = (self._CARD_START[0] * s, self._CARD_START[1] * s)
        in_rel = (self._CARD_IN[0] * s, self._CARD_IN[1] * s)
        # phase 1 (0-0.35): card slides into the reader
        t1 = min(1.0, self.phase / 0.35)
        crel = (start_rel[0] + (in_rel[0] - start_rel[0]) * t1,
                start_rel[1] + (in_rel[1] - start_rel[1]) * t1)
        # arrow: reader-bottom -> DOWN -> U-turn -> UP to the medic's bottom-centre
        S = (rtx + rw * 0.5, rty + rh)
        E = (m_tlx + mw * 0.5, m_tly + mh)
        C1, C2 = (S[0], 0.93 * h), (E[0], 0.93 * h)

        def bez(t):
            u = 1 - t
            return (u ** 3 * S[0] + 3 * u * u * t * C1[0] + 3 * u * t * t * C2[0] + t ** 3 * E[0],
                    u ** 3 * S[1] + 3 * u * u * t * C1[1] + 3 * u * t * t * C2[1] + t ** 3 * E[1])

        with self.canvas:
            self._blit(medic, m_tlx, m_tly, mw, mh)
            self._blit(reader, rtx, rty, rw, rh)
            self._blit(card, rtx + crel[0], rty + crel[1], cw, ch)
            if self.phase > 0.4:                      # phase 2 (0.4-0.85): arrow travels
                q = min(1.0, (self.phase - 0.4) / 0.45)
                n = 48
                k = max(2, int(n * q))
                pts = []
                for i in range(k + 1):
                    pts += list(self._kv(*bez(i / n)))
                Color(*theme.hex_to_rgba(theme.COLORS["red"]))   # red — Node Medic scheme
                Line(points=pts, width=dp(3.4), joint="round", cap="round")
                ex, ey = bez(k / n)                   # arrowhead along the tangent
                px, py = bez((k - 1) / n)
                ang = math.atan2(ey - py, ex - px)
                tip = self._kv(ex, ey)
                for a in (ang + 2.5, ang - 2.5):
                    barb = self._kv(ex - dp(13) * math.cos(a), ey - dp(13) * math.sin(a))
                    Line(points=[tip[0], tip[1], barb[0], barb[1]],
                         width=dp(3.4), cap="round")
                # phase 3 (>0.85): flash a ring at the target
                if self.phase > 0.85 and int((self.phase - 0.85) / 0.04) % 2 == 0:
                    ecx, ecy = self._kv(*E)
                    Color(1.0, 0.4, 0.4, 1)           # bright red flash ring
                    Line(circle=(ecx, ecy, dp(11)), width=dp(3))
        self._hide_label("medic")
        self._hide_label("card")

    def _draw_fallback(self):
        """Schematic (no art): an SD card slides into the medic."""
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        mw, mh = w * 0.44, h * 0.62
        mx, my = x + w - mw, cy - mh / 2
        cw, ch = w * 0.20, h * 0.34
        start_x = x + w * 0.05
        cx = start_x + (mx - cw + dp(12) - start_x) * min(1.0, self.phase / 0.85)
        with self.canvas:
            _draw_medic_vector(mx, my, mw, mh)
            Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
            RoundedRectangle(pos=(cx, cy - ch / 2), size=(cw, ch), radius=[dp(4)] * 4)
        card = self._label("card", text="SD", font_size="14sp", bold=True,
                          halign="center", valign="middle",
                          color=theme.hex_to_rgba(theme.COLORS["background"]))
        card.size = (cw, ch)
        card.pos = (cx, cy - ch / 2)
