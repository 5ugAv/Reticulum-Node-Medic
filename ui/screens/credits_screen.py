"""The red cross's secret — credits and the why of it all.

Tapping the cross on the front page lands here: thanks to the people who made
the tool possible, and a few words on what Reticulum gives communities. Tap
anywhere to return to the front page; the five mode buttons along the bottom
jump straight into the tool (mirroring the poster's card row).

Edit CREDITS and SPIEL freely — they're plain data.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme

#: (role, name) — shown in order. Edit to taste.
#: (Add Sophie White's design credit when her pages land.)
CREDITS = [
    ("Reticulum & RNode", "Mark Qvist"),
    ("RNode Firmware CE", "Liberated Systems & contributors"),
    ("Concept, build, field testing & front page", "5ugAv"),
    ("Engineering companion", "Claude (Anthropic)"),
    ("Maps", "OpenStreetMap contributors & CARTO"),
    ("And", "every neighbour who puts a node on a roof"),
]

SPIEL = (
    "Reticulum lets communities build their own communications - networks "
    "that need no towers, no subscriptions, no permission, and no internet. "
    "Off-grid, encrypted, and owned by the people who run the nodes.\n\n"
    "When the weather takes the phone lines, when the power's out, when "
    "you're simply out of range - the mesh keeps talking. Every node "
    "someone adds makes it stronger for everyone else.\n\n"
    "This tool exists to make that easy: to help anyone birth a node, place "
    "it well, keep it healthy, and grow the mesh.\n\n"
    "Think Globally, act Locally."
)


class CreditsScreen(BoxLayout):
    def __init__(self, on_select=None, on_back=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self._on_select = on_select
        self._on_back = on_back
        self.padding = [dp(20), dp(24), dp(20), dp(8)]
        self.spacing = dp(8)

        title = Label(text="With thanks", bold=True, font_size="26sp",
                      size_hint_y=None, height=dp(44),
                      color=theme.hex_to_rgba(theme.COLORS["red"]))
        self.add_widget(title)

        scroll = ScrollView()
        body = BoxLayout(orientation="vertical", size_hint_y=None,
                         spacing=dp(4))
        body.bind(minimum_height=body.setter("height"))
        for role, name in CREDITS:
            row = Label(text=f"[color=9e9e9e]{role}[/color]\n[b]{name}[/b]",
                        markup=True, halign="center", valign="middle",
                        size_hint_y=None, height=dp(52),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            row.bind(size=lambda i, v: setattr(i, "text_size", v))
            body.add_widget(row)
        spiel = Label(text=SPIEL, halign="center", valign="top",
                      font_size="15sp", size_hint_y=None,
                      color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        spiel.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        spiel.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(16)))
        body.add_widget(spiel)
        hint = Label(text="tap anywhere to go back",
                     font_size="12sp", size_hint_y=None, height=dp(24),
                     color=theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.7))
        body.add_widget(hint)
        scroll.add_widget(body)
        self.add_widget(scroll)

        # the poster's card row, mirrored: five mode buttons along the bottom
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(6))
        for key, label in (("vitals", "VITALS"), ("scan", "SCAN"),
                           ("birth", "BIRTH"), ("triage", "TRIAGE"),
                           ("probe", "PROBE")):
            btn = Button(text=label, font_size="13sp", background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            btn.bind(on_release=lambda _b, k=key: self._select(k))
            row.add_widget(btn)
        self._mode_row = row
        self.add_widget(row)

    def _select(self, key):
        if self._on_select:
            self._on_select(key)

    def on_touch_up(self, touch):
        # the bottom mode row handles its own taps; anywhere else = back
        if self._mode_row.collide_point(*touch.pos):
            return super().on_touch_up(touch)
        if self.collide_point(*touch.pos) and self._on_back:
            self._on_back()
            return True
        return super().on_touch_up(touch)
