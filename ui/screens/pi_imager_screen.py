"""On-medic Pi SD imaging — the guided screen (guided birth ▸ Pi path).

Detects a USB card reader, collects the few details a headless Pi needs (hostname,
WiFi, a login password — WiFi pre-filled from the medic's own network so the Pi
lands on the same LAN), then writes Pi OS + a firstboot config to the card. The
destructive write is guarded (never the medic's own disk) and gated behind a
typed-confirmation popup that names the exact card.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from ui import theme
from ui.onscreen_keyboard import bind_field
from ui.widgets.progress_ring import ProgressRing
from ui.widgets.birth_anims import InsertSdAnim
from provisioning import pi_imager

_EST_WRITE_S = 240.0                       # rough dd+config time for the fill estimate


def _line(text, size="15sp", color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=size, bold=bold, halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class PiImagerScreen(BoxLayout):
    """``wifi_credentials()`` (optional) pre-fills WiFi from the medic's own network."""

    def __init__(self, wifi_credentials=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(8)
        self._wifi_credentials = wifi_credentials
        self._target = None
        self._busy = False
        self.add_widget(_line("Image a Raspberry Pi SD card", bold=True,
                              size="22sp", h=40))
        body = ScrollView()
        self.col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        self.col.bind(minimum_height=self.col.setter("height"))
        body.add_widget(self.col)
        self.add_widget(body)
        self._build()

    def _field(self, label, hint, key, password=False, numeric=False):
        self.col.add_widget(_line(label, size="15sp", color="accent", bold=True, h=24))
        ti = TextInput(hint_text=hint, multiline=False, password=password,
                       size_hint_y=None, height=dp(48), font_size="17sp")
        bind_field(ti, numeric=numeric)
        self._inputs[key] = ti
        self.col.add_widget(ti)
        return ti

    def _build(self):
        self.col.clear_widgets()
        self._inputs = {}
        targets = pi_imager.list_target_disks()
        if not targets:
            # No card reader — show the insert animation + a rescan.
            self.col.add_widget(_line(
                "Put the Pi's microSD into a USB card reader and plug it into Node "
                "Medic (it has no built-in card slot).", size="15sp", h=48))
            anim = InsertSdAnim(size_hint_y=None, height=dp(180))
            self.col.add_widget(anim)
            anim.start()
            rescan = Button(text="I've plugged it in — look again", size_hint_y=None,
                            height=dp(52), bold=True, background_normal="",
                            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
            rescan.bind(on_release=lambda *_: self._build())
            self.col.add_widget(rescan)
            return
        self._target = targets[0]
        self.col.add_widget(_line(
            f"Card detected: {self._target['model'] or 'USB card'} "
            f"({self._target['size']}) at {self._target['path']}",
            size="14sp", color="green", h=24))
        self.col.add_widget(_line(
            "Everything on this card will be erased.", size="13sp",
            color="warning_yellow", h=22))

        self._field("Node hostname", "e.g. propagation-01", "hostname")
        ssid, psk = ("", "")
        if self._wifi_credentials:
            try:
                ssid, psk = self._wifi_credentials()
            except Exception:
                ssid, psk = ("", "")
        self._field("WiFi network", "SSID the Pi should join", "ssid").text = ssid
        self._field("WiFi password", "WiFi password", "psk", password=True).text = psk
        self._field("Set a login password", "for user 'pi' (SSH login)", "pw",
                    password=True)

        write = Button(text="Write SD card", size_hint_y=None, height=dp(56), bold=True,
                       font_size="18sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        write.bind(on_release=lambda *_: self._confirm())
        self.col.add_widget(write)
        self._status = _line("", size="13.5sp", color="text_secondary", h=26)
        self.col.add_widget(self._status)

    def _vals(self):
        return {k: v.text.strip() for k, v in self._inputs.items()}

    def _confirm(self):
        if self._busy or not self._target:
            return
        v = self._vals()
        if not v.get("hostname") or not v.get("pw"):
            self._status.text = "Enter at least a hostname and a login password."
            self._status.color = theme.hex_to_rgba(theme.COLORS["red"])
            return
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=(
            f"Write Pi OS to [b]{self._target['model'] or 'the USB card'} "
            f"({self._target['size']})[/b] at [b]{self._target['path']}[/b]?\n\n"
            "[color=ff5555]This ERASES everything on that card.[/color] It cannot be "
            "the medic's own storage — only a removable USB card is allowed."))
        msg.bind(size=lambda i, val: setattr(i, "text_size", val))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title="Confirm — this erases the card", content=box,
                      size_hint=(0.9, 0.6))
        cancel = Button(text="Cancel", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        go = Button(text="Erase & write", bold=True, background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        go.bind(on_release=lambda *_: (popup.dismiss(), self._write(v)))
        row.add_widget(cancel)
        row.add_widget(go)
        box.add_widget(row)
        popup.open()

    def _write(self, v):
        self._busy = True
        self.col.clear_widgets()
        self.col.add_widget(_line(f"Imaging {self._target['path']} as "
                                  f"'{v['hostname']}'…", bold=True, size="16sp", h=30))
        ring_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(80),
                             spacing=dp(12))
        self._ring = ProgressRing()
        ring_row.add_widget(self._ring)
        ring_row.add_widget(_line("Writing Pi OS and applying your settings. This "
                                  "takes a few minutes — don't remove the card.",
                                  size="13sp", color="accent"))
        self.col.add_widget(ring_row)
        import time
        self._t0 = time.monotonic()
        self._ev = Clock.schedule_interval(self._tick, 0.3)

        dev, path = self._target, self._target["path"]

        def work():
            ok, msg = pi_imager.flash(
                path, v["hostname"], "pi", v["pw"],
                wifi_ssid=v.get("ssid", ""), wifi_password=v.get("psk", ""))
            Clock.schedule_once(lambda dt: self._done(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _tick(self, _dt):
        import time
        frac = min(0.95, (time.monotonic() - self._t0) / _EST_WRITE_S)
        self._ring.set_fraction(frac)

    def _done(self, ok, msg):
        self._busy = False
        ev = getattr(self, "_ev", None)
        if ev is not None:
            ev.cancel()
        self._ring.set_fraction(1.0 if ok else self._ring.fraction)
        self.col.add_widget(_line("✓  Done!" if ok else "✗  Couldn't finish",
                                  bold=True, size="19sp",
                                  color="green" if ok else "red", h=30))
        self.col.add_widget(_line(msg, size="14sp", h=60))
        again = Button(text="Image another card", size_hint_y=None, height=dp(50),
                       background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        again.bind(on_release=lambda *_: self._build())
        self.col.add_widget(again)
