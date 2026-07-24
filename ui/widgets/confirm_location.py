"""Confirm-a-node's-location gate — a map modal shown BEFORE a location is written
to a birth/adopt certificate, so a wrong or stale GPS fix (or a mistyped address)
can't silently pin a node in the wrong place (a repair crew's wasted drive).

Shows the proposed pin on a street-level map; the operator TAPS the map to move
the pin, or pulls the medic's current GPS, checks the coordinates + reverse-
geocoded address, then Confirms. Reuses the SCAN map widget (ui.screens.scan_screen
.MapPlot) and the carried MBTiles basemap so it works offline (address needs net).
"""

from __future__ import annotations

from typing import Callable, Optional

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

from ui import theme


def _lbl(text, size="14sp", color="text_primary", bold=False, h=None):
    l = Label(text=text, font_size=size, bold=bold, halign="center",
              valign="middle", color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        l.size_hint_y = None
        l.height = dp(h)
    l.bind(size=lambda i, v: setattr(i, "text_size", v))
    return l


class ConfirmLocationPopup(Popup):
    """``on_confirm(lat, lon)`` fires when the operator accepts the (possibly
    moved) pin; ``on_cancel()`` if they back out."""

    def __init__(self, lat: float, lon: float, node_name: str = "",
                 on_confirm: Optional[Callable[[float, float], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None,
                 gps_reader: Optional[Callable[[], Optional[tuple]]] = None,
                 tiles=None, **kwargs):
        self._lat, self._lon = float(lat), float(lon)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._gps_reader = gps_reader
        self._decided = False

        who = f" — {node_name}" if node_name else ""
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))

        # the map (reused SCAN widget) with a draggable-by-tap pin
        if tiles is None:
            try:
                from ui.map_tiles import MAPS_DIR, find_mbtiles
                tiles = find_mbtiles(MAPS_DIR)
            except Exception:
                tiles = None
        from ui.screens.scan_screen import MapPlot
        self.plot = MapPlot(nodes=[], tiles=tiles, interactive=True,
                            on_pick=self._move_pin)
        body.add_widget(self.plot)

        body.add_widget(_lbl("Tap the map to move the pin", "13sp",
                             color="accent", h=20))
        self._coords = _lbl(self._coord_text(), "14sp", bold=True, h=22)
        body.add_widget(self._coords)
        self._addr = _lbl("Looking up address…", "12.5sp",
                          color="text_secondary", h=34)
        body.add_widget(self._addr)

        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(58),
                        spacing=dp(8))
        cancel = Button(text="Cancel", bold=True, font_size="15sp", size_hint_x=0.28,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *_: self._cancel())
        row.add_widget(cancel)
        if gps_reader is not None:
            usegps = Button(text="Use current GPS", bold=True, font_size="14sp",
                            size_hint_x=0.36, background_normal="",
                            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
            usegps.bind(on_release=lambda *_: self._use_gps())
            row.add_widget(usegps)
        confirm = Button(text="Confirm location", bold=True, font_size="16sp",
                         background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
        confirm.bind(on_release=lambda *_: self._confirm())
        row.add_widget(confirm)
        body.add_widget(row)

        super().__init__(title=f"Confirm this node's location{who}",
                         content=body, size_hint=(0.96, 0.92),
                         auto_dismiss=False, **kwargs)
        Clock.schedule_once(lambda *_: self.plot.focus((self._lat, self._lon)), 0)
        self._refresh_address()

    # -- pin movement ------------------------------------------------------
    def _coord_text(self):
        return f"{self._lat:.6f}, {self._lon:.6f}"

    def _move_pin(self, lat, lon):
        self._lat, self._lon = float(lat), float(lon)
        self._coords.text = self._coord_text()
        self.plot._me = (self._lat, self._lon)     # move the pin, keep the view
        self.plot._trigger()
        self._refresh_address()

    def _use_gps(self):
        if self._gps_reader is None:
            return
        try:
            fix = self._gps_reader()
        except Exception:
            fix = None
        if fix:
            self.plot.focus((fix[0], fix[1]))       # recentre on the live fix
            self._move_pin(fix[0], fix[1])
        else:
            self._addr.text = "No GPS fix right now — tap the map instead."

    def _refresh_address(self):
        import threading
        lat, lon = self._lat, self._lon
        self._addr.text = "Looking up address…"

        def work():
            addr = None
            try:
                from monitor.geo import reverse_geocode
                addr = reverse_geocode(lat, lon)
            except Exception:
                addr = None
            if (lat, lon) != (self._lat, self._lon):
                return                              # pin moved again; stale result
            Clock.schedule_once(lambda *_: setattr(
                self._addr, "text", addr or "(no address — offline; judge by the map)"), 0)
        threading.Thread(target=work, daemon=True).start()

    # -- decision ----------------------------------------------------------
    def _confirm(self):
        if self._decided:
            return
        self._decided = True
        self.dismiss()
        if self._on_confirm:
            self._on_confirm(self._lat, self._lon)

    def _cancel(self):
        if self._decided:
            return
        self._decided = True
        self.dismiss()
        if self._on_cancel:
            self._on_cancel()
