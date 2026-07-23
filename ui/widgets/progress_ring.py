"""A circular progress ring that FILLS with a percentage — a determinate,
reassuring alternative to an indeterminate 'spinning wheel of doom'."""

from __future__ import annotations

from kivy.graphics import Color, Line
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme


class ProgressRing(Widget):
    """A ring that fills clockwise from the top as ``fraction`` (0..1) rises, with
    the percentage in the centre."""

    fraction = NumericProperty(0.0)

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(66), dp(66)))
        super().__init__(**kwargs)
        self._label = Label(text="0%", bold=True, font_size="15sp",
                            color=theme.hex_to_rgba(theme.COLORS["accent"]))
        self.add_widget(self._label)
        self.bind(pos=self._draw, size=self._draw, fraction=self._draw)
        self._draw()

    def set_fraction(self, f):
        self.fraction = max(0.0, min(1.0, f))

    def _draw(self, *_):
        self.canvas.clear()
        r = min(self.width, self.height) / 2.0 - dp(4)
        if r <= 0:
            return
        cx, cy = self.center
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            Line(circle=(cx, cy, r), width=dp(5))                  # track
            if self.fraction > 0.001:
                Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
                Line(circle=(cx, cy, r, 0, self.fraction * 360.0), width=dp(5))
        self._label.center = self.center
        self._label.text = f"{int(round(self.fraction * 100))}%"
