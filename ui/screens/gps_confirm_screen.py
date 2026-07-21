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
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ui import theme
from ui.map_tiles import find_mbtiles
from ui.screens.scan_screen import MapPlot
from monitor.geo import read_splitter_fix, fix_trust

#: Bubble fill per fix level; a warning triangle is drawn for held/none.
_LEVEL_FILL = {"live": "green", "held": "warning_yellow", "none": "red",
               "info": "accent"}


class _Badge(BoxLayout):
    """A rounded, FILLED status bubble (no outline): green (live) / yellow (held) /
    red (none). Draws a warning triangle for held/none — the ⚠ emoji renders as
    tofu in the default font, so we draw it."""

    def __init__(self, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(46),
                         padding=[dp(14), dp(4)], spacing=dp(6), **kwargs)
        with self.canvas.before:
            self._fill = Color(0, 0, 0, 0)
            self._rect = RoundedRectangle(radius=[dp(16)] * 4)
        self.bind(pos=self._sync, size=self._sync)
        self._tri = Widget(size_hint=(None, 1), width=dp(0))
        self._tri.bind(pos=self._draw_tri, size=self._draw_tri)
        self.add_widget(self._tri)
        self.label = Label(font_size="16sp", bold=True, halign="left",
                           valign="middle")
        self.label.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.label)
        self._tri_color = None

    def _sync(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size

    def _draw_tri(self, *_):
        self._tri.canvas.after.clear()
        if self._tri_color is None or self._tri.width < dp(6):
            return
        w = self._tri
        cx, cy, half, h = w.center_x, w.center_y, dp(10), dp(9)
        with w.canvas.after:
            Color(*self._tri_color)
            Line(points=[cx - half, cy - h, cx + half, cy - h, cx, cy + h],
                 width=dp(1.8), close=True, joint="round", cap="round")
            Line(points=[cx, cy - h + dp(4), cx, cy + dp(1)], width=dp(1.6), cap="round")
            Line(points=[cx, cy + dp(3), cx, cy + dp(4)], width=dp(1.8), cap="round")

    def set(self, text, level):
        self._fill.rgba = theme.hex_to_rgba(
            theme.COLORS[_LEVEL_FILL.get(level, "surface")])
        dark = level in ("live", "held", "info")           # dark text on light fills
        self.label.color = theme.hex_to_rgba(
            theme.COLORS["background" if dark else "text_primary"])
        self.label.text = text
        if level in ("held", "none"):
            self._tri.width = dp(26)
            self._tri_color = theme.hex_to_rgba(theme.COLORS[
                "background" if level == "held" else "text_primary"])
        else:
            self._tri.width, self._tri_color = dp(0), None
        self._draw_tri()


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
        self.badge = _Badge()
        self.add_widget(self.badge)
        self.detail = Label(text="", font_size="12.5sp", halign="left", valign="top",
                            size_hint_y=None, height=dp(48),
                            color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        self.detail.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.detail)
        self.coords = _line("", size="16sp")
        self.add_widget(self.coords)

        self.map = MapPlot(tiles=self._tiles, interactive=False, size_hint_y=1)
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

    def _set_badge(self, text, level):
        self.badge.set(text, level)

    def _refresh(self):
        if self._manual:
            return
        self._fix = self._fix_reader()
        t = fix_trust(self._fix)
        self._set_badge(t["title"], t["level"])
        self.detail.text = t["detail"]
        if self._fix and self._fix.has_fix:
            self.coords.text = f"{self._fix.lat:.6f},  {self._fix.lon:.6f}"
            self.map.focus((self._fix.lat, self._fix.lon))   # street-level, pin centred
            self.confirm_btn.disabled = False
        else:
            self.coords.text = "—"
            self.confirm_btn.disabled = True

    def _confirm(self, *_):
        if self._manual:
            try:
                lat, lon, src = float(self.lat_in.text), float(self.lon_in.text), "manual"
            except ValueError:
                self._set_badge("Enter valid numbers for latitude and longitude", "none")
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
        self._set_badge("Recalibrating — take the medic outside for clear sky…", "info")
        self.detail.text = ("Waiting for a live fix (satellites tracking). The badge "
                            "turns green when it locks; then Use this position.")
        self._refresh()

    def _toggle_manual(self, *_):
        self._manual = not self._manual
        if self._manual:
            self.manual_row.height, self.manual_row.opacity = dp(48), 1
            self._set_badge("Enter coordinates manually", "info")
            self.detail.text = "Type the install location's lat/lon, then Use this position."
            self.confirm_btn.disabled = False
            if self._fix and self._fix.has_fix:
                self.lat_in.text = f"{self._fix.lat:.6f}"
                self.lon_in.text = f"{self._fix.lon:.6f}"
        else:
            self.manual_row.height, self.manual_row.opacity = dp(0), 0
            self._refresh()
