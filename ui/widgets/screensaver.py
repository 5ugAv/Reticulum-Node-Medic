"""Screen-saver overlay + styles.

A full-window overlay (added on top of the ScreenManager) that plays a moving
pattern so nothing static burns into the always-on panel. Any touch dismisses it.
First style: a 50's hypnotic swirl — a rotating black Archimedean spiral on an
off-white ground. Styles are a registry so more can be added (mirror
provisioning.screensaver.STYLES).
"""

from __future__ import annotations

import math

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import (Color, Line, PopMatrix, PushMatrix, Rectangle, Rotate)
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget

#: 50's palette — warm off-white ground, near-black ink.
_OFF_WHITE = (0.94, 0.92, 0.84, 1)
_INK = (0.09, 0.09, 0.08, 1)


class SwirlSaver(Widget):
    """A hypnotic spiral (black on off-white) SPINNING slowly + smoothly about the
    centre. The spiral geometry is drawn ONCE; only a GPU Rotate is animated each
    frame (cheap on the Pi), so it turns continuously without recomputing points."""

    def __init__(self, turns: int = 9, period: float = 14.0, **kwargs):
        super().__init__(**kwargs)
        self._turns = turns
        self._period = period                             # seconds per full turn
        self._angle = 0.0
        self._rot = None
        self._ev = None
        self.bind(size=self._rebuild, pos=self._rebuild)

    def start(self):
        self.stop()
        self._rebuild()
        self._ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    def stop(self):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def _tick(self, dt):
        # advance the rotation continuously (deg/sec = 360 / period); wrap at 360
        self._angle = (self._angle + (360.0 / self._period) * dt) % 360.0
        if self._rot is not None:
            self._rot.angle = self._angle
            self._rot.origin = self.center

    def _rebuild(self, *_):
        self.canvas.clear()
        w, h = self.size
        if w < 2 or h < 2:
            return
        cx, cy = self.center
        radius = (math.hypot(w, h) / 2.0) * 1.08          # reach the corners
        turns = self._turns
        steps = turns * 80
        spacing = radius / turns
        width = spacing * 0.30                             # black band with cream gaps
        pts = []
        for i in range(steps + 1):
            f = i / steps
            t = f * turns * 2 * math.pi
            r = radius * f
            pts += [cx + r * math.cos(t), cy + r * math.sin(t)]
        with self.canvas:
            Color(*_OFF_WHITE)
            Rectangle(pos=self.pos, size=self.size)
            PushMatrix()
            self._rot = Rotate(angle=self._angle, origin=(cx, cy))
            Color(*_INK)
            Line(points=pts, width=max(dp(4), width), joint="round", cap="round")
            PopMatrix()


#: style key -> widget class (mirror provisioning.screensaver.STYLES).
STYLES = {"swirl": SwirlSaver}


class Screensaver(FloatLayout):
    """Full-window overlay. ``show(style)`` mounts + starts it on the Window; any
    touch calls ``on_dismiss`` and ``hide()`` tears it down."""

    def __init__(self, on_dismiss=None, **kwargs):
        super().__init__(**kwargs)
        self._on_dismiss = on_dismiss
        self.active = False
        self._saver = None

    def show(self, style: str = "swirl"):
        if self.active:
            return
        self.clear_widgets()
        cls = STYLES.get(style, SwirlSaver)
        self._saver = cls(size_hint=(None, None))
        self.add_widget(self._saver)
        self.size = Window.size
        self.pos = (0, 0)
        if self.parent is None:
            Window.add_widget(self)
        Window.bind(size=self._resize)
        self._resize()
        self._saver.start()
        self.active = True

    def _resize(self, *_):
        self.size = Window.size
        self.pos = (0, 0)
        if self._saver is not None:
            self._saver.size = Window.size
            self._saver.pos = (0, 0)

    def hide(self):
        if not self.active:
            return
        if self._saver is not None:
            self._saver.stop()
        Window.unbind(size=self._resize)
        if self.parent is not None:
            Window.remove_widget(self)
        self.active = False

    def on_touch_down(self, touch):
        if self.active:
            if self._on_dismiss:
                self._on_dismiss()
            return True                                   # swallow the wake tap
        return super().on_touch_down(touch)
