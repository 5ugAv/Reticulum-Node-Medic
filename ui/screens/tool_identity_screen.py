"""Settings ▸ Tool identity (item 2, read-only).

Shows this Node Medic's own Reticulum identity hash, its tool name, its born date,
and — if it was cloned via MITOSIS — which parent unit it descended from. The
identity hash is read live off disk (a quick subprocess), so it's fetched on a
thread and posted back.
"""

from __future__ import annotations

import threading
from datetime import datetime

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from ui import theme
from ui.i18n import tr  # i18n: wrapped — every literal this screen draws
from provisioning import tool_identity as ti


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


def _field(title, value, mono=False, value_color="text_primary"):
    box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2),
                    padding=[0, dp(4)])
    box.bind(minimum_height=box.setter("height"))
    box.add_widget(_line(title, size="12.5sp", color="accent", bold=True, h=20))
    v = _line(value, size="16sp", color=value_color, mono=mono)
    v.size_hint_y = None
    v.bind(texture_size=lambda i, ts: setattr(i, "height", max(dp(24), ts[1])))
    box.add_widget(v)
    return box, v


class ToolIdentityScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(10)
        self.add_widget(_line(tr("Tool identity"), bold=True, size="22sp", h=40))

        self._name_box, self._name_v = _field(tr("Tool name"), "…")
        self.add_widget(self._name_box)
        self._hash_box, self._hash_v = _field(tr("Reticulum identity"), tr("reading…"),
                                              mono=True)
        self.add_widget(self._hash_box)
        self._born_box, self._born_v = _field(tr("Made"), "…")
        self.add_widget(self._born_box)
        self._parent_box, self._parent_v = _field(tr("Lineage"), "…")
        self.add_widget(self._parent_box)

        from kivy.uix.widget import Widget
        self.add_widget(Widget())                    # push to top
        self._load()

    def _load(self):
        def work():
            s = ti.summary()
            Clock.schedule_once(lambda dt: self._show(s), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show(self, s):
        self._name_v.text = s["name"]
        self._hash_v.text = (s["identity_hash"]
                             or tr("(not available — is Reticulum set up?)"))
        if s["born"]:
            self._born_v.text = datetime.fromtimestamp(s["born"]).strftime("%d %b %Y")
        else:
            self._born_v.text = tr("unknown")
        par = s["parent"]
        if par:
            h = f"  ({par['hash']})" if par.get("hash") else ""
            self._parent_v.text = tr("Cloned from {name}").format(
                name=par.get('name') or tr("another unit")) + h
        else:
            self._parent_v.text = tr("Original unit — not cloned from another")
