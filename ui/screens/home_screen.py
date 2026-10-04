"""HOME — the designed front page.

The poster (assets/ui/front_page.png) fills the screen (fit, letterboxed
on the dark ground); taps are converted into image-fraction coordinates and
resolved by the pure ui.home_zones mapper: the five bottom cards open their
modes, and the red cross is an Easter egg that opens the credits screen.
Everything visual is the artwork — this screen is just an image and a hit-map.

NOTE (walkthrough 2026-08-26): MITOSIS has NO entry on this poster — it is only
reachable via BIRTH ▸ Choose manually. A non-technical keeper looking to "make
another one" cannot find it. Giving it a real home entry needs an artwork/layout
decision (a sixth card, or a labelled control) — left for the operator.
"""

from __future__ import annotations

import os

from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image

from ui import theme
from ui.i18n import tr  # i18n: wrapped — power slider hint + flash-warning title
from ui.home_zones import (CARD_ORDER, card_rect, press_fires, rect_to_widget,
                           screen_for, zone_at)

POSTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      os.pardir, "assets", "ui", "front_page.png")
GEAR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "gear.png"))
POWER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "power.png"))


class _UnreadBadge(Label):
    """A count in an accent disc. Opacity 0 at zero — never a "0" on the art."""

    def __init__(self, **kw):
        super().__init__(text="", bold=True, font_size=theme.font_sp("13sp"),
                         size_hint=(None, None), size=(dp(26), dp(26)),
                         color=theme.hex_to_rgba(theme.COLORS["background"]), **kw)
        from kivy.graphics import Color, Ellipse
        with self.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            self._disc = Ellipse()
        self.bind(pos=self._paint, size=self._paint)
        self.opacity = 0

    def _paint(self, *_):
        self._disc.pos, self._disc.size = self.pos, self.size

    def set_count(self, n: int):
        self.text = str(n) if n < 100 else "99+"
        self.opacity = 1 if n > 0 else 0


class HomeScreen(FloatLayout):
    def __init__(self, on_select=None, on_mode=None, initial_mode="home",
                 poster: str = None, **kwargs):
        super().__init__(**kwargs)
        self._on_select = on_select
        self._down_zone = None        # card the finger went down on
        self._pressed_zone = None     # card currently drawn pressed
        self._press_group = None      # its canvas instructions
        self._press_at = 0.0          # when it went down (minimum hold)
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
        # The GREEN knob rides a lit capsule (operator, 2026-09-29, with the
        # artwork): the old red button was the last thing on this page still
        # speaking the visual language the poster left behind.
        from ui.widgets.slide_to_power import SlideToPowerOff
        from ui.power_slide_layout import NEON_ART_ASPECT
        # SIZED TO THE ARTWORK'S OWN ASPECT. The track is a picture now; give it
        # a box of the wrong shape and the painted capsule either stretches or
        # floats in a letterbox, and the knob stops lining up with the channel
        # it is supposed to ride.
        slide_w = dp(52) * 3.4
        self.power_slider = SlideToPowerOff(
            on_power_off=self._power_off, track="neon",
            size_hint=(None, None),
            size=(slide_w, slide_w / NEON_ART_ASPECT),
            pos_hint={"x": 0.02, "top": 0.985})
        self.add_widget(self.power_slider)

        # Battery gauge (top, left of the gear) — Medic 2.0's UPS pack, drawn
        # only while a UPS actually answers on the bus (Medic 1 has no HAT and
        # honestly shows nothing). Green fill = charge, pulsing bolt = charging.
        from ui.widgets.battery_icon import BatteryIcon
        self.battery_icon = BatteryIcon(pos_hint={"right": 0.90, "top": 0.965})
        self.add_widget(self.battery_icon)

        # Home / Backpack mode toggle (top-centre, between the power slide and the
        # gear) — flips the medic's network role: HOME = propagation node (routing +
        # store-and-forward), BACKPACK = mobile leaf (transport off, won't disturb
        # the mesh while it moves).
        # BOTH PICTURES, one lit (operator sketch, 2026-09-29): the cottage to
        # the LEFT of the gear, the hiker BELOW it, and the chosen one in colour
        # while the other greys out. The old switch showed only the current
        # mode, so the alternative was invisible — you had to already know that
        # tapping a cottage would hand you a hiker.
        from ui.widgets.mode_toggle import (BACKPACK, HOME, ModeIcon, ModePair,
                                            ModeSwapArrow)
        pick = on_mode or (lambda m: None)
        icon = dp(62)
        self.mode_home = ModeIcon(
            HOME, selected=(initial_mode == HOME), on_select=pick,
            size_hint=(None, None), size=(icon, icon),
            pos_hint={"right": 0.862, "top": 0.985})     # left of the gear
        self.mode_backpack = ModeIcon(
            BACKPACK, selected=(initial_mode != HOME), on_select=pick,
            size_hint=(None, None), size=(icon, icon),
            pos_hint={"right": 0.985, "top": 0.878})     # below the gear
        self.add_widget(self.mode_home)
        self.add_widget(self.mode_backpack)
        # The double-headed arrow between them. Two lit-or-grey pictures do not
        # say they are the same control; this does.
        self.add_widget(ModeSwapArrow(
            size_hint=(None, None), size=(dp(44), dp(44)),
            pos_hint={"right": 0.910, "top": 0.925}))
        #: The app drives the mode from three places through this handle; the
        #: pair answers to the same calls the single switch did.
        self.mode_toggle = ModePair(self.mode_home, self.mode_backpack)

        # Unread count on the CHAT card (2026-09-30): the medic is a messenger
        # now, and a message that arrives while the poster is up must show.
        # Sits in the card's top-right corner, hidden at zero; placed off the
        # same card_rect the tap-map uses, so it moves with the artwork.
        self.chat_badge = _UnreadBadge()
        self.add_widget(self.chat_badge)
        self.poster.bind(size=self._place_badge, pos=self._place_badge)

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
            def work():
                # THE RESULT IS NOT DISCARDED (2026-08-30: the slider said
                # "powering off…" and nothing happened — power_off's failure
                # message was thrown away, so a refused/failed shutdown was
                # indistinguishable from a slow one. The operator sat with a
                # full fan and a lying label). On failure the slider itself
                # carries the reason.
                ok, msg = power_off()
                if not ok:
                    from kivy.clock import Clock

                    def show(_dt):
                        try:
                            # knob back to ON, reason in red on the slider
                            self.power_slider.reset(
                                tr("Couldn't power off: ") + msg)
                        except Exception:      # noqa: BLE001
                            pass
                    Clock.schedule_once(show, 0)
            threading.Thread(target=work, daemon=True).start()

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

    # ---- the keys answer when you press them ------------------------------
    # The five cards are PAINTED as raised, bevelled plates, which is a promise
    # that they can be pressed. Until now nothing answered it: the front page is
    # a flat image, and a tap changed screens with no acknowledgement at all. On
    # a touchscreen that reads as a missed tap, and the operator presses again.
    #
    # So the key is drawn going DOWN into its well while the finger is on it —
    # the face falls out of the light, and the lower lip of the well catches it.
    # The geometry comes from home_zones.card_rect, the same numbers zone_at
    # divides the row by, so the highlight can never land on the card next door.

    MIN_PRESS = 0.10          # seconds — a flick still shows a visible press

    def set_unread(self, n: int):
        """Called by the app when the chat store changes."""
        self.chat_badge.set_count(int(n or 0))
        self._place_badge()

    def _place_badge(self, *_):
        r = self._card_press_rect("chat")
        if r is None:
            return
        x, y, w, h = r
        d = self.chat_badge.width
        self.chat_badge.pos = (x + w - d - dp(6), y + h - d - dp(6))

    def _card_press_rect(self, zone):
        """Where that card is on the screen right now, in Kivy pixels, or None
        when the poster hasn't been laid out (or measured) yet."""
        rect = card_rect(zone)
        if rect is None:
            return None
        iw, ih = self.poster.norm_image_size
        if iw < 1 or ih < 1:
            return None
        return rect_to_widget(rect,
                              self.poster.center_x - iw / 2.0,
                              self.poster.center_y - ih / 2.0, iw, ih)

    def _press_card(self, zone):
        if zone == self._pressed_zone:
            return
        self._release_card()
        rect = self._card_press_rect(zone)
        if rect is None:
            return
        from kivy.clock import Clock
        from kivy.graphics import Color, Line, Rectangle
        from kivy.graphics.instructions import InstructionGroup
        x, y, w, h = rect
        inset = max(dp(2), w * 0.022)
        g = InstructionGroup()
        g.add(Color(0, 0, 0, 0.38))                  # the face drops out of the light
        g.add(Rectangle(pos=(x, y), size=(w, h)))
        g.add(Color(0.60, 1.0, 0.45, 0.90))          # the well's lower lip catches it
        g.add(Line(width=dp(1.4), points=[x + inset, y + h - inset,
                                          x + inset, y + inset,
                                          x + w - inset, y + inset,
                                          x + w - inset, y + h - inset]))
        self.canvas.after.add(g)
        self._press_group = g
        self._pressed_zone = zone
        self._press_at = Clock.get_boottime()

    def _release_card(self, _dt=None):
        if self._press_group is not None:
            try:
                self.canvas.after.remove(self._press_group)
            except Exception:      # noqa: BLE001 — already gone; never cost a tap
                pass
        self._press_group = None
        self._pressed_zone = None

    def _release_card_soon(self):
        """Let go, but never before MIN_PRESS — a quick tap that goes down and
        up inside one frame would otherwise show the operator nothing at all."""
        if self._press_group is None:
            return
        from kivy.clock import Clock
        held = Clock.get_boottime() - self._press_at
        if held >= self.MIN_PRESS:
            self._release_card()
        else:
            Clock.schedule_once(self._release_card, self.MIN_PRESS - held)

    def _corner_control(self, pos) -> bool:
        """True while the touch belongs to the gear, the power slide or the
        Home/Backpack toggle, which handle themselves."""
        return bool(self.settings_btn.collide_point(*pos)
                    or self.power_slider.collide_point(*pos)
                    or self.mode_toggle.collide_point(*pos))

    def on_touch_down(self, touch):
        self._down_zone = None
        if not self._corner_control(touch.pos):
            frac = self._image_fraction(*touch.pos)
            if frac:
                zone = zone_at(*frac)
                if zone in CARD_ORDER:
                    self._down_zone = zone
                    self._press_card(zone)
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        # Slide off a key and it comes back up, the way a physical one does.
        if self._pressed_zone is not None:
            frac = self._image_fraction(*touch.pos)
            if not frac or zone_at(*frac) != self._pressed_zone:
                self._release_card()
        return super().on_touch_move(touch)

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
        down_zone, self._down_zone = self._down_zone, None
        self._release_card_soon()
        if self._corner_control(touch.pos):
            return super().on_touch_up(touch)      # let the corner controls handle it
        frac = self._image_fraction(*touch.pos)
        if frac:
            mode = zone_at(*frac)
            if mode and self._on_select:
                # A key only fires if the finger went down AND came up on the
                # same one — sliding off cancels, the way a physical key does.
                # The rule itself lives in home_zones so it can be tested; a
                # Kivy screen cannot be built without a display.
                if not press_fires(mode, down_zone):
                    return super().on_touch_up(touch)
                # The painted word is not always the screen's internal name —
                # one translation table, stated in home_zones (CHAT opened
                # the phone hand-off "comms" until the medic got its own
                # messenger, 2026-09-29).
                self._on_select(screen_for(mode))
                return True
        return super().on_touch_up(touch)
