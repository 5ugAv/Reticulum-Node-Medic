"""Per-node stat icon strip: battery, solar, mains, signal, last-seen.

RTNode-2400 nodes have no battery/solar hardware, so those icons are hidden
(``show_battery=False``, ``show_solar=False``) and only signal + last-seen show.
"""

from __future__ import annotations

from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from ui import theme


class _StatIcon(Label):
    def __init__(self, emoji, text, status="ok", **kwargs):
        super().__init__(**kwargs)
        self.text = f"{emoji} {text}"
        self.color = theme.status_rgba(status)
        self.font_size = "15sp"


class StatBar(BoxLayout):
    battery_pct = NumericProperty(100)
    signal_dbm = NumericProperty(-80)
    last_seen_hours = NumericProperty(0.0)
    powered_by = StringProperty("battery")  # battery | solar | mains
    show_battery = BooleanProperty(True)
    show_solar = BooleanProperty(True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.spacing = 12
        self.bind(
            battery_pct=self._rebuild, signal_dbm=self._rebuild,
            last_seen_hours=self._rebuild, powered_by=self._rebuild,
            show_battery=self._rebuild, show_solar=self._rebuild,
        )
        self._rebuild()

    def _rebuild(self, *args):
        self.clear_widgets()
        if self.show_battery:
            self.add_widget(_StatIcon(
                "\U0001F50B", f"{int(self.battery_pct)}%",
                theme.battery_status(self.battery_pct)))
        if self.show_solar:
            self.add_widget(_StatIcon("☀", "", "ok"))
        if self.powered_by == "mains":
            self.add_widget(_StatIcon("\U0001F50C", "", "ok"))
        self.add_widget(_StatIcon(
            "\U0001F4F6", f"{int(self.signal_dbm)} dBm",
            theme.signal_status(self.signal_dbm)))
        self.add_widget(_StatIcon(
            "\U0001F550", f"{self.last_seen_hours:.1f}h",
            theme.last_seen_status(self.last_seen_hours)))
