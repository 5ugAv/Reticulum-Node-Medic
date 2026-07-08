"""Left navigation sidebar — 72 px, icon-only, the five operating modes."""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button

from ui import theme

# Short text labels rather than emoji glyphs (the field Pi's default font has
# no emoji; no emoji font is carried offline).
MODES = [
    ("build", "BLD"),
    ("diagnose", "DIAG"),
    ("monitor", "MON"),
    ("map", "MAP"),
    ("clone", "CLONE"),
]


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
