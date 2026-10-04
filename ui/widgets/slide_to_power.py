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
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from ui import theme
from ui.power_slide_layout import (NEON_ART_ASPECT, STYLES, hint_font_px,
                                   hint_rect, neon_knob_rect,
                                   neon_progress, track_rect)

_ASSETS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui"))
POWER = os.path.join(_ASSETS, "power_knob.png")
#: The painted capsule the knob rides, supplied by the operator 2026-09-29.
#: ON and OFF are PART OF THE PICTURE, so the neon style draws neither a
#: capsule nor a hint of its own — the first attempt drew both and the word
#: kept fighting the art it was sitting on.
TRACK = os.path.join(_ASSETS, "power_track.png")
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
        # ONLY default the height when the caller gave neither height NOR size.
        # Kivy applies kwargs in order, and setdefault put "height" AFTER the
        # caller's "size", so a caller asking for (156, 52) silently got
        # (156, 84). The knob is the full height, so it then ate more than half
        # its own track and left the hint a 51px box — which is why OFF wrapped
        # to two lines on the front page (operator, 2026-09-29).
        if "size" not in kwargs and "height" not in kwargs:
            kwargs["height"] = dp(84)
        super().__init__(**kwargs)
        self._cb = on_power_off
        self._style = track
        self._pad = dp(6)
        self._grab = False
        self._slid = "red"
        self._art = None
        with self.canvas.before:
            if track == "neon":
                from kivy.core.image import Image as CoreImage
                self._track_c = Color(1, 1, 1, 1)
                self._art = Rectangle(texture=CoreImage(TRACK).texture)
                self._fill_c = Color(0, 0, 0, 0)   # the art carries its own channel
                self._fill = RoundedRectangle(size=(0, 0))
            else:
                self._track_c = Color(*theme.hex_to_rgba(
                    theme.COLORS["black" if track == "line" else "surface"]))
                self._track = RoundedRectangle()
                self._fill_c = Color(*theme.hex_to_rgba(theme.COLORS[self._slid], 0))
                self._fill = RoundedRectangle()
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
        self.hint = Label(text="" if track == "neon" else hint_text, bold=True,
                          size_hint=(None, None),
                          color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
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
        if self._style == "neon":
            return neon_knob_rect(self.x, self.y, self.width, self.height, 0.0)[0]
        return self.x

    def _right(self):
        if self._style == "neon":
            return neon_knob_rect(self.x, self.y, self.width, self.height, 1.0)[0]
        return self.right - self._ks()

    def _progress(self):
        if self._style == "neon":
            return neon_progress(self.x, self.y, self.width, self.height,
                                 self.knob.center_x)
        span = self._right() - self._left()
        return 0.0 if span <= 0 else max(0.0, min(1.0, (self.knob.x - self._left()) / span))

    def _layout(self, *_):
        if self._style == "neon":
            # The picture IS the track: the capsule, ON and OFF are painted, so
            # nothing here draws them. Only the knob is placed, and it is placed
            # against the PAINTED capsule rather than the widget, because the
            # art carries a glow margin the capsule does not fill.
            self._art.pos, self._art.size = self.pos, self.size
            kx, ky, ks = neon_knob_rect(self.x, self.y, self.width, self.height,
                                        0.0 if not self._grab else self._progress())
            self.knob.size = (ks, ks)
            if not self._grab:
                self.knob.pos = (kx, ky)
            else:
                self.knob.pos = (self.knob.x, ky)
            # The capsule paints its own word, so the hint is normally empty —
            # but the home page writes a power-off FAILURE into it. Unplaced,
            # that label sat at the screen's bottom-left corner (2026-10-03).
            self.hint.pos, self.hint.size = (self.x, self.y), (self.width, self.height)
            self.hint.text_size = (self.width, self.height)
            self.hint.halign, self.hint.valign = "center", "middle"
            self.hint.font_size = hint_font_px(self.height, "pill", dp(9.5))
            self._refresh()
            return
        th, ty = self._th(), self._ty()
        r = th / 2.0
        self._track.pos, self._track.size, self._track.radius = (self.x, ty), (self.width, th), [r] * 4
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
        if self._style == "neon":
            return                     # no fill, no hint — both are painted
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
            ks = self.knob.width
            x = max(self._left(), min(self._right(), touch.x - ks / 2.0))
            y = self.knob.y if self._style == "neon" else self.y
            self.knob.pos = (x, y)
            self._refresh()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._grab = False
            if self._progress() >= _TRIGGER:
                self.knob.x = self._right()
                if self._style != "neon":      # the painted track carries no hint
                    self.hint.text = "powering off…"
                    self.hint.opacity = 1
                    self.hint.color = theme.hex_to_rgba(
                        theme.COLORS["text_primary"])
                if self._cb:
                    self._cb()
            else:
                y = self.knob.y if self._style == "neon" else self.y
                Animation(x=self._left(), y=y, d=0.22,
                          t="out_quad").start(self.knob)
            return True
        return super().on_touch_up(touch)
