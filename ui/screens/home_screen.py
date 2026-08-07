"""HOME — the designed front page.

the designer's poster (assets/ui/front_page.png) fills the screen (fit, letterboxed
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
from ui.i18n import tr  # i18n: wrapped — power slider hint + flash-warning title
from ui.home_zones import screen_for, zone_at

POSTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      os.pardir, "assets", "ui", "front_page.png")
GEAR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "gear.png"))
POWER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "power.png"))


class HomeScreen(FloatLayout):
    def __init__(self, on_select=None, on_mode=None, initial_mode="home",
                 poster: str = None, **kwargs):
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
            on_power_off=self._power_off, hint_text=tr("OFF"),
            size_hint=(None, None), size=(knob * 3, knob),
            pos_hint={"x": 0.02, "top": 0.985})
        self.add_widget(self.power_slider)

        # Home / Backpack mode toggle (top-centre, between the power slide and the
        # gear) — flips the medic's network role: HOME = propagation node (routing +
        # store-and-forward), BACKPACK = mobile leaf (transport off, won't disturb
        # the mesh while it moves).
        from ui.widgets.mode_toggle import ModeToggle
        self.mode_toggle = ModeToggle(
            mode=initial_mode, on_toggle=(on_mode or (lambda m: None)),
            pos_hint={"right": 0.85, "top": 0.99})   # top-right, just left of the gear
        self.add_widget(self.mode_toggle)

        # Battery gauge — hidden until a UPS HAT is present (opacity 0). Sits under
        # the power slide on the left. Tune pos_hint on-device once the HAT is on.
        from ui.widgets.battery_gauge import BatteryGauge
        self.battery_gauge = BatteryGauge(pos_hint={"x": 0.02, "top": 0.90})
        self.add_widget(self.battery_gauge)

        # 'Radio parameters changed' badge — visible ONLY while the tool-wide
        # radio defaults differ from the canonical standard, so a non-standard
        # radio setup can never be forgotten (operator spec 2026-07-31).
        # Tap -> compare + one-tap revert to standard. Left edge under the
        # power slide; nudge down if the battery gauge (dormant until the UPS
        # HAT) ever needs the spot.
        self.radio_badge = Button(
            text=tr("! Radio params changed"), size_hint=(None, None),
            size=(dp(186), dp(36)), pos_hint={"x": 0.02, "top": 0.87},
            font_size="13.5sp", bold=True, background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.radio_badge.bind(on_release=lambda *_: self._radio_badge_tap())
        self.add_widget(self.radio_badge)
        self.refresh_radio_badge()

    def refresh_radio_badge(self):
        """Show/hide the non-standard-radio badge from the saved defaults."""
        try:
            from provisioning import radio_defaults as rd
            changed = not rd.is_standard()
        except Exception:
            changed = False
        self.radio_badge.opacity = 1 if changed else 0
        self.radio_badge.disabled = not changed

    def _radio_badge_tap(self):
        """Explain what's non-standard and offer the one-tap revert."""
        if getattr(self, "_rb_pop", None) is not None:   # doubled-tap guard
            return
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.popup import Popup
        from provisioning import radio_defaults as rd
        cur = rd.load_defaults()
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=(
            "[b]" + tr("This medic's radio defaults are NON-STANDARD.") + "[/b]\n\n"
            + tr("Current:") + f"  [b]{rd.summary(cur)}[/b]\n"
            + tr("Standard:") + f"  {rd.summary(rd.DEFAULT_PARAMS)}\n\n"
            + tr("New nodes are built with the CURRENT settings — they can only "
                 "talk to nodes on the same settings. Revert to standard so "
                 "every node can communicate, or keep them if this is a "
                 "deliberate separate mesh.")),
            color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(8))
        popup = Popup(title=tr("Radio parameters changed"), content=box,
                      size_hint=(0.94, 0.72),
                      title_color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]),
                      separator_color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        self._rb_pop = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_rb_pop", None))
        keep = Button(text=tr("Keep changed"), background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        keep.bind(on_release=popup.dismiss)
        revert = Button(text=tr("Revert to standard"), bold=True,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _revert(*_):
            popup.dismiss()
            try:
                rd.revert_to_standard()
            except Exception:
                pass
            self.refresh_radio_badge()
            # Retune the medic's own radio back to standard too (off-thread —
            # restarts rnsd); complain only if it fails.
            import threading

            def _tune():
                try:
                    from provisioning.medic_radio import retune_medic
                    ok, m = retune_medic()
                except Exception as e:      # noqa: BLE001
                    ok, m = False, str(e)[:100]
                if not ok:
                    from kivy.clock import Clock

                    def warn(_dt):
                        from ui.requirement_popup import requirement_popup
                        requirement_popup(
                            tr("Defaults reverted, but the medic's own radio "
                               "couldn't be retuned: ") + m,
                            tr("Radio parameters"), False)
                    Clock.schedule_once(warn, 0)
            threading.Thread(target=_tune, daemon=True).start()
        revert.bind(on_release=_revert)
        row.add_widget(keep)
        row.add_widget(revert)
        box.add_widget(row)
        popup.open()

    def _power_off(self):
        import threading
        from provisioning.power import power_off

        def do_off():
            threading.Thread(target=lambda: power_off(), daemon=True).start()

        # During a flash, WARN but let the operator override — a stuck flash must not
        # trap them into being unable to shut the medic down safely.
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is not None and app.flash_in_progress():
                from ui.confirm import confirm_power_override, FLASH_POWEROFF_WARNING
                confirm_power_override(FLASH_POWEROFF_WARNING,
                                       tr("Flashing in progress"), do_off)
                return
        except Exception:
            pass
        do_off()

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
                or self.power_slider.collide_point(*touch.pos)
                or self.mode_toggle.collide_point(*touch.pos)):
            return super().on_touch_up(touch)      # let the corner controls handle it
        frac = self._image_fraction(*touch.pos)
        if frac:
            mode = zone_at(*frac)
            if mode and self._on_select:
                # The painted word is not always the screen's internal name —
                # CHAT opens "comms", which has been called that since it was
                # built. One translation, stated in home_zones.
                self._on_select(screen_for(mode))
                return True
        return super().on_touch_up(touch)
