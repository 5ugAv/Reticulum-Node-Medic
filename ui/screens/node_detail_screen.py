"""Node detail screen — opened by tapping a node in Monitor.

Shows the node's live health (from its latest decoded beacon), its field notes
and commissioning log, and a "Ping node now" action that triggers an on-demand
poll and clears a red/orange warning to green on a clean reply.
"""

from __future__ import annotations

from datetime import datetime

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.widgets.hex_status import HexStatus
from monitor.formatting import beacon_lines


def _line(text, color="text_primary", size="15sp", bold=False):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=size, color=theme.hex_to_rgba(theme.COLORS[color]),
                size_hint_y=None, height=dp(24))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class NodeDetailScreen(BoxLayout):
    def __init__(self, record, now, on_poll=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        self.record = record
        self._on_poll = on_poll

        # header: hex status + name + location
        head = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=dp(56), spacing=dp(10))
        head.add_widget(HexStatus(status=record.status(now),
                                  size_hint_x=None, width=dp(48)))
        title = BoxLayout(orientation="vertical")
        title.add_widget(_line(record.name or record.dst_hash[:12], bold=True,
                               size="20sp"))
        title.add_widget(_line(record.location or record.node_type,
                               color="text_secondary", size="13sp"))
        head.add_widget(title)
        self.add_widget(head)

        seen = record.last_seen_hours(now)
        self.add_widget(_line(
            "Last heard: "
            + ("never" if seen is None else f"{seen:.1f} h ago"),
            color="text_secondary"))

        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        col.bind(minimum_height=col.setter("height"))

        col.add_widget(_line("Health", bold=True, size="17sp"))
        for ln in beacon_lines(record):
            col.add_widget(_line("  " + ln, size="14sp"))

        if record.notes:
            col.add_widget(_line("Field notes", bold=True, size="17sp"))
            for note in record.notes:
                col.add_widget(_line("  • " + note, size="14sp"))

        if record.events:
            col.add_widget(_line("Commissioning log", bold=True, size="17sp"))
            for ev in record.events:
                stamp = datetime.fromtimestamp(ev.at).strftime("%Y-%m-%d %H:%M")
                col.add_widget(_line(
                    f"  {stamp}  [{ev.kind}] {ev.summary} — {ev.operator}",
                    color="text_secondary", size="13sp"))

        body.add_widget(col)
        self.add_widget(body)

        ping = Button(text="Ping node now", size_hint_y=None, height=dp(52),
                      font_size="18sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        ping.bind(on_release=lambda *_: self._ping())
        self.add_widget(ping)

    def _ping(self):
        if self._on_poll:
            self._on_poll(self.record.dst_hash)
