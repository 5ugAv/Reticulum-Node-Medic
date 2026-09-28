"""Slide-to-power-off — drag the power knob across the track to shut down.

A deliberate gesture (not a tap) so the medic can't be powered off by accident.
Releasing before the end snaps back; reaching the end fires ``on_power_off``.

The knob is the green button the operator supplied on 2026-09-29, cut out of
their artwork; it replaced the old red one, which was the last thing on the
front page still speaking the pre-2026-09-28 visual language. The capsule it
rides in ("neon") is DRAWN rather than photographed: every text-free strip in
that artwork sits too close to the "ON" glyph, so filling the hole the knob
left behind streaked, and a drawn capsule also scales to any panel without
resampling.
"""

from __future__ import annotations

import os

from kivy.animation import Animation
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from ui import theme
from ui.power_slide_layout import STYLES, hint_font_px, hint_rect, track_rect

_ASSETS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui"))
POWER = os.path.join(_ASSETS, "power_knob.png")
#: The red button this replaced, kept on disk: it is still the right knob for
#: the imager's "slide to wipe", which is a destructive act and says so in red.
POWER_RED = os.path.join(_ASSETS, "power.png")

_TRIGGER = 0.92          # fraction of the track that counts as "powered off"


class SlideToPowerOff(FloatLayout):
    def __init__(self, on_power_off=None, hint_text="slide to power off  →",
                 track="pill", knob_image=None, **kwargs):
        """*track*: "pill" (a rounded bar that carries a sentence — the
        imager's "slide to wipe") or "line" (a thin BLACK line the unchanged
        red knob rides along — the front page's OFF, operator 2026-09-22:
        "instead of having that big oval shaped thing that the off button
        slides across ... it will just slide on a thin black line")."""
        if track not in STYLES:
            raise ValueError(track)
        # The green knob everywhere except the destructive slides, which keep
        # the red one — the colour is the warning, and a green button that
        # wipes a card would be the wrong picture.
        knob_src = knob_image or (POWER if track == "neon" else POWER_RED)
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(84))
        super().__init__(**kwargs)
        self._cb = on_power_off
        self._style = track
        self._pad = dp(6)
        self._grab = False
        self._slid = "green" if track == "neon" else "red"
        with self.canvas.before:
            self._track_c = Color(*theme.hex_to_rgba(
                theme.COLORS["black" if track in ("line", "neon") else "surface"]))
            self._track = RoundedRectangle()
            self._fill_c = Color(*theme.hex_to_rgba(theme.COLORS[self._slid], 0))
            self._fill = RoundedRectangle()
            # the capsule's lit rim — drawn, so it holds at any panel size
            self._rim_c = Color(*theme.hex_to_rgba(
                theme.COLORS["green"], 1 if track == "neon" else 0))
            self._rim = Line(width=dp(1.6))
        # The word takes the capsule's own colour: grey secondary text inside a
        # lit green capsule reads as a disabled control rather than the place
        # the knob is going.
        # size_hint (None, None) IS THE FIX, not a detail. A Label defaults to
        # size_hint (1, 1), so FloatLayout's own pass overwrote the box that
        # _layout had just computed: the label kept its left edge and was
        # stretched to the full control width, which moved the centred text
        # right by half the difference. On the front page that pushed OFF out
        # past the end of the capsule (operator photo, 2026-09-29). Every style
        # was affected; only the capsule made it obvious.
        self.hint = Label(text=hint_text, bold=True,
                          size_hint=(None, None),
                          color=theme.hex_to_rgba(theme.COLORS[
                              "green" if track == "neon" else "text_secondary"]))
        self.add_widget(self.hint)
        self.knob = Image(source=knob_src, size_hint=(None, None),
                          allow_stretch=True, keep_ratio=True)
        self.knob.bind(pos=lambda *a: self._refresh())
        self.add_widget(self.knob)
        self.bind(pos=self._layout, size=self._layout)

    # -- geometry (ui/power_slide_layout — pure, previewable) ---------------
    # The track is thinner than the knob and vertically centred, so the
    # full-height round knob sits proud of it: a pill, or a thin line.

    def _ks(self):
        return self.height                     # knob = full height -> proud of the track

    def _track_geom(self):
        # NOT _track: that name is the canvas RoundedRectangle set in
        # __init__, and a method of the same name is shadowed by it — the
        # first layout raised "RoundedRectangle is not callable" and the
        # medic's UI went down on deploy (2026-09-22). test_no_method_is_
        # shadowed_by_an_attribute now forbids the pattern tool-wide.
        return track_rect(self.x, self.y, self.width, self.height, self._style)

    def _th(self):
        return self._track_geom()[3]

    def _ty(self):
        return self._track_geom()[1]

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
        self._rim.rounded_rectangle = (self.x + dp(1), ty + dp(1),
                                       max(dp(2), self.width - dp(2)),
                                       max(dp(2), th - dp(2)), r)
        self.knob.size = (self._ks(), self._ks())
        if not self._grab:
            self.knob.pos = (self._left(), self.y)
        # The label sits right of the resting knob so the knob never hides
        # it: inside the pill, or ON the line in a face the line cannot give.
        hx, hy, hw, hh = hint_rect(self.x, self.y, self.width, self.height,
                                   self._ks(), self._style)
        self.hint.pos, self.hint.size = (hx, hy - dp(3)), (hw, hh)
        self.hint.text_size = (hw, hh)
        self.hint.halign, self.hint.valign = "center", "middle"
        self.hint.font_size = hint_font_px(self.height, self._style, dp(9.5))
        self._refresh()

    def _refresh(self, *_):
        th, ty = self._th(), self._ty()
        r = th / 2.0
        w = max(th, self.knob.center_x - self.x)
        self._fill.pos, self._fill.size, self._fill.radius = (self.x, ty), (w, th), [r] * 4
        p = self._progress()
        self._fill_c.rgba = theme.hex_to_rgba(theme.COLORS[self._slid],
                                              min(1.0, p * 1.1))
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
