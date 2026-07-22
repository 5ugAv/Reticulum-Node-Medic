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
SD_READER_PNG = os.path.join(_ANIM_DIR, "sd_reader.png")           # microSD + card reader
SD_PNG = os.path.join(_ANIM_DIR, "sd_card.png")
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
    """An SD card slides into the Node Medic's card reader slot."""

    def _draw(self):
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        mw, mh = w * 0.44, h * 0.62
        mx, my = x + w - mw, cy - mh / 2
        cw, ch = w * 0.20, h * 0.34
        start_x = x + w * 0.05
        dock_x = mx - cw + dp(12)                    # ends slightly inside the medic
        p = min(1.0, self.phase / 0.85)
        cx = start_x + (dock_x - start_x) * p
        medic_tex, sd_tex = _texture(MEDIC_PNG), _texture(SD_PNG)
        with self.canvas:
            if medic_tex:
                Color(1, 1, 1, 1)
                Rectangle(texture=medic_tex, pos=(mx, my), size=(mw, mh))
            else:
                _draw_medic_vector(mx, my, mw, mh)
                slot_w, slot_h = dp(14), dp(46)      # reader slot (fallback only)
                Color(*theme.hex_to_rgba(theme.COLORS["background"]))
                RoundedRectangle(pos=(mx - slot_w, cy - slot_h / 2),
                                 size=(slot_w, slot_h), radius=[dp(2)] * 4)
            if sd_tex:
                Color(1, 1, 1, 1)
                Rectangle(texture=sd_tex, pos=(cx, cy - ch / 2), size=(cw, ch))
            else:
                Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
                RoundedRectangle(pos=(cx, cy - ch / 2), size=(cw, ch), radius=[dp(4)] * 4)
                Color(*theme.hex_to_rgba(theme.COLORS["background"]))
                notch = dp(10)                       # SD's cut corner
                RoundedRectangle(pos=(cx, cy + ch / 2 - notch), size=(notch, notch))
            if self.phase > 0.85:                    # docked glow
                Color(*theme.hex_to_rgba(theme.COLORS["green"], 0.9))
                Line(circle=(mx, cy, dp(12)), width=dp(2))
        if medic_tex:
            self._hide_label("medic")
        else:
            medic = self._label("medic", text="NODE\nMEDIC", font_size="15sp",
                                bold=True, halign="center", valign="middle",
                                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            medic.size = (mw, dp(40))
            medic.pos = (mx, my - dp(44))
        if sd_tex:
            self._hide_label("card")
        else:
            card = self._label("card", text="SD", font_size="14sp", bold=True,
                              halign="center", valign="middle",
                              color=theme.hex_to_rgba(theme.COLORS["background"]))
            card.size = (cw, ch)
            card.pos = (cx, cy - ch / 2)
