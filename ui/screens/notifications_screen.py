"""Notifications settings — OPTIONALLY push node-down escalations to the operator's
own Reticulum address (Sideband / Columba / any LXMF client).

The medic always alerts on its own screen; this second tier only *also* messages the
operator when they've saved an address here. Blank = medic-only. See
``monitor.operator_alert``.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr  # i18n: wrapped — Notifications labels/buttons/status
from ui.onscreen_keyboard import bind_field
from monitor.operator_alert import (
    load_operator_address, save_operator_address, valid_address)


def _lbl(text, size="14sp", color="text_secondary", h=None, bold=False):
    lbl = Label(text=text, font_size=theme.font_sp(size), halign="left",
                valign="top", bold=bold,
                color=theme.hex_to_rgba(theme.COLORS[color]), size_hint_y=None)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    if h:
        lbl.height = dp(max(h, theme.line_dp(size)))
    else:
        lbl.bind(texture_size=lambda i, v: setattr(i, "height", v[1]))
    return lbl


class NotificationsScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(18)
        self.spacing = dp(12)

        self.add_widget(_lbl(tr("Notifications"), size="22sp", color="text_primary",
                             h=40, bold=True))
        self.add_widget(_lbl(tr(
            "The Node Medic always alerts on its own screen. It can ALSO message you "
            "when a node has been unreachable for 3 days and needs a physical check — "
            "sent to your Reticulum address, so it reaches Sideband, Columba, or any "
            "LXMF app on your phone. This is optional."), h=120))
        self.add_widget(_lbl(tr("Your Reticulum / LXMF address"), size="13sp",
                             color="accent", h=24))

        self.field = TextInput(
            text=load_operator_address(), multiline=False,
            hint_text=tr("32-character address (leave blank for medic-only)"),
            size_hint_y=None, height=dp(48), font_size="27sp")
        bind_field(self.field)
        self.add_widget(self.field)

        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(10))
        save = Button(text=tr("Save"), bold=True, background_normal="",
                      background_down="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        save.bind(on_release=lambda *_: self._save())
        clear = Button(text=tr("Clear"), bold=True, background_normal="",
                       background_down="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        clear.bind(on_release=lambda *_: self._clear())
        row.add_widget(save)
        row.add_widget(clear)
        self.add_widget(row)

        self.status = _lbl("", size="13sp", h=30)
        self.add_widget(self.status)
        self.add_widget(_lbl(tr(
            "Find your address in Sideband/Columba under your identity — the "
            "32-character hex hash. We only use it to send these node alerts."),
            size="12sp", h=64))
        self.add_widget(Widget())

    def _set_status(self, text, color):
        self.status.text = text
        self.status.color = theme.hex_to_rgba(theme.COLORS[color])

    def _save(self):
        raw = self.field.text.strip()
        if not raw:
            self._clear()
            return
        if not valid_address(raw):
            self._set_status(tr("That doesn't look like a valid Reticulum address "
                                "(needs 32 hex characters)."), "amber")
            return
        try:
            self.field.text = save_operator_address(raw)
        except Exception as e:      # noqa: BLE001 — a failed write must SHOW
            self._set_status(tr("Couldn't save: ") + str(e)[:90], "red")
            return
        self._set_status(tr("Saved — you'll get a message when a node needs checking."),
                         "green")

    def _clear(self):
        self.field.text = ""
        try:
            save_operator_address("")
        except Exception as e:      # noqa: BLE001
            self._set_status(tr("Couldn't save: ") + str(e)[:90], "red")
            return
        self._set_status(tr("Cleared — alerts stay on the medic only."), "text_secondary")
