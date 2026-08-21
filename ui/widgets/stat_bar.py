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
    #: Age of the last transport REPLAY of this node's beacon; negative means
    #: none on record (a NumericProperty cannot carry None). Display-only.
    last_echo_hours = NumericProperty(-1.0)
    powered_by = StringProperty("battery")  # battery | solar | mains
    show_battery = BooleanProperty(True)
    show_solar = BooleanProperty(True)
    show_signal = BooleanProperty(True)   # hidden when no signal was ever measured

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.spacing = 12
        self.bind(
            battery_pct=self._rebuild, signal_dbm=self._rebuild,
            last_seen_hours=self._rebuild, last_echo_hours=self._rebuild,
            powered_by=self._rebuild,
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
        if self.show_signal:
            self.add_widget(_StatIcon(
                f"SIG {int(self.signal_dbm)}dBm",
                theme.signal_status(self.signal_dbm)))
        # DAYS ONCE IT IS DAYS. "SEEN 268h" makes the reader do the division,
        # and the thing they are dividing towards — is this node overdue? — is
        # measured in days (operator, 2026-08-10).
        from monitor.formatting import echo_annotation, format_age
        self.add_widget(_StatIcon(
            f"SEEN {format_age(self.last_seen_hours)}",
            theme.last_seen_status(self.last_seen_hours)))
        # A replay fresher than the sighting gets a MUTED tag after SEEN, and
        # nothing more. It is deliberately weaker-looking than everything else
        # on this strip: an echo is rnsd repeating the node's last beacon, not
        # the node speaking — the evidence that kept a powered-off, battery-less
        # board green for hours on 2026-08-21. The tag never touches the SEEN
        # icon's colour, never the row's status hexagon, and echo_annotation
        # hides it the moment the node itself is heard again.
        tag = echo_annotation(
            self.last_seen_hours,
            self.last_echo_hours if self.last_echo_hours >= 0 else None)
        if tag is not None:
            muted = Label(text=f"· {tag}", font_size="12sp")
            # Same de-emphasis as the "x{n} services" note in the VITALS rows:
            # text_secondary at 0.6 — present, and clearly not a vital sign.
            muted.color = theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.6)
            self.add_widget(muted)
