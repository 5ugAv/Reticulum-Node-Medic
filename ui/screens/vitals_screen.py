"""VITALS screen — the network monitor dashboard (mode 1).

Scrollable node list (two columns when wide enough), a filter bar
(All / OK / Warn / Alert / Search), and one row per node: a hexagonal status
indicator, name, location and a stat icon strip. This is the one screen with
no Back/Home nav — it is the tool's home.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from ui import theme
from ui.i18n import tr  # i18n: wrapped — filter labels + Search hint
from ui.onscreen_keyboard import bind_field
from ui.widgets.hex_status import HexStatus
from ui.widgets.stat_bar import StatBar

# NB: FILTERS values double as logic keys (_FILTER_TO_STATUS, active_filter). Keep
# them ENGLISH — only the DISPLAYED label is passed through tr() at render time.
FILTERS = ["All", "OK", "Warn", "Alert"]
_FILTER_TO_STATUS = {"OK": "ok", "Warn": "warn", "Alert": "alert"}


def partition_quiet(rows):
    """Split rows into (active, quiet) preserving each side's order. A row is
    quiet when the registry flagged it (unheard past theme.QUIET_AFTER_HOURS).
    Pure — unit-testable without building widgets."""
    active = [n for n in rows if not n.get("quiet")]
    quiet = [n for n in rows if n.get("quiet")]
    return active, quiet


class QuietDivider(BoxLayout):
    """The separator between active nodes and the ones that have gone quiet —
    a hairline with a small caption. Nodes below it are just not-recently-heard;
    a fresh ping moves them back above on the next refresh."""

    def __init__(self, count, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = dp(30)
        self.padding = (dp(12), dp(6))
        lbl = Label(text=f"Quiet · not heard in {theme.QUIET_AFTER_HOURS}h+   ({count})",
                    halign="center", valign="middle", font_size="12sp", bold=True,
                    color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        from kivy.graphics import Color, Line
        with self.canvas.before:
            self._ln_c = Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.35))
            self._ln = Line(points=[], width=1)
        self.bind(pos=self._draw_line, size=self._draw_line)
        self.add_widget(lbl)

    def _draw_line(self, *_):
        y = self.y + self.height / 2
        self._ln.points = [self.x + dp(8), y, self.right - dp(8), y]


class NodeRow(BoxLayout):
    """One node in the list. Tapping it opens the node's stored certificate
    (``on_open(node)``); a scroll drag is ignored via a movement threshold."""

    def __init__(self, node, on_open=None, **kwargs):
        super().__init__(**kwargs)
        self.node = node
        self._on_open = on_open
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = dp(80)
        self.spacing = dp(10)
        self.padding = dp(8)

        hexw = HexStatus(status=node.get("status", "unknown"),
                         size_hint_x=None, width=dp(48))
        self.add_widget(hexw)

        # The name + subtitle stack. 2026-08-22 (live): a "Propagation relay
        # <hash8>" row rendered with its name, hash and "LXMF propagation
        # announces" subtitle ON TOP of each other. The cause was two Labels
        # sharing the column with size_hint_y=1 and text_size bound to their
        # FULL box: a long name wrapped to two lines whose texture overflowed
        # its half of the box (valign middle spills both ways) and landed on the
        # subtitle below. Fix: each line is a FIXED-height, single-line label
        # that ELLIPSISES when too long (shorten), and the column is sized to its
        # children and vertically centred — so name and subtitle always stack
        # cleanly, for a normal row AND the longer propagation-relay case.
        text = BoxLayout(orientation="vertical", size_hint_y=None,
                         pos_hint={"center_y": 0.5})
        text.bind(minimum_height=text.setter("height"))
        name = Label(text=node.get("name", "unknown"), halign="left",
                     valign="middle", bold=True,
                     size_hint_y=None, height=dp(26),
                     shorten=True, shorten_from="right",
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        name.bind(size=lambda i, v: setattr(i, "text_size", v))
        loc = Label(text=node.get("location", ""), halign="left",
                    valign="middle", font_size="13sp",
                    size_hint_y=None, height=dp(20),
                    shorten=True, shorten_from="right",
                    color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        loc.bind(size=lambda i, v: setattr(i, "text_size", v))
        text.add_widget(name)
        text.add_widget(loc)
        caps = node.get("capabilities")
        if caps:
            chips = BoxLayout(orientation="horizontal", size_hint_y=None,
                              height=dp(18), spacing=dp(10))
            for key, label in (("lora", "LORA"), ("wifi", "WIFI"),
                               ("bluetooth", "BT"), ("internet", "NET")):
                # THREE STATES, THREE LOOKS. True/False/None used to collapse
                # into two: green for working, one grey for everything else —
                # so "the node says this is down" and "the node has never
                # mentioned it" were indistinguishable. That is the gap the
                # board-type guess used to be poured into, and it is how VITALS
                # came to show BT on a node whose Bluetooth was switched off
                # (SolarLove, 2026-08-10).
                #
                #   green bold   the node reported it working — evidence
                #   amber        the node reported it DOWN — actionable
                #   faint grey   never mentioned — unknown, and not a claim
                state = caps.get(key)
                active = state is True
                colour = ("green" if active
                          else "amber" if state is False
                          else "text_secondary")
                chip = Label(text=label, font_size="11sp", bold=active,
                             halign="left", valign="middle",
                             size_hint_x=None, width=dp(44),
                             color=theme.hex_to_rgba(
                                 theme.COLORS[colour],
                                 1.0 if active else 0.8 if state is False
                                 else 0.3))
                chip.bind(size=lambda i, v: setattr(i, "text_size", v))
                chips.add_widget(chip)
            if node.get("aspects", 1) > 1:
                more = Label(text=f"x{node['aspects']} services",
                             font_size="11sp", halign="left", valign="middle",
                             color=theme.hex_to_rgba(
                                 theme.COLORS["text_secondary"], 0.6))
                more.bind(size=lambda i, v: setattr(i, "text_size", v))
                chips.add_widget(more)
            text.add_widget(chips)
        self.add_widget(text)

        is_rtnode = node.get("type") == "rtnode2400"
        batt = node.get("battery_pct")
        sig = node.get("signal_dbm")
        echo = node.get("last_echo_hours")
        direct = node.get("last_direct_hours")
        self.add_widget(StatBar(
            battery_pct=batt if batt is not None else 0,
            signal_dbm=sig if sig is not None else 0,
            last_seen_hours=node.get("last_seen_hours", 0.0),
            # has_seen/seen_impossible carry what the number above can't: a
            # never-heard fleet row ("SEEN never" grey) and a clock-stepped
            # reading ("SEEN ?" grey). Never green off a collapsed 0.0 — the
            # dead-board-green class through the clock-step door (2026-08-22).
            has_seen=node.get("has_seen", True),
            seen_impossible=node.get("seen_impossible", False),
            # A transport replay fresher than the node's last DIRECT word
            # shows as a muted "echo 4m" after SEEN (StatBar composes it via
            # formatting.seen_and_echo; has_echo/has_direct carry the None-ness
            # a NumericProperty can't). Display-only: the hexagon above took
            # node["status"] before this line and echoes feed no status
            # anywhere — the rule from 2026-08-21, when replays kept a dead
            # board's row green.
            last_echo_hours=echo if echo is not None else 0.0,
            has_echo=echo is not None,
            last_direct_hours=direct if direct is not None else 0.0,
            has_direct=direct is not None,
            powered_by=node.get("powered_by", "battery"),
            show_battery=batt is not None and not is_rtnode,
            show_solar=batt is not None and not is_rtnode,
            show_signal=sig is not None,     # never invent a signal reading
            size_hint_x=None, width=dp(210)))   # was 320 — starved the name column

    def on_touch_up(self, touch):
        # A stationary tap (not a scroll) opens the node's certificate. The
        # movement threshold lets a drag scroll the list without triggering.
        if (self._on_open is not None and touch.grab_current is None
                and self.collide_point(*touch.pos)
                and abs(touch.x - touch.ox) + abs(touch.y - touch.oy) < dp(12)):
            self._on_open(self.node)
            return True
        return super().on_touch_up(touch)


class VitalsScreen(BoxLayout):
    def __init__(self, nodes=None, on_open=None, on_self_diagnose=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self._on_open = on_open
        self._on_self_diagnose = on_self_diagnose
        self.nodes = nodes or []
        self.active_filter = "All"
        self.search_text = ""

        self.filter_bar = BoxLayout(size_hint_y=None, height=dp(48),
                                    spacing=dp(6), padding=dp(6))
        self._filter_buttons = []
        for name in FILTERS:
            btn = Button(text=name, background_normal="",
                         background_color=theme.hex_to_rgba(
                             theme.COLORS["surface"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            btn.filter_name = name
            btn.bind(on_release=lambda b: self.set_filter(b.filter_name))
            self.filter_bar.add_widget(btn)
            self._filter_buttons.append(btn)
        self._highlight_filter()
        # Self-check on the FILTER row (the medic diagnosing its OWN radio/GPS —
        # VITALS is mesh health and the medic is the vantage point, so a quick
        # self-test lives here as well as in Settings). Opens self_diagnose.
        if on_self_diagnose is not None:
            self_btn = Button(text=tr("Self-check"), size_hint_x=None, width=dp(110),
                              bold=True, background_normal="",
                              background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                              color=theme.hex_to_rgba(theme.COLORS["background"]))
            self_btn.bind(on_release=lambda *_: self._on_self_diagnose())
            self.filter_bar.add_widget(self_btn)
        self.add_widget(self.filter_bar)

        # Search on its OWN row BELOW the filters — the single top row (filters +
        # search + self-check) was too cramped on the 5in panel.
        search_row = BoxLayout(size_hint_y=None, height=dp(46), padding=(dp(6), 0))
        search = TextInput(hint_text=tr("Search"), multiline=False, font_size="26sp")
        bind_field(search)                           # pop the on-screen keyboard
        search.bind(text=lambda i, v: self.set_search(v))
        search_row.add_widget(search)
        self.add_widget(search_row)

        # Alert banner (Settings ▸ Alerts) — shows when a node is orange/red; the
        # alerting nodes are also pushed to the top of the list.
        from kivy.graphics import Color, Rectangle
        self.alert_banner = Label(text="", size_hint_y=None, height=dp(0),
                                  halign="left", valign="middle", bold=True,
                                  font_size="14sp", padding=(dp(10), 0),
                                  color=theme.hex_to_rgba(theme.COLORS["background"]))
        with self.alert_banner.canvas.before:
            self._ab_color = Color(*theme.status_rgba("alert", 0))
            self._ab_rect = Rectangle()
        self.alert_banner.bind(
            pos=lambda *_: setattr(self._ab_rect, "pos", self.alert_banner.pos),
            size=lambda i, v: (setattr(self._ab_rect, "size", v),
                               setattr(i, "text_size", (v[0] - dp(20), v[1]))))
        self.add_widget(self.alert_banner)

        self.scroll = ScrollView()
        self.grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(4))
        self.grid.bind(minimum_height=self.grid.setter("height"))
        self.scroll.add_widget(self.grid)
        self.add_widget(self.scroll)

        self.refresh()

    # -- filtering (pure logic, unit-friendly) -----------------------------

    def visible_nodes(self):
        result = []
        want = _FILTER_TO_STATUS.get(self.active_filter)
        for node in self.nodes:
            if want and node.get("status") != want:
                continue
            if self.search_text and self.search_text.lower() not in (
                    node.get("name", "").lower()):
                continue
            result.append(node)
        return result

    def set_nodes(self, nodes):
        """Replace the node list (e.g. from a live MonitorService poll) and
        re-render, preserving the active filter/search."""
        self.nodes = nodes or []
        self._highlight_filter()          # tab counts follow the data
        self.refresh()

    def _highlight_filter(self):
        """The selected tab reads as selected even when its list is empty, and
        every tab carries its count — an empty tab says 0 instead of nothing.
        Neighbours (status unknown: heard, health unknowable) count under All
        only."""
        counts = {"All": len(self.nodes)}
        for f, st in _FILTER_TO_STATUS.items():
            counts[f] = sum(1 for n in self.nodes if n.get("status") == st)
        for btn in getattr(self, "_filter_buttons", []):
            btn.text = f"{tr(btn.filter_name)} {counts.get(btn.filter_name, 0)}"
            active = btn.filter_name == self.active_filter
            btn.background_color = theme.hex_to_rgba(
                theme.COLORS["accent"] if active else theme.COLORS["surface"])
            btn.color = theme.hex_to_rgba(
                theme.COLORS["background"] if active
                else theme.COLORS["text_primary"])

    def set_filter(self, name):
        self.active_filter = name
        self._highlight_filter()
        self.refresh()

    def set_search(self, text):
        self.search_text = text
        self.refresh()

    def refresh(self):
        from monitor import alerts
        enabled = alerts.is_enabled()
        # banner reflects the CURRENT alerting set (whole fleet, not the filtered view)
        text = alerts.banner_text(self.nodes) if enabled else ""
        self.alert_banner.text = text
        self.alert_banner.height = dp(34) if text else dp(0)
        self._ab_color.a = 0.92 if text else 0.0

        rows = self.visible_nodes()
        if enabled:
            worst = {"alert": 0, "warn": 1}         # push orange/red to the top
            rows = sorted(rows, key=lambda n: worst.get(n.get("status"), 2))
        # Nodes not heard in a while sink below a divider (recency, not health) —
        # the alert sort above still orders each side, so an ACTIVE fault rises to
        # the top while a merely-dormant node settles quietly underneath.
        active, quiet = partition_quiet(rows)
        self.grid.clear_widgets()
        for node in active:
            self.grid.add_widget(NodeRow(node, on_open=self._on_open))
        if quiet:
            self.grid.add_widget(QuietDivider(len(quiet)))
            for node in quiet:
                self.grid.add_widget(NodeRow(node, on_open=self._on_open))
