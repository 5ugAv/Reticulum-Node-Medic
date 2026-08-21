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
    #: Age of the last transport REPLAY of this node's announce, valid only
    #: while ``has_echo`` — an explicit flag, not a -1 sentinel, so "no echo"
    #: and a genuine 0.0h echo can never conflate (the registry clamps ages
    #: to >= 0, so 0.0 is a value a real echo takes under clock skew).
    last_echo_hours = NumericProperty(0.0)
    has_echo = BooleanProperty(False)
    #: Age of the node's last DIRECT word, valid only while ``has_direct`` —
    #: the echo tag's gate (see monitor.formatting.seen_and_echo). Display-only.
    last_direct_hours = NumericProperty(0.0)
    has_direct = BooleanProperty(False)
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
            has_echo=self._rebuild, last_direct_hours=self._rebuild,
            has_direct=self._rebuild, powered_by=self._rebuild,
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
        from monitor.formatting import seen_and_echo
        seen_text, echo_tag = seen_and_echo({
            "last_seen_hours": self.last_seen_hours,
            "last_echo_hours": self.last_echo_hours if self.has_echo else None,
            "last_direct_hours": (self.last_direct_hours
                                  if self.has_direct else None),
        })
        self.add_widget(_StatIcon(
            seen_text, theme.last_seen_status(self.last_seen_hours)))
        # A replay fresher than the node's last DIRECT word gets a MUTED tag
        # after SEEN, and nothing more. It is deliberately weaker-looking than
        # everything else on this strip: an echo is rnsd repeating the node's
        # last announce, not the node speaking — the evidence that kept a
        # powered-off, battery-less board green for hours on 2026-08-21. The
        # tag never touches the SEEN icon's colour, never the row's status
        # hexagon, and seen_and_echo hides it once the node itself is heard.
        if echo_tag is not None:
            # Sized to its TEXT, not to an equal share of the strip: the strip
            # is a hand-tuned dp(210) (vitals_screen — do not widen; it starved
            # the name column at 320), and a fifth equal-width child would
            # shove every icon into its neighbour. At 10sp with no "· " prefix
            # the tag is its narrowest honest self: on an RTNode row at
            # density 1.5 (315 px strip), "SIG -64dBm" + "SEEN 3.1h" at 14sp
            # (~21 px/em Roboto, ≈110+100 px) + "echo 4m" at 10sp (≈55 px)
            # + 2×12 spacing ≈ 289 px — it fits. Battery rows were already
            # over budget before this label existed; it adds only its texture.
            muted = Label(text=echo_tag, font_size="10sp",
                          size_hint_x=None, width=0)
            muted.bind(texture_size=lambda i, ts: setattr(i, "width", ts[0]))
            # Same de-emphasis as the "x{n} services" note in the VITALS rows:
            # text_secondary at 0.6 — present, and clearly not a vital sign.
            muted.color = theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.6)
            self.add_widget(muted)
