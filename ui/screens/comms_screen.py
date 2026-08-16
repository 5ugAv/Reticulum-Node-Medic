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
        self._banner = None   # one-shot download outcome, consumed by _render

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
        banner = getattr(self, "_banner", None)
        self._banner = None
        if banner:
            self.status.text, colour = banner
            self.status.color = theme.hex_to_rgba(theme.COLORS[colour])
            self.list.clear_widgets()
            for app in apps:
                self.list.add_widget(self._card(app))
            return
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
            # THE SENTENCE THAT WAS HERE POINTED AT A BUTTON THAT DID NOT EXIST.
            # It read "refresh it while the medic is online (Settings ▸ Storage)" —
            # but Settings ▸ Storage has never synced phone apps, and nothing in
            # the app called workflows.phone_apps.sync_all at all. The downloader
            # was complete, tested, and unreachable. Found live on the medic
            # 2026-08-16 with assets/apps holding nothing but .gitkeep, dated the
            # day the directory was created.
            #
            # The refresh belongs HERE, next to the app that is missing, because
            # this screen is where anyone discovers it is missing.
            card.add_widget(_line(tr("Not carried yet."), size="12.5sp",
                                  color="warning_yellow", h=22))
            btn = Button(text=tr("Download it now"), size_hint_y=None, height=dp(50),
                         bold=True, font_size="16sp", background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            note = _line(tr("Needs the internet. It is a large download — do it "
                            "before you leave."), size="11.5sp",
                         color="text_secondary", h=32)
            btn.bind(on_release=lambda *_: self._fetch(app, card, btn, note))
            card.add_widget(btn)
            card.add_widget(note)
        return card

    def _fetch(self, app, card, btn, note):
        """Download one app's APK off-thread, then re-render the screen.

        Deliberately per-app rather than a single "refresh everything": the two
        catalogue apps are ~224 MB together at current picks, and an operator who
        wants one messenger should not be made to carry both.
        """
        if getattr(card, "_fetching", False):
            return
        card._fetching = True
        btn.disabled = True
        btn.text = tr("Downloading {name}…").format(name=app["name"])
        note.text = tr("This can take several minutes. Leave the screen open.")

        def work():
            try:
                from transport.connection import LocalConnection
                from workflows.phone_apps import sync_app
                res = sync_app(app["key"], LocalConnection(), self._cache_dir)
                ok, msg = (not res.failed), res.message
                if not res.online and not msg:
                    msg = tr("The medic is offline — connect it to the internet, "
                             "then try again.")
            except Exception as exc:                              # noqa: BLE001
                ok, msg = False, tr("Download failed: {err}").format(err=exc)
            Clock.schedule_once(lambda dt: self._fetched(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _fetched(self, ok, message):
        """Report the outcome, then re-read what is actually on disk.

        The outcome is handed to the next _render rather than written to the label
        here: enter() reloads off-thread and _render would otherwise overwrite it a
        moment later, which reads as the message flashing and vanishing.

        The banner says what the download reported; the cards below it are rebuilt
        from `ls`, so a card that flips to "Send to my phone" is evidence the file
        landed, not a claim that it did."""
        self._banner = (message or (tr("Done.") if ok else tr("Nothing downloaded.")),
                        "green" if ok else "warning_yellow")
        self.enter()

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
            # ANDROID REFUSES TWICE, and neither refusal explains itself. The
            # old one-liner said "allow install from unknown sources", which
            # assumes the operator knows how — and on Android 8+ there is no
            # such global setting any more: permission is PER-APP and only
            # appears part-way through the install, worded as a flat "can't
            # install" (operator, 2026-08-06: "the phone will say unknown app
            # can't install ... there needs to be a short instruction").
            #
            # So the two scary screens are named, in the order they appear,
            # with the button to press on each. Someone who has never sideloaded
            # reads the first warning as "this is malware" and stops.
            panel.add_widget(_line(tr("On your phone — Android"), bold=True,
                                   size="13.5sp", color="accent", h=24))
            # NAME the network. "Join the medic's Wi-Fi" sounds like the medic
            # broadcasts one — it does not: it serves the APK at its OWN address
            # on whatever network it is already joined to, so what the phone
            # needs is to be on THAT SAME network (operator, 2026-08-06: "join
            # the medic's Wi-Fi. What does that mean?"). The medic knows the
            # name, so there is no reason to make anyone guess it.
            try:
                from workflows.phone_serve import current_ssid
                ssid = current_ssid()
            except Exception:                                     # noqa: BLE001
                ssid = None
            step_one = (
                tr("1.  Put your phone on the same Wi-Fi as this medic: {ssid}"
                   ).format(ssid=ssid) if ssid else
                tr("1.  Put your phone on the same Wi-Fi network this medic "
                   "is using."))
            for step in (
                step_one,
                # WHY, because a QR code normally means "open this web link" and
                # this one does not. The medic IS the server: the code points at
                # its own address on the local network (192.168.x.x), which
                # nothing outside that network can reach. A phone with perfect
                # mobile signal and no shared Wi-Fi will fail, and the operator
                # would have no way to guess why (2026-08-06: "doesn't the qr
                # code just require Internet connection of any sort?").
                tr("     The app comes from this medic, not the internet — "
                   "mobile data alone cannot reach it."),
                tr("2.  The browser warns the file may be harmful — "
                   "choose Download anyway."),
                tr("3.  Open the downloaded file. Android says it is not "
                   "allowed to install unknown apps — tap Settings."),
                tr("4.  Turn on Allow from this source, go back, "
                   "then tap Install."),
            ):
                panel.add_widget(_line(step, size="12.5sp",
                                       color="text_secondary", h=34))
            panel.add_widget(_line(tr(
                "Both warnings are normal: they appear for any app not from the "
                "Play Store. This one came from the medic in front of you."),
                size="12sp", color="text_secondary", h=32))
            # The field answer when there is no shared network to join. The
            # medic JOINS networks (provisioning/wifi.py — RTNode setup APs,
            # phone hotspots, venue Wi-Fi); it never raises one of its own, so
            # the phone has to be the hotspot.
            panel.add_widget(_line(tr(
                "No Wi-Fi you both can join? Turn on your phone's hotspot, then "
                "connect this medic to it in Settings ▸ Wi-Fi."),
                size="12sp", color="text_secondary", h=32))
            matrix = qr_matrix(url)
            if matrix:
                holder = AnchorLayout(anchor_x="center", size_hint_y=None)
                qr = QRCodeWidget(matrix)
                holder.height = qr.height + dp(10)
                holder.add_widget(qr)
                panel.add_widget(holder)
            panel.add_widget(_line(url, size="12sp", color="accent", h=24))
        card.add_widget(panel)
