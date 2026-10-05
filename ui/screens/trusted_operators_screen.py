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
from ui.i18n import tr  # i18n: wrapped — every literal this screen draws
from ui.text_fit import grow_to_text
from monitor import trust

#: Pill label (an English source string, translated with tr() where it is
#: drawn — never at import time, the language is chosen at runtime) + colour.
_STATUS = {
    "self": ("YOU", "accent"),
    "trusted": ("TRUSTED", "green"),
    "untrusted": ("UNTRUSTED", "warning_yellow"),
}


def _line(text, size="15sp", color="text_primary", bold=False, h=None, mono=False):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]),
                font_name="RobotoMono-Regular" if mono else "Roboto")
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(max(h, theme.line_dp(size, mono=mono)))
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
        self.add_widget(_line(tr("Trusted operators"), bold=True, size="22sp", h=40))
        self.add_widget(grow_to_text(_line(
            tr("Node Medic units and the trust between them. Trust is per-unit and "
               "never inherited — a clone of a clone must be approved by you."),
            size="13sp", color="text_secondary")))
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
            self._list.add_widget(grow_to_text(_line(
                tr("No other units yet. When you clone this medic (the Clone button "
                   "under BUILD), the new unit appears here."),
                size="13.5sp", color="text_secondary")))
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
        pill = Label(text=tr(label), bold=True, font_size="11sp", size_hint_x=None,
                     width=dp(96), color=theme.hex_to_rgba(theme.COLORS[colname]))
        head.add_widget(pill)
        card.add_widget(head)

        if u["hash"]:
            card.add_widget(_line(u["hash"], size="12sp", color="text_secondary",
                                  mono=True, h=20))
        via = tr(u["via"]) if u.get("via") else ""   # "this unit", "cloned from this unit"
        if u["status"] == "untrusted" and u.get("revoked"):
            via = tr("trust revoked — was {via}").format(via=via or tr("known"))
        elif u["status"] == "untrusted" and u.get("parent_name"):
            via = tr("descended from {name} — approve to trust").format(
                name=u['parent_name'])
        when = ""
        if u.get("established_at"):
            when = "  ·  " + datetime.fromtimestamp(u["established_at"]).strftime("%d\u00a0%b\u00a0%Y")
        card.add_widget(grow_to_text(_line(f"{via}{when}", size="12.5sp",
                                           color="text_secondary")))

        if u["status"] == "trusted":
            card.add_widget(self._btn(tr("Revoke trust"), "red",
                                      lambda: self._confirm_revoke(u)))
        elif u["status"] == "untrusted":
            # Two answers, not one: approve, or FORGET. A revoked or unknown
            # unit used to offer only the green button — no way to say no
            # (operator, 2026-09-21). Forgetting is not trusting: heard again,
            # the unit comes back here needing approval.
            card.add_widget(self._pair_row(
                self._btn(tr("Approve — trust this unit"), "green",
                          lambda: self._approve(u)),
                self._btn(tr("Forget this unit"), "red",
                          lambda: self._confirm_forget(u))))
        return card

    def _btn(self, text, color, on_tap):
        b = Button(text=text, size_hint_y=None, height=dp(44), bold=True,
                   font_size="14sp", background_normal="", halign="center",
                   valign="middle",
                   background_color=theme.hex_to_rgba(theme.COLORS[color]),
                   color=theme.hex_to_rgba(theme.COLORS["background"]))
        # The caption wraps inside the button and the button grows to it: in
        # French, German, Russian and Swedish "Approve — trust this unit" ran
        # past the edges of a half-row button (2026-10-05).
        b.bind(width=lambda i, w: setattr(i, "text_size", (max(0, w - dp(16)), None)),
               texture_size=lambda i, ts: setattr(i, "height", max(dp(44), ts[1] + dp(16))))
        b.bind(on_release=lambda *_: on_tap())
        return b

    @staticmethod
    def _pair_row(left, right):
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(44), spacing=dp(8))
        row.add_widget(left)
        row.add_widget(right)

        def _grow(*_):
            row.height = max(left.height, right.height)
        left.bind(height=_grow)
        right.bind(height=_grow)
        _grow()
        return row

    def _approve(self, u):
        trust.trust(u["hash"])
        self._changed()

    def _confirm_forget(self, u):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        box.add_widget(Label(
            text=(tr("Forget [b]{name}[/b]?").format(name=u['name']) + "\n\n"
                  + tr("It disappears from this list. Nothing is trusted by "
                       "forgetting: if this unit is ever heard again it comes back "
                       "here as untrusted, needing your approval.")),
            markup=True, halign="center", valign="middle"))
        box.children[0].bind(size=lambda i, v: setattr(i, "text_size", v))
        btns = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title=tr("Forget this unit"), content=box, size_hint=(0.88, 0.5))
        cancel = Button(text=tr("Cancel"), background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        confirm = Button(text=tr("Forget"), bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _do(*_):
            popup.dismiss()
            trust.forget(u["hash"])
            self._changed()
        confirm.bind(on_release=_do)
        btns.add_widget(cancel)
        btns.add_widget(confirm)
        box.add_widget(btns)
        popup.open()

    def _confirm_revoke(self, u):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=(
            tr("Revoke trust in [b]{name}[/b]?").format(name=u['name']) + "\n\n"
            + tr("Nodes birthed by this unit will no longer appear as kin on your VITALS "
                 "and MAPS — they drop to neighbour status. You can re-approve it later.")))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title=tr("Revoke trust"), content=box, size_hint=(0.88, 0.55))
        cancel = Button(text=tr("Cancel"), background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        confirm = Button(text=tr("Revoke"), bold=True, background_normal="",
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
