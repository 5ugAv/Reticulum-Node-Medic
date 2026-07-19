"""HOME — the designed front page.

Sophie's poster (assets/ui/front_page.png) fills the screen (fit, letterboxed
on the dark ground); taps are converted into image-fraction coordinates and
resolved by the pure ui.home_zones mapper: the five bottom cards open their
modes, the red cross opens MITOSIS (the medic itself). Everything visual is
the artwork — this screen is just an image and a hit-map.
"""

from __future__ import annotations

import os

from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image

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
        frac = self._image_fraction(*touch.pos)
        if frac:
            mode = zone_at(*frac)
            if mode and self._on_select:
                self._on_select(mode)
                return True
        return super().on_touch_up(touch)
