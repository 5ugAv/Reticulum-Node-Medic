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
from ui.widgets.hex_status import HexStatus
from ui.widgets.stat_bar import StatBar

FILTERS = ["All", "OK", "Warn", "Alert"]
_FILTER_TO_STATUS = {"OK": "ok", "Warn": "warn", "Alert": "alert"}


class NodeRow(BoxLayout):
    """One node in the list."""

    def __init__(self, node, **kwargs):
        super().__init__(**kwargs)
        self.node = node
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = dp(80)
        self.spacing = dp(10)
        self.padding = dp(8)

        hexw = HexStatus(status=node.get("status", "unknown"),
                         size_hint_x=None, width=dp(48))
        self.add_widget(hexw)

        text = BoxLayout(orientation="vertical")
        name = Label(text=node.get("name", "unknown"), halign="left",
                     valign="middle", bold=True,
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        name.bind(size=lambda i, v: setattr(i, "text_size", v))
        loc = Label(text=node.get("location", ""), halign="left",
                    valign="middle", font_size="13sp",
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
                active = caps.get(key) is True
                chip = Label(text=label, font_size="11sp", bold=active,
                             halign="left", valign="middle",
                             size_hint_x=None, width=dp(44),
                             color=theme.hex_to_rgba(
                                 theme.COLORS["green"] if active
                                 else theme.COLORS["text_secondary"],
                                 1.0 if active else 0.45))
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
        self.add_widget(StatBar(
            battery_pct=batt if batt is not None else 0,
            signal_dbm=sig if sig is not None else 0,
            last_seen_hours=node.get("last_seen_hours", 0.0),
            powered_by=node.get("powered_by", "battery"),
            show_battery=batt is not None and not is_rtnode,
            show_solar=batt is not None and not is_rtnode,
            show_signal=sig is not None,     # never invent a signal reading
            size_hint_x=None, width=dp(320)))


class VitalsScreen(BoxLayout):
    def __init__(self, nodes=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
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
        search = TextInput(hint_text="Search", multiline=False,
                           size_hint_x=None, width=dp(220))
        search.bind(text=lambda i, v: self.set_search(v))
        self.filter_bar.add_widget(search)
        self.add_widget(self.filter_bar)

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
            btn.text = f"{btn.filter_name} {counts.get(btn.filter_name, 0)}"
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
        self.grid.clear_widgets()
        for node in self.visible_nodes():
            self.grid.add_widget(NodeRow(node))
