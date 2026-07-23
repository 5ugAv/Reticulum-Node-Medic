"""Settings ▸ Trusted operators (item 7).

The family tree of Node Medic units this tool knows — its own unit, the units it
cloned, and any units discovered descending from them. Each shows name, identity
hash, and when/how trust was established. Trusted units' birthed nodes appear as
kin on VITALS/SCAN; revoking a unit (with a confirmation) drops its nodes to
neighbour. Descendants of a trusted unit are NOT trusted automatically — they show
as "untrusted — descended from [X]" and need manual approval (trust is never
transitive).
"""

from __future__ import annotations

from datetime import datetime

from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from ui import theme
from monitor import trust

_STATUS = {
    "self": ("YOU", "accent"),
    "trusted": ("TRUSTED", "green"),
    "untrusted": ("UNTRUSTED", "warning_yellow"),
}


def _line(text, size="15sp", color="text_primary", bold=False, h=None, mono=False):
    lbl = Label(text=text, font_size=size, bold=bold, halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]),
                font_name="RobotoMono-Regular" if mono else "Roboto")
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class TrustedOperatorsScreen(BoxLayout):
    """``on_change`` (optional) is called after trust/revoke so the app can
    re-classify kin on the running registry."""

    def __init__(self, on_change=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(14)
        self.spacing = dp(8)
        self._on_change = on_change
        self.add_widget(_line("Trusted operators", bold=True, size="22sp", h=40))
        self.add_widget(_line(
            "Node Medic units and the trust between them. Trust is per-unit and "
            "never inherited — a clone of a clone must be approved by you.",
            size="13sp", color="text_secondary", h=40))
        body = ScrollView()
        self._list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        self._list.bind(minimum_height=self._list.setter("height"))
        body.add_widget(self._list)
        self.add_widget(body)
        self._refresh()

    def _refresh(self):
        self._list.clear_widgets()
        us = trust.units()
        if not us:
            self._list.add_widget(_line(
                "No other units yet. When you clone this medic (MITOSIS), the new "
                "unit appears here.", size="13.5sp", color="text_secondary", h=44))
            return
        for u in us:
            self._list.add_widget(self._card(u))

    def _card(self, u):
        label, colname = _STATUS.get(u["status"], ("?", "surface"))
        card = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(3),
                         padding=dp(12))
        card.bind(minimum_height=card.setter("height"))
        with card.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            rect = RoundedRectangle(radius=[dp(10)] * 4)
        card.bind(pos=lambda *_: setattr(rect, "pos", card.pos),
                  size=lambda *_: setattr(rect, "size", card.size))

        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(28))
        head.add_widget(_line(u["name"], bold=True, size="17sp"))
        pill = Label(text=label, bold=True, font_size="11sp", size_hint_x=None,
                     width=dp(96), color=theme.hex_to_rgba(theme.COLORS[colname]))
        head.add_widget(pill)
        card.add_widget(head)

        if u["hash"]:
            card.add_widget(_line(u["hash"], size="12sp", color="text_secondary",
                                  mono=True, h=20))
        via = u.get("via", "")
        if u["status"] == "untrusted" and u.get("parent_name"):
            via = f"descended from {u['parent_name']} — approve to trust"
        when = ""
        if u.get("established_at"):
            when = "  ·  " + datetime.fromtimestamp(u["established_at"]).strftime("%d %b %Y")
        card.add_widget(_line(f"{via}{when}", size="12.5sp", color="text_secondary", h=20))

        if u["status"] == "trusted":
            card.add_widget(self._btn("Revoke trust", "red",
                                      lambda: self._confirm_revoke(u)))
        elif u["status"] == "untrusted":
            card.add_widget(self._btn("Approve — trust this unit", "green",
                                      lambda: self._approve(u)))
        return card

    def _btn(self, text, color, on_tap):
        b = Button(text=text, size_hint_y=None, height=dp(44), bold=True,
                   font_size="14sp", background_normal="",
                   background_color=theme.hex_to_rgba(theme.COLORS[color]),
                   color=theme.hex_to_rgba(theme.COLORS["background"]))
        b.bind(on_release=lambda *_: on_tap())
        return b

    def _approve(self, u):
        trust.trust(u["hash"])
        self._changed()

    def _confirm_revoke(self, u):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=(
            f"Revoke trust in [b]{u['name']}[/b]?\n\n"
            "Nodes birthed by this unit will no longer appear as kin on your VITALS "
            "and SCAN — they drop to neighbour status. You can re-approve it later."))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title="Revoke trust", content=box, size_hint=(0.88, 0.55))
        cancel = Button(text="Cancel", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        confirm = Button(text="Revoke", bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _do(*_):
            popup.dismiss()
            trust.revoke(u["hash"])
            self._changed()
        confirm.bind(on_release=_do)
        row.add_widget(cancel)
        row.add_widget(confirm)
        box.add_widget(row)
        popup.open()

    def _changed(self):
        self._refresh()
        if self._on_change:
            self._on_change()
