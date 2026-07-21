"""Confirm the GPS position before it's stamped onto a node being installed.

The medic can HOLD a fix from an earlier, possibly distant spot — a GNSS receiver
coasts on its last lock when it loses sky view (sats=0 but has_fix=True). Committing
that blindly could pin a node kilometres from where it actually is, poisoning the
placement map. So at the moment of committing a location we:
  * show the position on the offline map (a 'you are here' pin),
  * badge it clearly LIVE (green, tracking now) vs HELD (amber, may be stale) vs
    NONE (red), and
  * let the operator Confirm it, Recalibrate (take it outside, wait for a live fix),
    or Enter coordinates by hand.

The freshness verdict is monitor.geo.fix_trust (pure + unit-tested); this screen is
the Kivy presentation over it.
"""

from __future__ import annotations

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

from ui import theme
from ui.map_tiles import find_mbtiles
from ui.screens.scan_screen import MapPlot
from monitor.geo import read_splitter_fix, fix_trust

_LEVEL_COLOR = {"live": "green", "held": "amber", "none": "red"}


def _line(text, bold=False, size="15sp", color="text_primary"):
    lbl = Label(text=text, bold=bold, font_size=size, halign="left", valign="middle",
                size_hint_y=None, height=dp(26),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


def _btn(text, color, on_tap):
    b = Button(text=text, bold=True, font_size="15sp", background_normal="",
               background_color=theme.hex_to_rgba(theme.COLORS[color]),
               color=theme.hex_to_rgba(theme.COLORS[
                   "background" if color != "surface" else "text_primary"]))
    b.bind(on_release=lambda *_: on_tap())
    return b


class GpsConfirmScreen(BoxLayout):
    """Confirm the position that will be stamped onto a node.

    ``on_confirm(lat, lon, source)`` fires when the operator commits a position
    (from the live/held fix, or manually typed). ``fix_reader`` is injectable for
    tests; by default it reads the Tracker's fix via the splitter."""

    def __init__(self, on_confirm=None, on_cancel=None, fix_reader=None,
                 tiles=None, poll=True, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(6)
        self.padding = dp(10)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._fix_reader = fix_reader or read_splitter_fix
        self._tiles = tiles if tiles is not None else find_mbtiles()
        self._fix = None
        self._manual = False

        self.add_widget(_line("Confirm this node's location", bold=True, size="20sp"))
        self.badge = _line("", bold=True, size="16sp")
        self.add_widget(self.badge)
        self.detail = Label(text="", font_size="12.5sp", halign="left", valign="top",
                            size_hint_y=None, height=dp(48),
                            color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        self.detail.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.detail)
        self.coords = _line("", size="16sp")
        self.add_widget(self.coords)

        self.map = MapPlot(tiles=self._tiles, size_hint_y=1)
        self.add_widget(self.map)

        self.manual_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                    height=dp(0), spacing=dp(8), opacity=0)
        self.lat_in = TextInput(hint_text="latitude", multiline=False,
                                input_filter="float", font_size="16sp")
        self.lon_in = TextInput(hint_text="longitude", multiline=False,
                                input_filter="float", font_size="16sp")
        self.manual_row.add_widget(self.lat_in)
        self.manual_row.add_widget(self.lon_in)
        self.add_widget(self.manual_row)

        btns = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(58),
                         spacing=dp(8))
        self.confirm_btn = _btn("Use this position", "green", self._confirm)
        btns.add_widget(self.confirm_btn)
        btns.add_widget(_btn("Recalibrate", "accent", self._recalibrate))
        btns.add_widget(_btn("Enter manually", "surface", self._toggle_manual))
        self.add_widget(btns)

        self._refresh()
        self._ev = Clock.schedule_interval(lambda dt: self._refresh(), 3) if poll else None

    def _set_badge(self, text, color):
        self.badge.text = text
        self.badge.color = theme.hex_to_rgba(theme.COLORS[color])

    def _refresh(self):
        if self._manual:
            return
        self._fix = self._fix_reader()
        t = fix_trust(self._fix)
        self._set_badge(t["title"], _LEVEL_COLOR.get(t["level"], "text_secondary"))
        self.detail.text = t["detail"]
        if self._fix and self._fix.has_fix:
            self.coords.text = f"{self._fix.lat:.6f},  {self._fix.lon:.6f}"
            self.map.set_me((self._fix.lat, self._fix.lon))
            self.confirm_btn.disabled = False
        else:
            self.coords.text = "—"
            self.confirm_btn.disabled = True

    def _confirm(self, *_):
        if self._manual:
            try:
                lat, lon, src = float(self.lat_in.text), float(self.lon_in.text), "manual"
            except ValueError:
                self._set_badge("Enter valid numbers for latitude and longitude", "red")
                return
        elif self._fix and self._fix.has_fix:
            lat, lon, src = self._fix.lat, self._fix.lon, self._fix.source
        else:
            return
        if self._on_confirm:
            self._on_confirm(lat, lon, src)

    def _recalibrate(self, *_):
        self._manual = False
        self.manual_row.height, self.manual_row.opacity = dp(0), 0
        self._set_badge("Recalibrating — take the medic outside for clear sky...", "amber")
        self.detail.text = ("Waiting for a live fix (satellites tracking). The badge "
                            "turns green when it locks; then Use this position.")
        self._refresh()

    def _toggle_manual(self, *_):
        self._manual = not self._manual
        if self._manual:
            self.manual_row.height, self.manual_row.opacity = dp(48), 1
            self._set_badge("Enter coordinates manually", "accent")
            self.detail.text = "Type the install location's lat/lon, then Use this position."
            self.confirm_btn.disabled = False
            if self._fix and self._fix.has_fix:
                self.lat_in.text = f"{self._fix.lat:.6f}"
                self.lon_in.text = f"{self._fix.lon:.6f}"
        else:
            self.manual_row.height, self.manual_row.opacity = dp(0), 0
            self._refresh()
