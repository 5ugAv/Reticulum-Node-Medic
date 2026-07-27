"""A 24-hour activity histogram — when a node is typically HEARD, by hour of day.

Drawn on canvas (no font glyphs, which the Pi font renders as tofu). Bars are
scaled to the busiest hour; a faint baseline and quarter-day tick lines (6/12/18)
give the shape context. Feed it ``by_hour`` — a 24-int list from
``monitor.history.activity_profile``.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.widget import Widget

from ui import theme


class ActivityChart(Widget):
    def __init__(self, by_hour=None, **kwargs):
        super().__init__(**kwargs)
        self.by_hour = list(by_hour) if by_hour else [0] * 24
        self.size_hint_y = None
        self.height = dp(92)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def set_data(self, by_hour):
        self.by_hour = list(by_hour) if by_hour else [0] * 24
        self._redraw()

    def _redraw(self, *_):
        from kivy.graphics import Color, Line, Rectangle
        self.canvas.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        if w < 2 or h < 2:
            return
        base = y + dp(2)
        chart_h = h - dp(6)
        n = 24
        gap = dp(2)
        bw = max(dp(1), (w - (n + 1) * gap) / n)
        mx = max(self.by_hour) or 1
        with self.canvas:
            # quarter-day tick lines (6h / 12h / 18h) for orientation
            Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.18))
            for hour in (6, 12, 18):
                tx = x + gap + hour * (bw + gap)
                Line(points=[tx, base, tx, base + chart_h], width=1)
            # baseline
            Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.35))
            Line(points=[x, base, x + w, base], width=1)
            # bars
            for i, v in enumerate(self.by_hour):
                if not v:
                    continue
                bx = x + gap + i * (bw + gap)
                bh = (v / mx) * chart_h
                Color(*theme.hex_to_rgba(theme.COLORS["accent"], 0.9))
                Rectangle(pos=(bx, base), size=(bw, bh))
