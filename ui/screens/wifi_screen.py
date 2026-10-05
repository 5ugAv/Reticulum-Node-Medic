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
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput

from ui import theme
from ui.i18n import tr  # i18n: wrapped — WiFi screen labels/status/buttons
from ui.onscreen_keyboard import bind_field
from provisioning import wifi


def _line(text, bold=False, size="15sp", color="text_primary", h=26):
    lbl = Label(text=text, bold=bold, font_size=theme.font_sp(size),
                halign="left", valign="middle",
                size_hint_y=None,
                height=dp(max(h, theme.line_dp(size))),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


def _row_label(n):
    """The tappable text for one scanned network row (pure — unit-tested).

    Kept out of the widget code so the string the operator taps is testable
    without a Kivy Window (Widget.__init__ needs one)."""
    tag = ("   " + tr("• connected") if n["active"]
           else ("" if n["secure"] else "   " + tr("(open)")))
    return f"{n['ssid']}    {n['signal']}%{tag}"


class WifiScreen(BoxLayout):
    """Scan + connect to WiFi. *run* is injectable for tests."""

    def __init__(self, run=None, on_done=None, **kwargs):
        super().__init__(**kwargs)
        self._on_done = on_done            # back to Settings once connected
        self.orientation = "vertical"
        self.spacing = dp(8)
        self.padding = dp(12)
        self._run = run
        self._busy = False
        self._selected = None

        # Title row with the radio switch (keeper, 2026-10-06): one place to
        # turn the medic's Wi-Fi off — for an offline clone test or a quiet
        # site — and back on. NetworkManager keeps it off across reboots.
        # Same switch and row as Settings' screen saver / auto-backpack rows:
        # words on the left, the standard Switch (blue on, grey off) on the right.
        self.add_widget(_line(tr("Wi-Fi"), bold=True, size="22sp"))
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                         spacing=dp(10))
        head.add_widget(_line(tr("Turn Wi-Fi on or off"), size="14sp", h=44))
        self.radio = Switch(active=True, size_hint_x=None, width=dp(90))
        self.radio.bind(active=lambda _w, v: self._radio_changed(v))
        head.add_widget(self.radio)
        self.add_widget(head)
        self._radio_quiet = False          # set while we move the switch ourselves
        self.status = _line("", size="14sp", color="text_secondary", h=24)
        self.status.size_hint_y = None
        self.status.bind(texture_size=lambda i, v: setattr(
            i, "height", max(dp(24), v[1] + dp(4))))
        self.add_widget(self.status)

        self.scan_btn = Button(text=tr("Search for Wi-Fi networks"), size_hint_y=None,
                               height=dp(48), bold=True, background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                               color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.scan_btn.bind(on_release=lambda *_: self._scan())
        self.add_widget(self.scan_btn)

        # do_scroll_x=False: a vertical network list must never scroll sideways.
        # Leaving it on lets the ScrollView measure horizontal content extent,
        # which (with a text_size that didn't match the row width) gave the layout
        # a width to oscillate on — a redraw storm. The list fills the width
        # (size_hint_x=1) and only its height tracks its contents.
        scroll = ScrollView(do_scroll_x=False)
        self.list = BoxLayout(orientation="vertical", size_hint=(1, None), spacing=dp(4))
        self.list.bind(minimum_height=self.list.setter("height"))
        scroll.add_widget(self.list)
        self.add_widget(scroll)

        # password + connect row (revealed when a secured network is picked)
        self.pw_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height=dp(0), spacing=dp(6), opacity=0)
        self.pw_in = TextInput(hint_text=tr("password"), multiline=False, password=True,
                               font_size="27sp")
        bind_field(self.pw_in)                       # pop the on-screen keyboard
        # Show/Hide toggle so the operator can check the password for typos.
        self.show_btn = Button(text=tr("Show"), size_hint_x=None, width=dp(78), bold=True,
                               background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                               color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self.show_btn.bind(on_release=lambda *_: self._toggle_pw())
        self.connect_btn = Button(text=tr("Connect"), size_hint_x=None, width=dp(120),
                                  bold=True, background_normal="",
                                  background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                                  color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.connect_btn.bind(on_release=lambda *_: self._connect())
        self.pw_row.add_widget(self.pw_in)
        self.pw_row.add_widget(self.show_btn)
        self.pw_row.add_widget(self.connect_btn)
        self.add_widget(self.pw_row)

        # Auto-reconnect toggle (revealed with the password row) — on = the medic
        # rejoins this network by itself after a reboot/power loss (home + field
        # hotspots); off = a one-off network it shouldn't cling to.
        self.autoconn_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                      height=dp(0), spacing=dp(6), opacity=0)
        self.autoconn_row.add_widget(_line(tr("Reconnect automatically"), size="14sp"))
        self.autoconnect = Switch(active=True, size_hint_x=None, width=dp(90))
        self.autoconn_row.add_widget(self.autoconnect)
        self.add_widget(self.autoconn_row)

        self._refresh_status()

    def enter(self):
        """Shown (screen on_enter): refresh status AND auto-search, so available
        networks appear without having to hunt for a button."""
        self._refresh_status()
        self._scan()

    def _toggle_pw(self):
        """Reveal / mask the password field so the operator can check for typos."""
        self.pw_in.password = not self.pw_in.password
        self.show_btn.text = tr("Hide") if not self.pw_in.password else tr("Show")

    # -- status -------------------------------------------------------------

    def _refresh_status(self):
        def work():
            on = wifi.radio_enabled(**self._kw())
            cur = wifi.current_connection(**self._kw()) if on else None
            Clock.schedule_once(lambda dt: self._show_radio(on, cur), 0)
        threading.Thread(target=work, daemon=True).start()

    # -- radio on/off ---------------------------------------------------------

    def _set_switch(self, on):
        self._radio_quiet = True
        self.radio.active = on
        self._radio_quiet = False

    def _show_radio(self, on, cur):
        self._set_switch(on)
        self.scan_btn.disabled = not on
        self.scan_btn.opacity = 1 if on else 0.4
        if on:
            self._show_status(cur)
        else:
            self.list.clear_widgets()
            self.status.text = tr("Wi-Fi is off. This medic will not join any "
                                  "network until you switch it back on here.")
            self.status.color = theme.hex_to_rgba(theme.COLORS["amber"])

    def _radio_changed(self, on):
        if self._radio_quiet or self._busy:
            return
        self._busy = True
        self.status.text = tr("Switching Wi-Fi on…") if on else tr("Switching Wi-Fi off…")

        def work():
            ok, why = wifi.set_radio(on, **self._kw())
            Clock.schedule_once(lambda dt: self._radio_done(on, ok, why), 0)
        threading.Thread(target=work, daemon=True).start()

    def _radio_done(self, on, ok, why):
        self._busy = False
        if not ok:
            self._set_switch(not on)
            self.status.text = (
                tr("This medic needs a one-time permission update before it "
                   "can switch its own Wi-Fi.")
                if why == "permission" else
                tr("Could not switch the Wi-Fi: {why}").format(why=why))
            self.status.color = theme.hex_to_rgba(theme.COLORS["amber"])
            return
        if on:
            Clock.schedule_once(lambda dt: self.enter(), 4)   # let it rejoin, then scan
            self._show_radio(True, None)
            self.status.text = tr("Wi-Fi is on. Rejoining your saved network…")
        else:
            self._show_radio(False, None)

    def _kw(self):
        return {"run": self._run} if self._run else {}

    def _show_status(self, cur):
        if cur:
            self.status.text = tr("Connected: {ssid}").format(ssid=cur["ssid"]) + (
                f"  ({cur['ip']})" if cur.get("ip") else "")
            self.status.color = theme.hex_to_rgba(theme.COLORS["green"])
        else:
            self.status.text = tr("Not connected — scan and pick a network.")
            self.status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])

    # -- scan ---------------------------------------------------------------

    def _scan(self):
        if self._busy or not self.radio.active:      # nothing to find with it off
            return
        self._busy = True
        self.scan_btn.text = tr("Searching…")
        self.list.clear_widgets()

        def work():
            nets = wifi.scan_networks(**self._kw())
            Clock.schedule_once(lambda dt: self._show_networks(nets), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show_networks(self, nets):
        self._busy = False
        self.scan_btn.text = tr("Search for Wi-Fi networks")
        self._rows = []
        if not nets:
            self.list.add_widget(_line(tr("No networks found."), color="amber"))
            return
        for n in nets:
            btn = Button(text=_row_label(n),
                         size_hint_y=None, height=dp(46), halign="left",
                         padding=(dp(8), 0),          # inset text via padding…
                         background_normal="", background_color=theme.hex_to_rgba(
                             theme.COLORS["surface"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            # …NOT by subtracting from text_size. text_size must equal the row
            # size (the safe idiom, same as _line): a fixed point the layout
            # settles on. The old ``(v[0] - dp(16), v[1])`` never matched the
            # row width, so size→text_size→size could never converge → storm.
            btn.bind(size=lambda i, v: setattr(i, "text_size", v))
            btn.bind(on_release=lambda *_a, net=n, row=btn: self._select(net, row=row))
            self._rows.append(btn)
            self.list.add_widget(btn)

    # -- select + connect ---------------------------------------------------

    def _highlight_row(self, row):
        """The tapped row is the one that changes. A status line further down
        saying "Enter password for X" is not a selection the eye can find in
        a list of eight look-alike rows (operator, 2026-09-21)."""
        for b in getattr(self, "_rows", []):
            b.background_color = theme.hex_to_rgba(theme.COLORS["surface"])
            b.color = theme.hex_to_rgba(theme.COLORS["text_primary"])
        if row is not None:
            row.background_color = theme.hex_to_rgba(theme.COLORS["accent"])
            row.color = theme.hex_to_rgba(theme.COLORS["background"])

    def _select(self, net, row=None):
        self._selected = net
        self._highlight_row(row)
        self.autoconnect.active = True                # default: rejoin automatically
        self.autoconn_row.height, self.autoconn_row.opacity = dp(40), 1
        if net["secure"]:
            self.pw_row.height, self.pw_row.opacity = dp(48), 1
            self.pw_in.text = ""
            self.pw_in.password = True                # start masked
            self.show_btn.text = tr("Show")
            self.status.text = tr("Enter password for {ssid}, then Connect.").format(
                ssid=net["ssid"])
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
        auto = self.autoconnect.active
        self.status.text = tr("Connecting to {ssid}…").format(ssid=ssid)
        self.status.color = theme.hex_to_rgba(theme.COLORS["accent"])

        def work():
            ok, msg = wifi.connect(ssid, pw, autoconnect=auto, **self._kw())
            Clock.schedule_once(lambda dt: self._show_result(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, ok, msg):
        """A VERDICT the operator can act on, then onward — not a status
        line under a keyboard (operator, 2026-09-22: "user should get a
        confirmation of connection or connection failed, then sent back to
        settings"). Connected: say so and return to Settings on dismiss.
        Failed: say why in plain words, keep the password field so the
        retry is one tap — sending them away would make them find the
        network again."""
        from ui.requirement_popup import requirement_popup
        self._busy = False
        self.status.text = msg
        self.status.color = theme.hex_to_rgba(theme.COLORS["green" if ok else "red"])
        ssid = (self._selected or {}).get("ssid", "")
        if ok:
            self.pw_row.height, self.pw_row.opacity = dp(0), 0
            self.autoconn_row.height, self.autoconn_row.opacity = dp(0), 0
            self._refresh_status()
            pop = requirement_popup(
                tr("Connected to {ssid}.").format(ssid=ssid) + "\n\n"
                + tr("Node Medic will use this network from now on."),
                tr("Wi-Fi connected"), False, tone="success")
            if self._on_done is not None:
                pop.bind(on_dismiss=lambda *_: self._on_done())
            return
        kind, tail = wifi.explain_failure(msg)
        words = {
            wifi.FAIL_PASSWORD: tr("The password was not accepted. Check it and "
                                   "try again."),
            wifi.FAIL_OUT_OF_RANGE: tr("That network is out of range right now."),
        }
        why = words.get(kind) or (tr("The network did not accept the connection.")
                                  + "\n" + tail)
        requirement_popup(tr("Could not connect to {ssid}.").format(ssid=ssid)
                          + "\n\n" + why, tr("Wi-Fi connection failed"), False,
                          tone="warning")
        if self._selected and self._selected.get("secure"):
            self.pw_in.focus = True
