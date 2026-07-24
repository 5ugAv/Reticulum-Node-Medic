"""A small round "?" help button. Tapping it opens the Reticulum/radio quick-guide
in a popup — so an operator can get the explanation of any setup step WITHOUT
leaving the step they're on (unlike navigating to the Settings guide screen).

Drop one into any setup header:  header.add_widget(HelpButton())
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.popup import Popup

from ui import theme
from ui.widgets.guide_content import build_guide_content
from provisioning import network_guide as g


class HelpButton(Button):
    """Round accent "?" that pops the quick-guide. Size defaults to a finger-sized
    circle; pass size_hint/size to override for a given header."""

    def __init__(self, **kwargs):
        kwargs.setdefault("text", "?")
        kwargs.setdefault("bold", True)
        kwargs.setdefault("font_size", "20sp")
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(40), dp(40)))
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_down", "")
        # Yellow "?" on a red circle — the Node Medic hazard/attention look.
        kwargs.setdefault("color", theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        super().__init__(**kwargs)
        from kivy.graphics import Color, Ellipse, Line
        with self.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["red"]))
            self._circle = Ellipse(pos=self.pos, size=self.size)
            # a brighter red rim so the "circle around it" reads clearly on dark UI
            Color(*theme.hex_to_rgba("#ff1744"))
            self._rim = Line(width=dp(1.6))
        self.background_color = (0, 0, 0, 0)          # button rect stays invisible
        self.bind(pos=self._sync, size=self._sync)
        self.bind(on_release=lambda *_: open_guide_popup())

    def _sync(self, *_):
        self._circle.pos, self._circle.size = self.pos, self.size
        self._rim.circle = (self.center_x, self.center_y,
                            min(self.width, self.height) / 2 - dp(1))


def open_guide_popup():
    """Open the quick-guide in a dismissable popup (also usable standalone)."""
    body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(6))
    body.add_widget(build_guide_content())
    close = Button(text="Close", size_hint_y=None, height=dp(48), bold=True,
                   background_normal="",
                   background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                   color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
    body.add_widget(close)
    popup = Popup(title=g.TITLE, content=body, size_hint=(0.94, 0.9),
                  title_size="16sp",
                  separator_color=theme.hex_to_rgba(theme.COLORS["accent"]))
    close.bind(on_release=popup.dismiss)
    popup.open()
    return popup
