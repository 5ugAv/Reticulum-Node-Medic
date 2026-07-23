"""Settings — the medic's config hub, reached from the gear on the home page.

For now it holds one entry (WiFi & Network); it's built as a menu so more settings
(radio defaults, display, about, …) drop in as rows without touching navigation.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.slider import Slider
from kivy.uix.switch import Switch
from kivy.uix.widget import Widget

from ui import theme
from ui.widgets.slide_to_power import SlideToPowerOff
from provisioning.power import power_off
from provisioning import brightness as bright


def _line(text, bold=False, size="15sp", color="text_primary", h=30):
    lbl = Label(text=text, bold=bold, font_size=size, halign="left", valign="middle",
                size_hint_y=None, height=dp(h),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class SettingsScreen(BoxLayout):
    """A menu of settings. ``on_open(target)`` navigates to a setting's screen
    (e.g. ``"wifi"``)."""

    def __init__(self, on_open=None, on_retention_change=None,
                 node_count_provider=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(10)
        self.padding = dp(16)
        self._on_open = on_open
        self._on_retention_change = on_retention_change
        self._node_count_provider = node_count_provider

        self.add_widget(_line("Settings", bold=True, size="24sp", h=44))
        self.add_widget(self._entry("Default radio parameters",
                                    "Frequency, bandwidth, SF, CR, TX power that BIRTH "
                                    "pre-fills — includes regional presets", "radio_defaults"))
        self.add_widget(self._entry("Tool identity",
                                    "This medic's Reticulum identity, name, born date "
                                    "and lineage", "tool_identity"))
        self.add_widget(self._entry("WiFi & Network",
                                    "Connect to a hotspot or venue WiFi", "wifi"))
        self.add_widget(self._brightness_section())
        self.add_widget(self._alerts_section())
        self.add_widget(self._retention_section())
        # future rows (about…) slot in here.
        self.add_widget(Widget())          # push rows to the top

        # Clean shutdown — a SLIDE (not a tap) so it can't fire by accident. Protects
        # the SD card from the hard-power-cut corruption risk (hit 2026-07-22).
        self.add_widget(_line("Power", bold=True, size="15sp", color="accent", h=28))
        self.add_widget(SlideToPowerOff(on_power_off=self._power_off))
        self._power_note = _line("", size="12.5sp", color="text_secondary", h=24)
        self.add_widget(self._power_note)

    def _power_off(self):
        def work():
            ok, msg = power_off()
            Clock.schedule_once(lambda dt: setattr(self._power_note, "text", msg), 0)
        threading.Thread(target=work, daemon=True).start()

    # -- display brightness -------------------------------------------------
    def _brightness_section(self):
        """A Display ▸ Brightness slider driving the touchscreen backlight. Shows a
        graceful note instead of a dead slider when the panel exposes no control."""
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line("Display", bold=True, size="15sp", color="accent", h=26))
        if not bright.has_control():
            box.add_widget(_line("Brightness control isn't available on this display.",
                                 size="12.5sp", color="text_secondary", h=24))
            return box
        cur = bright.get_brightness()
        if cur is None:
            cur = bright.load_pct() or 80
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48),
                        spacing=dp(10))
        lbl = _line("Brightness", size="15sp", h=48)
        lbl.size_hint_x, lbl.width = None, dp(104)
        row.add_widget(lbl)
        self._bright_slider = Slider(min=bright.MIN_PCT, max=100, value=cur, step=1)
        self._bright_slider.bind(value=lambda _i, v: self._on_brightness(v))
        row.add_widget(self._bright_slider)
        self._bright_val = _line(f"{int(cur)}%", size="14sp", h=48)
        self._bright_val.size_hint_x, self._bright_val.width = None, dp(52)
        row.add_widget(self._bright_val)
        box.add_widget(row)
        return box

    def _on_brightness(self, value):
        pct = int(value)
        self._bright_val.text = f"{pct}%"
        ev = getattr(self, "_bright_ev", None)
        if ev is not None:
            ev.cancel()                       # debounce: don't spam sudo while dragging
        self._bright_ev = Clock.schedule_once(lambda dt: self._apply_brightness(pct), 0.15)

    def _apply_brightness(self, pct):
        threading.Thread(target=lambda: bright.set_brightness(pct), daemon=True).start()

    # -- alerts -------------------------------------------------------------
    def _alerts_section(self):
        from monitor import alerts
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line("Alerts", bold=True, size="15sp", color="accent", h=26))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                        spacing=dp(10))
        row.add_widget(_line("Alert me when a node goes orange or red", size="14sp"))
        sw = Switch(active=alerts.is_enabled(), size_hint_x=None, width=dp(90))
        sw.bind(active=lambda _i, v: alerts.set_enabled(bool(v)))
        row.add_widget(sw)
        box.add_widget(row)
        box.add_widget(_line(
            "Visual for now — a banner on VITALS and the affected nodes pushed to "
            "the top. (An audible option can be added later.)",
            size="12sp", color="text_secondary", h=34))
        return box

    # -- beacon history retention -------------------------------------------
    def _retention_section(self):
        from monitor import retention
        self._ret_days = retention.load_days()
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line("Beacon history retention", bold=True, size="15sp",
                             color="accent", h=26))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(46),
                        spacing=dp(8))
        minus = Button(text="–", size_hint_x=None, width=dp(52), bold=True,
                       font_size="22sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        minus.bind(on_release=lambda *_: self._step_retention(-1))
        self._ret_lbl = _line("", size="17sp", h=46)
        self._ret_lbl.halign = "center"
        plus = Button(text="+", size_hint_x=None, width=dp(52), bold=True,
                      font_size="22sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        plus.bind(on_release=lambda *_: self._step_retention(+1))
        row.add_widget(minus)
        row.add_widget(self._ret_lbl)
        row.add_widget(plus)
        box.add_widget(row)
        self._ret_impact = _line("", size="12.5sp", color="text_secondary", h=22)
        box.add_widget(self._ret_impact)
        self._refresh_retention()
        return box

    def _step_retention(self, direction):
        from monitor import retention
        self._ret_days = retention.set_days(
            retention.step(self._ret_days, direction))
        if self._on_retention_change:
            self._on_retention_change(self._ret_days)
        self._refresh_retention()

    def _refresh_retention(self):
        from monitor import retention
        self._ret_lbl.text = f"{self._ret_days} days"
        n = self._node_count_provider() if self._node_count_provider else 0
        est = retention.estimate_bytes(self._ret_days, max(n, 1))
        self._ret_impact.text = (f"Storage impact: ≈ {retention.format_size(est)} "
                                 f"for {n} node{'s' if n != 1 else ''} (estimate)")

    def _entry(self, title, subtitle, target):
        row = Button(text=title, size_hint_y=None, height=dp(62), halign="left",
                     valign="middle", font_size="18sp", bold=True,
                     background_normal="", background_down="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        row.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
        row.bind(on_release=lambda *_: self._on_open and self._on_open(target))
        return row
