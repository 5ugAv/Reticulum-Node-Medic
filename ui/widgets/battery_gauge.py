"""Small battery indicator for the home page — a drawn battery (no emoji glyph,
which the Pi font renders as tofu) with a proportional fill + a % label. Hidden
(opacity 0) until a UPS is actually present, so it never clutters a mains-only
medic. Green > 35%, amber 15–35%, red ≤ 15%, accent while charging.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme


class BatteryGauge(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.size = (dp(78), dp(30))
        self._percent = 0
        self._charging = False
        self._present = False
        self.opacity = 0
        self.label = Label(text="", font_size="12sp", bold=True,
                           halign="center", valign="middle")
        self.label.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.label)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def update(self, state):
        """Feed a monitor.ups.UpsState — shows/hides + repaints."""
        self._present = bool(getattr(state, "present", False))
        self._percent = int(getattr(state, "percent", 0))
        self._charging = bool(getattr(state, "charging", False))
        self.opacity = 1 if self._present else 0
        self._redraw()

    def _color(self):
        if self._charging:
            return theme.COLORS["accent"]
        p = self._percent
        if p <= 15:
            return theme.COLORS["red"]
        if p <= 35:
            return theme.COLORS["amber"]
        return theme.COLORS["green"]

    def _redraw(self, *_):
        from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
        self.canvas.before.clear()
        if not self._present:
            self.label.text = ""
            return
        x, y, w, h = self.x, self.y, self.width, self.height
        col = theme.hex_to_rgba(self._color())
        bw, bh = w * 0.5, h * 0.62                     # battery body (left half)
        bx, by = x + dp(2), y + (h - bh) / 2.0
        with self.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["background"], 0.55))
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(8)] * 4)
            Color(*col)
            Line(rounded_rectangle=(bx, by, bw, bh, dp(2)), width=1.3)   # outline
            Rectangle(pos=(bx + bw, by + bh * 0.3), size=(dp(2.5), bh * 0.4))  # nub
            fill_w = (bw - dp(3)) * max(0.05, min(1.0, self._percent / 100.0))
            Rectangle(pos=(bx + dp(1.5), by + dp(1.5)), size=(fill_w, bh - dp(3)))
        self.label.pos = (x + bw + dp(2), y)
        self.label.size = (w - bw - dp(2), h)
        self.label.color = col
        self.label.text = "%d%%" % self._percent
