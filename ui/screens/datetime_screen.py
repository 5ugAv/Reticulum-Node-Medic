"""Settings ▸ Date, time & timezone (item 8).

Set the medic's clock and timezone by hand, or let it keep itself right from GPS
(the Tracker's GNSS carries satellite UTC time — the only reliable source when
the unit is offline afield). With auto-sync ON the manual fields go read-only and
the screen shows when it last synced from GPS.

All clock/timezone/GPS reads and writes are quick subprocesses, so they run on a
thread and post results back via ``Clock``.
"""

from __future__ import annotations

import threading
import time

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput

from ui import theme
from ui.onscreen_keyboard import bind_field
from provisioning import tool_datetime as td


def _line(text, size="15sp", color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=size, bold=bold, halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class DateTimeScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(14)
        self.spacing = dp(8)
        self.add_widget(_line("Date & time", bold=True, size="22sp", h=40))

        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        col.bind(minimum_height=col.setter("height"))

        # -- auto-sync toggle -------------------------------------------------
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(46),
                        spacing=dp(10))
        row.add_widget(_line("Keep the clock synced from GPS", size="15sp"))
        self._auto = Switch(active=td.is_autosync(), size_hint_x=None, width=dp(90))
        self._auto.bind(active=lambda _i, v: self._on_autosync(bool(v)))
        row.add_widget(self._auto)
        col.add_widget(row)

        self._sync_status = _line("", size="13sp", color="text_secondary", h=24)
        col.add_widget(self._sync_status)

        # -- manual fields ----------------------------------------------------
        col.add_widget(_line("Set manually", bold=True, size="15sp",
                             color="accent", h=26))

        col.add_widget(_line("Date & time  (YYYY-MM-DD HH:MM:SS)",
                             size="13sp", color="text_secondary", h=22))
        self._dt = TextInput(text=td.now_string(), multiline=False,
                             size_hint_y=None, height=dp(46), font_size="18sp")
        bind_field(self._dt)
        col.add_widget(self._dt)

        col.add_widget(_line("Timezone  (e.g. Australia/Melbourne)",
                             size="13sp", color="text_secondary", h=22))
        self._tz = TextInput(text="reading…", multiline=False,
                             size_hint_y=None, height=dp(46), font_size="18sp")
        bind_field(self._tz)
        col.add_widget(self._tz)

        self._save = Button(text="Save date, time & timezone", size_hint_y=None,
                            height=dp(54), bold=True, font_size="17sp",
                            background_normal="",
                            background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self._save.bind(on_release=lambda *_: self._do_save())
        col.add_widget(self._save)

        self._sync_now = Button(text="Sync now from GPS", size_hint_y=None,
                                height=dp(50), font_size="16sp", background_normal="",
                                background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self._sync_now.bind(on_release=lambda *_: self._do_sync_now())
        col.add_widget(self._sync_now)

        self._status = _line("", size="13sp", color="green", h=24)
        col.add_widget(self._status)

        body.add_widget(col)
        self.add_widget(body)

        self._apply_mode(td.is_autosync())
        self._load()

    # -- background reads --------------------------------------------------

    def _load(self):
        """Read the current timezone + last-sync state off-thread."""
        def work():
            tz = td.current_timezone()
            ls = td.last_sync()
            ago = td.format_synced_ago(ls, time.time())
            Clock.schedule_once(lambda dt: self._show_loaded(tz, ago), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show_loaded(self, tz, ago):
        if tz:
            self._tz.text = tz
        elif self._tz.text == "reading…":
            self._tz.text = ""
        self._refresh_sync_status(ago)

    def _refresh_sync_status(self, ago=None):
        if ago is None:
            ago = td.format_synced_ago(td.last_sync(), time.time())
        if self._auto.active:
            self._sync_status.text = f"Auto-sync ON — {ago}. Manual entry is disabled."
        else:
            self._sync_status.text = (f"Auto-sync OFF — set the clock by hand below "
                                      f"({ago}).")

    # -- mode: auto vs manual ---------------------------------------------

    def _apply_mode(self, auto):
        """When auto-sync is ON, the manual fields are read-only."""
        for w in (self._dt, self._tz):
            w.readonly = auto
            w.disabled = auto
        self._save.disabled = auto
        self._refresh_sync_status()

    def _on_autosync(self, on):
        td.set_autosync(on)
        self._apply_mode(on)
        self._status.text = ("Auto-sync enabled — the medic will keep its clock set "
                             "from GPS." if on else "Auto-sync disabled.")
        if on:
            self._do_sync_now()          # correct the clock immediately

    # -- actions -----------------------------------------------------------

    def _do_save(self):
        dt_val = self._dt.text.strip()
        tz_val = self._tz.text.strip()
        self._status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
        self._status.text = "Applying…"

        def work():
            msgs = []
            ok = True
            if tz_val:
                tz_ok, tz_msg = td.set_timezone(tz_val)
                ok = ok and tz_ok
                msgs.append(tz_msg)
            if dt_val:
                dt_ok, dt_msg = td.set_datetime(dt_val)
                ok = ok and dt_ok
                msgs.append(dt_msg)
            Clock.schedule_once(
                lambda dt: self._report(ok, "  ".join(msgs) or "Nothing to set."), 0)
        threading.Thread(target=work, daemon=True).start()

    def _do_sync_now(self):
        self._status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
        self._status.text = "Reading GPS…"
        self._sync_now.disabled = True

        def work():
            ok, msg = td.sync_from_gps()
            Clock.schedule_once(lambda dt: self._after_sync(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _after_sync(self, ok, msg):
        self._sync_now.disabled = False
        self._report(ok, msg)
        if ok:
            self._dt.text = td.now_string()      # reflect the just-set clock
        self._refresh_sync_status()

    def _report(self, ok, msg):
        self._status.color = theme.hex_to_rgba(
            theme.COLORS["green" if ok else "warning_yellow"])
        self._status.text = msg
