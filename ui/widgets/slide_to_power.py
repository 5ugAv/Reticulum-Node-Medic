"""Slide-to-power-off — drag the red power knob across the track to shut down.

A deliberate gesture (not a tap) so the medic can't be powered off by accident.
Releasing before the end snaps back; reaching the end fires ``on_power_off``.
"""

from __future__ import annotations

import os

from kivy.animation import Animation
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from ui import theme

POWER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "power.png"))

_TRIGGER = 0.92          # fraction of the track that counts as "powered off"


class SlideToPowerOff(FloatLayout):
    def __init__(self, on_power_off=None, hint_text="slide to power off  →", **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(84))
        super().__init__(**kwargs)
        self._cb = on_power_off
        self._pad = dp(6)
        self._grab = False
        with self.canvas.before:
            self._track_c = Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            self._track = RoundedRectangle()
            self._fill_c = Color(*theme.hex_to_rgba(theme.COLORS["red"], 0))
            self._fill = RoundedRectangle()
        self.hint = Label(text=hint_text, bold=True,
                          color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        self.add_widget(self.hint)
        self.knob = Image(source=POWER, size_hint=(None, None), allow_stretch=True,
                          keep_ratio=True)
        self.knob.bind(pos=lambda *a: self._refresh())
        self.add_widget(self.knob)
        self.bind(pos=self._layout, size=self._layout)

    # -- geometry -----------------------------------------------------------
    # The track is a THIN bar (TRACK_FRAC of the widget height), vertically
    # centred, so the full-height round knob sits slightly PROUD of it.
    TRACK_FRAC = 0.64

    def _ks(self):
        return self.height                     # knob = full height -> proud of the track

    def _th(self):
        return self.height * self.TRACK_FRAC   # track height (thinner than the knob)

    def _ty(self):
        return self.y + (self.height - self._th()) / 2.0

    def _left(self):
        return self.x

    def _right(self):
        return self.right - self._ks()

    def _progress(self):
        span = self._right() - self._left()
        return 0.0 if span <= 0 else max(0.0, min(1.0, (self.knob.x - self._left()) / span))

    def _layout(self, *_):
        th, ty = self._th(), self._ty()
        r = th / 2.0
        self._track.pos, self._track.size, self._track.radius = (self.x, ty), (self.width, th), [r] * 4
        self.knob.size = (self._ks(), self._ks())
        if not self._grab:
            self.knob.pos = (self._left(), self.y)
        self.hint.pos, self.hint.size = (self.x, ty), (self.width, th)
        self.hint.text_size = (self.width, th)
        self.hint.halign, self.hint.valign = "center", "middle"
        self.hint.font_size = max(dp(9.5), th * 0.5)   # scales with the thin track
        self._refresh()

    def _refresh(self, *_):
        th, ty = self._th(), self._ty()
        r = th / 2.0
        w = max(th, self.knob.center_x - self.x)
        self._fill.pos, self._fill.size, self._fill.radius = (self.x, ty), (w, th), [r] * 4
        p = self._progress()
        self._fill_c.rgba = theme.hex_to_rgba(theme.COLORS["red"], min(1.0, p * 1.1))
        self.hint.opacity = max(0.0, 1.0 - p * 1.4)

    # -- drag ---------------------------------------------------------------
    def on_touch_down(self, touch):
        if self.knob.collide_point(*touch.pos):
            self._grab = True
            touch.grab(self)
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            x = max(self._left(), min(self._right(), touch.x - self._ks() / 2.0))
            self.knob.pos = (x, self.y)
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._grab = False
            if self._progress() >= _TRIGGER:
                self.knob.x = self._right()
                self.hint.text = "powering off…"
                self.hint.opacity = 1
                self.hint.color = theme.hex_to_rgba(theme.COLORS["text_primary"])
                if self._cb:
                    self._cb()
            else:
                Animation(x=self._left(), y=self.y, d=0.22,
                          t="out_quad").start(self.knob)
            return True
        return super().on_touch_up(touch)
