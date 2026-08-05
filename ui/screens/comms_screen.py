"""Communication apps — the medic hands a Reticulum messenger to a phone.

The medic is the mesh's post office (a propagation node); the messaging happens on
a PHONE running Columba or Sideband. This screen lists both carried apps and, on
"Send to my phone", serves the chosen APK over Wi-Fi behind a QR the phone scans.
It also shows whether store-and-forward is on (Home ▸ propagation), since that's
what lets the medic hold messages for phones that are offline.

Data comes from workflows.phone_apps (what's carried) over a LocalConnection; the
serve is workflows.phone_serve (in-process). All the heavy lifting is in those
tested modules — this is the view.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.i18n import tr  # i18n: wrapped — Comms labels/buttons
from ui.qr import qr_matrix
from ui.screens.birth_screen import QRCodeWidget


def _line(text, bold=False, size="15sp", color="text_primary", h=28):
    lbl = Label(text=text, bold=bold, font_size=theme.font_sp(size),
                halign="left", valign="middle",
                size_hint_y=None, color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
             texture_size=lambda i, ts: setattr(i, "height", max(dp(h), ts[1])))
    return lbl


class CommsScreen(BoxLayout):
    def __init__(self, cache_dir=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(8)
        from workflows.phone_apps import APPS_CACHE_DIR
        self._cache_dir = cache_dir or APPS_CACHE_DIR
        self._server = None

        self.add_widget(_line(tr("Communication apps"), bold=True, size="24sp", h=42))
        self.add_widget(_line(tr(
            "Node Medic hands a Reticulum messaging app to your phone — the medic is "
            "the mesh's post office, your phone is the messenger."),
            size="13.5sp", color="text_secondary", h=44))
        self.status = _line("", size="13sp", color="accent", h=40)
        self.add_widget(self.status)

        body = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        self.list.bind(minimum_height=self.list.setter("height"))
        body.add_widget(self.list)
        self.add_widget(body)

    def enter(self):
        """Called when the screen is shown — fetch carried apps + propagation state
        off-thread, then render."""
        self.status.text = tr("Checking what's carried…")
        self.list.clear_widgets()

        def work():
            from transport.connection import LocalConnection
            from workflows.phone_apps import cached_apps
            from workflows.node_mode import current_mode, load_home_profile
            conn = LocalConnection()
            apps = cached_apps(conn, self._cache_dir)
            try:
                store_on = (current_mode(conn) == "home"
                            and load_home_profile() == "propagation")
            except Exception:
                store_on = False
            Clock.schedule_once(lambda dt: self._render(apps, store_on), 0)
        threading.Thread(target=work, daemon=True).start()

    def _render(self, apps, store_on):
        self.status.text = (
            tr("Store-and-forward is ON — the medic holds messages for phones that are "
               "offline.") if store_on else
            tr("Note: message store-and-forward is OFF. Switch to Home ▸ full "
               "propagation node so the medic can hold messages for offline phones."))
        self.status.color = theme.hex_to_rgba(
            theme.COLORS["green" if store_on else "warning_yellow"])
        self.list.clear_widgets()
        for app in apps:
            self.list.add_widget(self._card(app))

    def _card(self, app):
        card = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4),
                         padding=dp(12))
        card.bind(minimum_height=card.setter("height"))
        from kivy.graphics import Color, RoundedRectangle
        with card.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            card._bg = RoundedRectangle(radius=[dp(10)] * 4)
        card.bind(pos=lambda i, v: setattr(i._bg, "pos", i.pos),
                  size=lambda i, v: setattr(i._bg, "size", i.size))

        head = f"{app['name']}"
        if app.get("carried") and app.get("version"):
            head += f"   {app['version']}"
        card.add_widget(_line(head, bold=True, size="19sp", h=28))
        card.add_widget(_line(app["blurb"], size="13.5sp", color="text_secondary", h=44))
        card.add_widget(_line(tr("Licence: {lic}").format(lic=app['license']),
                              size="11.5sp", color="text_secondary", h=20))

        if app.get("carried"):
            btn = Button(text=tr("Send to my phone"), size_hint_y=None, height=dp(50),
                         bold=True, font_size="16sp", background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            btn.bind(on_release=lambda *_: self._send(app, card))
            card.add_widget(btn)
        else:
            card.add_widget(_line(tr("Not carried yet — refresh it while the medic is "
                                     "online (Settings ▸ Storage)."), size="12.5sp",
                                  color="warning_yellow", h=34))
        return card

    def _send(self, app, card):
        """Start the local server and show a QR of the download URL under the card."""
        if getattr(card, "_qr_open", False):
            return
        card._qr_open = True
        from workflows.phone_serve import AppServer
        if self._server is None:
            self._server = AppServer(self._cache_dir)
        url = None
        try:
            self._server.start()
            url = self._server.url_for(app["file"])
        except Exception:
            url = None
        panel = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        panel.bind(minimum_height=panel.setter("height"))
        if not url:
            panel.add_widget(_line(tr(
                "Get the medic and the phone on the SAME Wi-Fi first (or the medic's "
                "hotspot), then try again."), size="13sp", color="warning_yellow", h=44))
        else:
            panel.add_widget(_line(tr("On your phone: join the medic's Wi-Fi, scan this, "
                                      "then allow install from unknown sources."),
                                   size="12.5sp", color="text_secondary", h=40))
            matrix = qr_matrix(url)
            if matrix:
                holder = AnchorLayout(anchor_x="center", size_hint_y=None)
                qr = QRCodeWidget(matrix)
                holder.height = qr.height + dp(10)
                holder.add_widget(qr)
                panel.add_widget(holder)
            panel.add_widget(_line(url, size="12sp", color="accent", h=24))
        card.add_widget(panel)
