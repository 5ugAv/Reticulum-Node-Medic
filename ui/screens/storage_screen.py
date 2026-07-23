"""Settings ▸ Storage usage (item 6, read-only).

How full the SD card is, a breakdown of what's using space (map tiles, beacon
history, firmware assets, registry & fleet, logs), and how much is free. Sizes are
walked off-thread (the map cache can be big) and posted back. No cleanup actions
here — read-only.
"""

from __future__ import annotations

import os
import threading

from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme
from provisioning import storage
from ui.map_tiles import MAPS_DIR
from workflows.updater import RNODE_UPDATE_DIR

_FIRMWARE_ASSETS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
    "assets", "firmware"))
_MEDIC_DIR = "~/.reticulum-node-medic"
_KIVY_LOGS = "~/.kivy/logs"


def _line(text, size="15sp", color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=size, bold=bold, halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class _Bar(Widget):
    """A horizontal fill bar (0..1) — track + coloured fill."""

    def __init__(self, frac=0.0, color="accent", **kwargs):
        super().__init__(size_hint_y=None, height=dp(14), **kwargs)
        self._frac = max(0.0, min(1.0, frac))
        self._colname = color
        with self.canvas:
            self._tc = Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            self._track = RoundedRectangle(radius=[dp(7)] * 4)
            self._fc = Color(*theme.hex_to_rgba(theme.COLORS[color]))
            self._fill = RoundedRectangle(radius=[dp(7)] * 4)
        self.bind(pos=self._sync, size=self._sync)

    def set_frac(self, frac):
        self._frac = max(0.0, min(1.0, frac))
        self._sync()

    def _sync(self, *_):
        self._track.pos, self._track.size = self.pos, self.size
        self._fill.pos = self.pos
        self._fill.size = (max(dp(14), self.width * self._frac), self.height)


class StorageScreen(BoxLayout):
    """``history_bytes`` (optional callable) reports the live in-memory beacon
    history size, since that isn't on disk yet."""

    def __init__(self, history_bytes=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(8)
        self._history_bytes = history_bytes
        self.add_widget(_line("Storage usage", bold=True, size="22sp", h=40))

        self._summary = _line("Reading…", size="15sp", color="text_secondary", h=24)
        self.add_widget(self._summary)
        self._disk_bar = _Bar(0.0, color="accent")
        self.add_widget(self._disk_bar)
        self._free = _line("", size="14sp", color="green", h=24)
        self.add_widget(self._free)

        self.add_widget(_line("What's using space", bold=True, size="15sp",
                              color="accent", h=28))
        self._rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self._rows.bind(minimum_height=self._rows.setter("height"))
        self.add_widget(self._rows)
        self.add_widget(Widget())                    # push up
        self._load()

    def _load(self):
        def work():
            disk = storage.disk_usage("/")
            hist = 0
            try:
                hist = int(self._history_bytes()) if self._history_bytes else 0
            except Exception:
                hist = 0
            cats = [
                ("Map tiles", storage.path_size(MAPS_DIR)),
                ("Beacon history", hist),
                ("Firmware assets",
                 storage.paths_size([_FIRMWARE_ASSETS, RNODE_UPDATE_DIR])),
                ("Registry & fleet", storage.path_size(_MEDIC_DIR)),
                ("Logs", storage.path_size(_KIVY_LOGS)),
            ]
            Clock.schedule_once(lambda dt: self._show(disk, cats), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show(self, disk, cats):
        fs = storage.format_size
        self._summary.text = (f"{fs(disk['used'])} of {fs(disk['total'])} used "
                              f"({disk['percent']}%)")
        self._disk_bar.set_frac(disk["percent"] / 100.0)
        self._free.text = f"{fs(disk['free'])} free"
        self._rows.clear_widgets()
        biggest = max((b for _, b in cats), default=1) or 1
        for label, nbytes in sorted(cats, key=lambda c: -c[1]):
            row = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(46),
                            spacing=dp(3))
            head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(22))
            head.add_widget(_line(label, size="14.5sp"))
            amt = _line(fs(nbytes), size="14.5sp", color="text_secondary")
            amt.halign = "right"
            head.add_widget(amt)
            row.add_widget(head)
            row.add_widget(_Bar(nbytes / biggest, color="accent"))
            self._rows.add_widget(row)
