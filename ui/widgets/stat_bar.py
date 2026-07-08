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
    # Short text labels rather than emoji: the field Pi's default font has no
    # emoji glyphs (they render as tofu), and no emoji font is carried offline.
    def __init__(self, text, status="ok", **kwargs):
        super().__init__(**kwargs)
        self.text = text
        self.color = theme.status_rgba(status)
        self.font_size = "14sp"


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
                f"BAT {int(self.battery_pct)}%",
                theme.battery_status(self.battery_pct)))
        if self.show_solar:
            self.add_widget(_StatIcon("SOL", "ok"))
        if self.powered_by == "mains":
            self.add_widget(_StatIcon("AC", "ok"))
        self.add_widget(_StatIcon(
            f"SIG {int(self.signal_dbm)}dBm",
            theme.signal_status(self.signal_dbm)))
        self.add_widget(_StatIcon(
            f"SEEN {self.last_seen_hours:.1f}h",
            theme.last_seen_status(self.last_seen_hours)))
