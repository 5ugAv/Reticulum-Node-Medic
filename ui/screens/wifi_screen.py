"""WiFi connect — join a phone hotspot or venue AP in the field.

Offline-first, but a link unlocks address geocoding, firmware refresh and map
top-ups. Scan → tap a network → (password) → Connect. The nmcli calls block, so
they run off-thread and post back via the Kivy Clock. Logic lives in
provisioning.wifi (unit-tested); this is the touchscreen over it.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from ui import theme
from provisioning import wifi


def _line(text, bold=False, size="15sp", color="text_primary", h=26):
    lbl = Label(text=text, bold=bold, font_size=size, halign="left", valign="middle",
                size_hint_y=None, height=dp(h),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class WifiScreen(BoxLayout):
    """Scan + connect to WiFi. *run* is injectable for tests."""

    def __init__(self, run=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(8)
        self.padding = dp(12)
        self._run = run
        self._busy = False
        self._selected = None

        self.add_widget(_line("WiFi", bold=True, size="22sp"))
        self.status = _line("", size="14sp", color="text_secondary", h=24)
        self.add_widget(self.status)

        self.scan_btn = Button(text="Scan for networks", size_hint_y=None,
                               height=dp(48), bold=True, background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                               color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.scan_btn.bind(on_release=lambda *_: self._scan())
        self.add_widget(self.scan_btn)

        scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.list.bind(minimum_height=self.list.setter("height"))
        scroll.add_widget(self.list)
        self.add_widget(scroll)

        # password + connect row (revealed when a secured network is picked)
        self.pw_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height=dp(0), spacing=dp(6), opacity=0)
        self.pw_in = TextInput(hint_text="password", multiline=False, password=True,
                               font_size="16sp")
        self.connect_btn = Button(text="Connect", size_hint_x=None, width=dp(120),
                                  bold=True, background_normal="",
                                  background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                                  color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.connect_btn.bind(on_release=lambda *_: self._connect())
        self.pw_row.add_widget(self.pw_in)
        self.pw_row.add_widget(self.connect_btn)
        self.add_widget(self.pw_row)

        self._refresh_status()

    # -- status -------------------------------------------------------------

    def _refresh_status(self):
        def work():
            cur = wifi.current_connection(**self._kw())
            Clock.schedule_once(lambda dt: self._show_status(cur), 0)
        threading.Thread(target=work, daemon=True).start()

    def _kw(self):
        return {"run": self._run} if self._run else {}

    def _show_status(self, cur):
        if cur:
            self.status.text = f"Connected: {cur['ssid']}" + (
                f"  ({cur['ip']})" if cur.get("ip") else "")
            self.status.color = theme.hex_to_rgba(theme.COLORS["green"])
        else:
            self.status.text = "Not connected — scan and pick a network."
            self.status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])

    # -- scan ---------------------------------------------------------------

    def _scan(self):
        if self._busy:
            return
        self._busy = True
        self.scan_btn.text = "Scanning…"
        self.list.clear_widgets()

        def work():
            nets = wifi.scan_networks(**self._kw())
            Clock.schedule_once(lambda dt: self._show_networks(nets), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show_networks(self, nets):
        self._busy = False
        self.scan_btn.text = "Scan for networks"
        if not nets:
            self.list.add_widget(_line("No networks found.", color="amber"))
            return
        for n in nets:
            tag = ("   • connected" if n["active"]
                   else ("" if n["secure"] else "   (open)"))
            btn = Button(text=f"{n['ssid']}    {n['signal']}%{tag}",
                         size_hint_y=None, height=dp(46), halign="left",
                         background_normal="", background_color=theme.hex_to_rgba(
                             theme.COLORS["surface"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            btn.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(16), v[1])))
            btn.bind(on_release=lambda *_a, net=n: self._select(net))
            self.list.add_widget(btn)

    # -- select + connect ---------------------------------------------------

    def _select(self, net):
        self._selected = net
        if net["secure"]:
            self.pw_row.height, self.pw_row.opacity = dp(48), 1
            self.pw_in.text = ""
            self.status.text = f"Enter password for {net['ssid']}, then Connect."
            self.status.color = theme.hex_to_rgba(theme.COLORS["text_primary"])
        else:
            self.pw_row.height, self.pw_row.opacity = dp(0), 0
            self._connect()                           # open network — join directly

    def _connect(self):
        if self._busy or not self._selected:
            return
        self._busy = True
        ssid = self._selected["ssid"]
        pw = self.pw_in.text if self._selected["secure"] else ""
        self.status.text = f"Connecting to {ssid}…"
        self.status.color = theme.hex_to_rgba(theme.COLORS["accent"])

        def work():
            ok, msg = wifi.connect(ssid, pw, **self._kw())
            Clock.schedule_once(lambda dt: self._show_result(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, ok, msg):
        self._busy = False
        self.status.text = msg
        self.status.color = theme.hex_to_rgba(theme.COLORS["green" if ok else "red"])
        if ok:
            self.pw_row.height, self.pw_row.opacity = dp(0), 0
            self._refresh_status()
