"""Settings ▸ Reticulum & radio guide — the plain-language reference for what the
three node roles are, the golden placement rule, and the canonical radio params.

Also reachable inline from a "?" during setups (via ui.widgets.help_button, which
shows the same content in a popup). Content lives in provisioning.network_guide;
this screen just frames it with a heading.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from ui import theme
from ui.widgets.guide_content import build_guide_content
from provisioning import network_guide as g


class GuideScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(8)
        title = Label(text=g.TITLE, bold=True, font_size="21sp", halign="left",
                      valign="middle", size_hint_y=None, height=dp(52),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        title.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(title)
        self.add_widget(build_guide_content())
