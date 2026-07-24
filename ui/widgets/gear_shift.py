"""A 'gear-shift' override gesture.

Drag the knob RIGHT (~½"), then DOWN (~¼"), then RIGHT again (~½") through an
H-gate. Because the middle leg only advances on DOWNWARD motion, a single straight
swipe can't complete it — the operator has to make the deliberate dog-leg, like
shifting a car into gear. Fires ``on_complete`` at the end; releasing early snaps
the knob back to the start. Used for the power-off override during a flash, where
an accidental trigger could brick a board.
"""

from __future__ import annotations

import os

from kivy.core.image import Image as CoreImage
from kivy.metrics import dp
from kivy.graphics import Color, Ellipse, Line, Rectangle
from kivy.uix.widget import Widget

from ui import theme

#: Reuse the front-page power-off button (already round-masked, transparent
#: corners) so the shutdown knob is consistent everywhere.
_KNOB_PNG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "ui", "power.png")
_knob_tex = None


def _knob_texture():
    """The red power-button knob image, decoded once. None if the asset is absent
    (then a plain red disc is drawn)."""
    global _knob_tex
    if _knob_tex is None and os.path.exists(_KNOB_PNG):
        try:
            _knob_tex = CoreImage(_KNOB_PNG).texture
        except Exception:
            _knob_tex = None
    return _knob_tex


class GearShiftOverride(Widget):
    def __init__(self, on_complete=None, **kwargs):
        super().__init__(**kwargs)
        self._on_complete = on_complete
        self.L1 = dp(80)                 # right  ~½ inch
        self.L2 = dp(40)                 # down   ~¼ inch
        self.L3 = dp(80)                 # right  ~½ inch
        self.knob_r = dp(19)
        self._margin = dp(18)
        self.size_hint = (None, None)
        self.width = self._margin * 2 + self.L1 + self.L3 + self.knob_r * 2
        self.height = self._margin * 2 + self.L2 + self.knob_r * 2
        self._progress = 0.0
        self._done = False
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    @property
    def _total(self):
        return self.L1 + self.L2 + self.L3

    def _start_pt(self):
        x0 = self.x + self._margin + self.knob_r
        y_top = self.y + self.height - self._margin - self.knob_r
        return x0, y_top

    def _point_at(self, p):
        x0, y_top = self._start_pt()
        if p <= self.L1:
            return (x0 + p, y_top)
        p -= self.L1
        if p <= self.L2:
            return (x0 + self.L1, y_top - p)
        p -= self.L2
        return (x0 + self.L1 + min(p, self.L3), y_top - self.L2)

    def _dir_at(self, p):
        if p < self.L1:
            return (1, 0)                # right (kivy +x)
        if p < self.L1 + self.L2:
            return (0, -1)               # down  (kivy -y)
        return (1, 0)                    # right

    def _path_to(self, p):
        """Polyline from the start to the knob at progress *p* (for the fill)."""
        x0, y_top = self._start_pt()
        pts = [x0, y_top]
        if p > self.L1:
            pts += [x0 + self.L1, y_top]
        if p > self.L1 + self.L2:
            pts += [x0 + self.L1, y_top - self.L2]
        kx, ky = self._point_at(p)
        pts += [kx, ky]
        return pts

    def _redraw(self, *_):
        self.canvas.clear()
        x0, y_top = self._start_pt()
        gate = [x0, y_top, x0 + self.L1, y_top, x0 + self.L1, y_top - self.L2,
                x0 + self.L1 + self.L3, y_top - self.L2]
        ex, ey = self._point_at(self._total)
        kx, ky = self._point_at(self._progress)
        with self.canvas:
            # channel
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            Line(points=gate, width=self.knob_r, joint="round", cap="round")
            Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.45))
            Line(points=gate, width=dp(1.4), joint="round", cap="round")
            # end target ring
            Color(*theme.hex_to_rgba(theme.COLORS["red"], 0.5))
            Line(circle=(ex, ey, self.knob_r * 0.9), width=dp(2))
            # traversed fill (red, danger)
            if self._progress > 1:
                Color(*theme.hex_to_rgba(theme.COLORS["red"], 0.85))
                Line(points=self._path_to(self._progress),
                     width=self.knob_r * 0.7, joint="round", cap="round")
            # knob = the red power-button icon (falls back to a plain red disc)
            ktex = _knob_texture()
            if ktex is not None:
                kd = self.knob_r * 2.6
                Color(1, 1, 1, 1)
                Rectangle(texture=ktex, pos=(kx - kd / 2, ky - kd / 2), size=(kd, kd))
            else:
                Color(*theme.hex_to_rgba(theme.COLORS["red"]))
                Ellipse(pos=(kx - self.knob_r, ky - self.knob_r),
                        size=(self.knob_r * 2, self.knob_r * 2))
                Color(1, 1, 1, 0.9)
                Ellipse(pos=(kx - dp(4), ky - dp(4)), size=(dp(8), dp(8)))

    # -- gesture ------------------------------------------------------------
    def on_touch_down(self, touch):
        if self._done:
            return super().on_touch_down(touch)
        kx, ky = self._point_at(self._progress)
        if (touch.x - kx) ** 2 + (touch.y - ky) ** 2 <= (self.knob_r * 1.9) ** 2:
            touch.grab(self)
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)
        dx, dy = self._dir_at(self._progress)
        self._progress = max(0.0, min(self._total,
                                      self._progress + touch.dx * dx + touch.dy * dy))
        self._redraw()
        if self._progress >= self._total - 0.5 and not self._done:
            self._done = True
            touch.ungrab(self)
            if self._on_complete:
                self._on_complete()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if not self._done:            # released early -> snap back to start
                self._progress = 0.0
                self._redraw()
            return True
        return super().on_touch_up(touch)
