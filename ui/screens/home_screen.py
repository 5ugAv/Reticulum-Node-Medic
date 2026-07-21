"""HOME — the designed front page.

Sophie's poster (assets/ui/front_page.png) fills the screen (fit, letterboxed
on the dark ground); taps are converted into image-fraction coordinates and
resolved by the pure ui.home_zones mapper: the five bottom cards open their
modes, the red cross opens MITOSIS (the medic itself). Everything visual is
the artwork — this screen is just an image and a hit-map.
"""

from __future__ import annotations

import os

from kivy.metrics import dp
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image

from ui import theme
from ui.home_zones import zone_at

POSTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      os.pardir, "assets", "ui", "front_page.png")


class HomeScreen(FloatLayout):
    def __init__(self, on_select=None, poster: str = None, **kwargs):
        super().__init__(**kwargs)
        self._on_select = on_select
        self.poster = Image(source=poster or os.path.normpath(POSTER),
                            allow_stretch=True, keep_ratio=True,
                            size_hint=(1, 1))
        self.add_widget(self.poster)

        # Gear / Settings button (top-right, off the poster's card zones) — the
        # medic's config hub (WiFi to start; more to come). Drawn, not an emoji.
        self.settings_btn = Button(size_hint=(None, None), size=(dp(54), dp(54)),
                                   pos_hint={"right": 0.985, "top": 0.985},
                                   background_normal="", background_down="",
                                   background_color=theme.hex_to_rgba(
                                       theme.COLORS["surface"], 0.85))
        self.settings_btn.bind(on_release=lambda *_: self._on_select and self._on_select("settings"))
        self.settings_btn.bind(pos=self._draw_gear, size=self._draw_gear)
        self.add_widget(self.settings_btn)

    def _draw_gear(self, *_):
        import math
        from kivy.graphics import Color, Line
        w = self.settings_btn
        w.canvas.after.clear()
        cx, cy = w.center_x, w.center_y
        r = min(w.width, w.height) * 0.26
        with w.canvas.after:
            Color(*theme.hex_to_rgba(theme.COLORS["text_primary"]))
            for i in range(8):                       # eight teeth
                a = i * math.pi / 4.0
                Line(points=[cx + math.cos(a) * r, cy + math.sin(a) * r,
                             cx + math.cos(a) * r * 1.5, cy + math.sin(a) * r * 1.5],
                     width=dp(2.2), cap="round")
            Line(circle=(cx, cy, r), width=dp(2.2))  # body ring
            Line(circle=(cx, cy, r * 0.42), width=dp(1.8))   # centre hole

    def _image_fraction(self, tx: float, ty: float):
        """Touch (window coords) -> image-fraction (x right, y DOWN), or None
        when the touch lands in the letterbox."""
        iw, ih = self.poster.norm_image_size
        if iw < 1 or ih < 1:
            return None
        ix = self.poster.center_x - iw / 2.0
        iy = self.poster.center_y - ih / 2.0
        fx = (tx - ix) / iw
        fy_up = (ty - iy) / ih
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy_up <= 1.0):
            return None
        return fx, 1.0 - fy_up            # zones use top-down y

    def on_touch_up(self, touch):
        if self.settings_btn.collide_point(*touch.pos):
            return super().on_touch_up(touch)      # let the gear button handle it
        frac = self._image_fraction(*touch.pos)
        if frac:
            mode = zone_at(*frac)
            if mode and self._on_select:
                self._on_select(mode)
                return True
        return super().on_touch_up(touch)
