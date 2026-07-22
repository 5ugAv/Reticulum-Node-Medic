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
GEAR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "gear.png"))
POWER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "power.png"))


class HomeScreen(FloatLayout):
    def __init__(self, on_select=None, poster: str = None, **kwargs):
        super().__init__(**kwargs)
        self._on_select = on_select
        self.poster = Image(source=poster or os.path.normpath(POSTER),
                            allow_stretch=True, keep_ratio=True,
                            size_hint=(1, 1))
        self.add_widget(self.poster)

        # Gear / Settings button (top-right, off the poster's card zones) — the
        # medic's config hub (WiFi to start; more to come). Uses the supplied gear
        # PNG (transparent background, so no grey box).
        self.settings_btn = Button(size_hint=(None, None), size=(dp(58), dp(58)),
                                   pos_hint={"right": 0.98, "top": 0.98},
                                   background_normal=GEAR, background_down=GEAR,
                                   border=(0, 0, 0, 0), background_color=(1, 1, 1, 1))
        self.settings_btn.bind(on_release=lambda *_: self._on_select and self._on_select("settings"))
        self.add_widget(self.settings_btn)

        # Power slide (top-left) — a small slide-to-power-off, track ~3x the knob,
        # so a safe shutdown is right on the front page but can't fire by accident.
        from ui.widgets.slide_to_power import SlideToPowerOff
        knob = dp(52)
        self.power_slider = SlideToPowerOff(
            on_power_off=self._power_off, hint_text="OFF",
            size_hint=(None, None), size=(knob * 3, knob),
            pos_hint={"x": 0.02, "top": 0.985})
        self.add_widget(self.power_slider)

    def _power_off(self):
        import threading
        from provisioning.power import power_off
        threading.Thread(target=lambda: power_off(), daemon=True).start()

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
        if (self.settings_btn.collide_point(*touch.pos)
                or self.power_slider.collide_point(*touch.pos)):
            return super().on_touch_up(touch)      # let the corner controls handle it
        frac = self._image_fraction(*touch.pos)
        if frac:
            mode = zone_at(*frac)
            if mode and self._on_select:
                self._on_select(mode)
                return True
        return super().on_touch_up(touch)
