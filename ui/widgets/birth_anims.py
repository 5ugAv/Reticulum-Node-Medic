"""Schematic animations for the guided birth wizard.

Deliberately simple vector shapes drawn on the Kivy canvas — enough to SHOW the
physical action (plug a board in, insert an SD card) so a first-time operator
knows what to do, with no image assets. A looping motion plays while the step is
on screen. These are placeholders for Sophie's polished artwork; the geometry and
timing are what we're getting right first.
"""

from __future__ import annotations

from kivy.animation import Animation
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme


class _LoopAnim(Widget):
    """Base: a ``phase`` 0→1 that loops while the step is shown. Subclasses draw
    themselves from ``phase`` in ``_draw``. Text is rendered with child Labels
    (repositioned each frame) since canvas text is awkward."""

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
        return lbl

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


def _medic(canvas_ctx, x, y, w, h):
    """Draw a 'Node Medic' body (rounded slab + red cross) at (x,y,w,h)."""
    Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
    RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(10)] * 4)
    Color(*theme.hex_to_rgba(theme.COLORS["red"]))
    t = min(w, h) * 0.14                       # cross arm thickness
    cx, cy = x + w / 2, y + h / 2
    arm = min(w, h) * 0.28
    RoundedRectangle(pos=(cx - t / 2, cy - arm), size=(t, 2 * arm), radius=[t / 2] * 4)
    RoundedRectangle(pos=(cx - arm, cy - t / 2), size=(2 * arm, t), radius=[t / 2] * 4)


class ConnectBoardAnim(_LoopAnim):
    """A small radio board slides right and plugs into the Node Medic's USB port."""

    def _draw(self):
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        mw, mh = w * 0.40, h * 0.62            # medic slab on the right
        mx, my = x + w - mw, cy - mh / 2
        with self.canvas:
            _medic(self.canvas, mx, my, mw, mh)
            # USB port notch on the medic's left face
            port_w, port_h = dp(10), dp(20)
            Color(*theme.hex_to_rgba(theme.COLORS["background"]))
            RoundedRectangle(pos=(mx - port_w, cy - port_h / 2),
                             size=(port_w, port_h), radius=[dp(2)] * 4)
            # the radio board travels from the left toward the port
            bw, bh = w * 0.24, h * 0.30
            start_x = x + w * 0.04
            dock_x = mx - port_w - bw - dp(6)
            p = min(1.0, self.phase / 0.85)
            bx = start_x + (dock_x - start_x) * p
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            RoundedRectangle(pos=(bx, cy - bh / 2), size=(bw, bh), radius=[dp(6)] * 4)
            # the plug + cable reaching to the port
            Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            plug_x = bx + bw
            Line(points=[plug_x, cy, mx - port_w, cy], width=dp(2))
            Color(*theme.hex_to_rgba(theme.COLORS["text_primary"]))
            RoundedRectangle(pos=(plug_x, cy - dp(6)), size=(dp(12), dp(12)),
                             radius=[dp(2)] * 4)
            # a little glow at the port once docked
            if self.phase > 0.85:
                Color(*theme.hex_to_rgba(theme.COLORS["green"], 0.9))
                Line(circle=(mx - port_w / 2, cy, dp(12)), width=dp(2))
        board = self._label("board", text="radio\nboard", font_size="13sp",
                            bold=True, halign="center", valign="middle",
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
        board.size = (bw, bh)                       # label rides the board rect
        board.pos = (bx, cy - bh / 2)
        medic = self._label("medic", text="NODE\nMEDIC", font_size="15sp",
                            bold=True, halign="center", valign="middle",
                            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        medic.size = (mw, dp(40))
        medic.pos = (mx, my - dp(44))


class InsertSdAnim(_LoopAnim):
    """An SD card slides into the Node Medic's card reader slot."""

    def _draw(self):
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        mw, mh = w * 0.44, h * 0.62
        mx, my = x + w - mw, cy - mh / 2
        slot_w, slot_h = dp(14), dp(46)
        with self.canvas:
            _medic(self.canvas, mx, my, mw, mh)
            # card-reader slot on the medic's left face
            Color(*theme.hex_to_rgba(theme.COLORS["background"]))
            RoundedRectangle(pos=(mx - slot_w, cy - slot_h / 2),
                             size=(slot_w, slot_h), radius=[dp(2)] * 4)
            # the SD card travels right into the slot (notched top-left corner)
            cw, ch = w * 0.20, h * 0.34
            start_x = x + w * 0.05
            dock_x = mx - slot_w - cw + dp(10)     # ends slightly inside the slot
            p = min(1.0, self.phase / 0.85)
            cx = start_x + (dock_x - start_x) * p
            Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
            RoundedRectangle(pos=(cx, cy - ch / 2), size=(cw, ch), radius=[dp(4)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["background"]))
            notch = dp(10)
            RoundedRectangle(pos=(cx, cy + ch / 2 - notch), size=(notch, notch))
            if self.phase > 0.85:
                Color(*theme.hex_to_rgba(theme.COLORS["green"], 0.9))
                Line(rectangle=(mx - slot_w, cy - slot_h / 2, slot_w, slot_h),
                     width=dp(1.6))
        medic = self._label("medic", text="NODE\nMEDIC", font_size="15sp",
                            bold=True, halign="center", valign="middle",
                            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        medic.size = (mw, dp(40))
        medic.pos = (mx, my - dp(44))
        card = self._label("card", text="SD", font_size="14sp", bold=True,
                          halign="center", valign="middle",
                          color=theme.hex_to_rgba(theme.COLORS["background"]))
        cw, ch = w * 0.20, h * 0.34
        start_x = x + w * 0.05
        dock_x = mx - slot_w - cw + dp(10)
        p = min(1.0, self.phase / 0.85)
        card.pos = (start_x + (dock_x - start_x) * p, cy - ch / 2)
        card.size = (cw, ch)
