"""Settings — the medic's config hub, reached from the gear on the home page.

For now it holds one entry (WiFi & Network); it's built as a menu so more settings
(radio defaults, display, about, …) drop in as rows without touching navigation.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme


def _line(text, bold=False, size="15sp", color="text_primary", h=30):
    lbl = Label(text=text, bold=bold, font_size=size, halign="left", valign="middle",
                size_hint_y=None, height=dp(h),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class SettingsScreen(BoxLayout):
    """A menu of settings. ``on_open(target)`` navigates to a setting's screen
    (e.g. ``"wifi"``)."""

    def __init__(self, on_open=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(10)
        self.padding = dp(16)
        self._on_open = on_open

        self.add_widget(_line("Settings", bold=True, size="24sp", h=44))
        self.add_widget(self._entry("WiFi & Network",
                                    "Connect to a hotspot or venue WiFi", "wifi"))
        # future rows (radio defaults, display, about…) slot in here.
        self.add_widget(Widget())          # push rows to the top

    def _entry(self, title, subtitle, target):
        row = Button(text=title, size_hint_y=None, height=dp(62), halign="left",
                     valign="middle", font_size="18sp", bold=True,
                     background_normal="", background_down="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        row.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
        row.bind(on_release=lambda *_: self._on_open and self._on_open(target))
        return row
