"""A simple rotating-arc spinner — 'something is happening' feedback for long
operations (a firmware flash compiles for minutes with no per-line output)."""

from __future__ import annotations

from kivy.animation import Animation
from kivy.graphics import Color, Line
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.widget import Widget

from ui import theme


class SpinnerWheel(Widget):
    """A 270° accent arc that rotates while running. Call ``start()`` / ``stop()``."""

    angle = NumericProperty(0)

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(30), dp(30)))
        super().__init__(**kwargs)
        self._anim = None
        self.bind(pos=self._draw, size=self._draw, angle=self._draw)
        self._draw()

    def start(self):
        self.stop()
        self._anim = Animation(angle=360, duration=0.9)
        self._anim.repeat = True
        self._anim.start(self)

    def stop(self):
        if self._anim is not None:
            self._anim.cancel(self)
            self._anim = None

    def _draw(self, *_):
        self.canvas.clear()
        r = min(self.width, self.height) / 2.0 - dp(3)
        if r <= 0:
            return
        cx, cy = self.center
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            Line(circle=(cx, cy, r), width=dp(3))          # faint full ring
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            Line(circle=(cx, cy, r, self.angle, self.angle + 270), width=dp(3))
