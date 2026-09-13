"""Left navigation sidebar — 72 px, icon-only, the five operating modes."""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button

from ui import theme

# Final confirmed modes, in order. Text labels rather than emoji glyphs (the
# field Pi's default font has no emoji; no emoji font is carried offline) — the
# intended icons, until designed PNGs land, are:
#   VITALS 🫀 (monitor dashboard) · SCAN 🧫 (topology + map) · BIRTH 🥚 (provision)
#   TRIAGE 🩺 (site assessment) · PROBE 🩻 (diagnose + repair) · MITOSIS 🧬 (clone)
# Since the 2026-09-13 repaint (docs/FRONT_PAGE_BRIEF.md) the painted word and
# the screen key differ (MAPS card, "scan" screen). POSTER_WORD_FOR is the one
# place the two are tied, so the sidebar reads it instead of keeping a second
# copy that could drift from the poster. HOME and PROBE have no painted card.
from ui.home_zones import POSTER_WORD_FOR

MODES = [(key, POSTER_WORD_FOR.get(key, key.upper()))
         for key in ("home", "vitals", "scan", "birth", "triage", "probe")]


class Sidebar(BoxLayout):
    def __init__(self, on_select=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.size_hint_x = None
        self.width = dp(72)
        self._on_select = on_select
        with self.canvas.before:
            from kivy.graphics import Color, Rectangle
            self._bg_color = Color(*theme.hex_to_rgba(theme.COLORS["sidebar"]))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)
        for name, icon in MODES:
            btn = Button(
                text=icon, font_size="13sp",
                background_normal="", background_color=(0, 0, 0, 0),
                color=theme.hex_to_rgba(theme.COLORS["text_primary"]),
            )
            btn.mode_name = name
            btn.bind(on_release=self._pressed)
            self.add_widget(btn)

    def _sync_bg(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _pressed(self, btn):
        if self._on_select:
            self._on_select(btn.mode_name)
