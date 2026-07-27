"""Regression guard for the SCAN-map pan freeze.

A missing map tile must be looked up in the tile store ONCE, then served from a
miss cache — not re-queried on every redraw. Without this, panning over sparse
high-zoom area re-ran a SQLite query per visible tile per redraw, multiplied by
_draw_tile's overzoom pyramid walk (up to the zoom depth), pegging a core at 100%
and freezing the touchscreen.

Like the other SCAN tests, we stub Kivy at import time so this runs with or
without a real Kivy / display (the miss path touches no Kivy — CoreImage is only
reached on a hit). MapPlot.__new__ bypasses the Widget constructor, so no GL.
"""

import sys
import types


def _install_kivy_stubs():
    class _Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Dummy()

    def _module(name):
        m = types.ModuleType(name)
        m.__getattr__ = lambda attr: _Dummy
        return m

    for name in (
        "kivy", "kivy.clock", "kivy.core", "kivy.core.image", "kivy.core.window",
        "kivy.graphics", "kivy.metrics", "kivy.app", "kivy.uix",
        "kivy.uix.boxlayout", "kivy.uix.button", "kivy.uix.floatlayout",
        "kivy.uix.label", "kivy.uix.textinput", "kivy.uix.widget",
        "kivy.uix.popup",
    ):
        sys.modules.setdefault(name, _module(name))


_install_kivy_stubs()

from ui.screens.scan_screen import MapPlot  # noqa: E402


class _CountingTiles:
    """A tile store that records every get_tile call and reports every tile as
    absent — the exact 'sparse area' case that caused the storm."""

    def __init__(self):
        self.calls = 0

    def get_tile(self, z, x, y):
        self.calls += 1
        return None


def _bare_plot(tiles):
    plot = MapPlot.__new__(MapPlot)          # bypass Kivy Widget.__init__ (no GL)
    plot._tex_cache = {}
    plot._tile_misses = set()
    plot._tiles = tiles
    return plot


def test_missing_tile_is_queried_once_not_every_redraw():
    tiles = _CountingTiles()
    plot = _bare_plot(tiles)
    for _ in range(50):                       # 50 redraws touching the same tile
        assert plot._tile_texture(16, 10, 12) is None
    assert tiles.calls == 1                   # one store hit, then served from misses


def test_each_distinct_missing_tile_is_its_own_single_query():
    tiles = _CountingTiles()
    plot = _bare_plot(tiles)
    for _ in range(30):
        for x in (10, 11, 12):                # three distinct missing tiles
            plot._tile_texture(16, x, 12)
    assert tiles.calls == 3                    # not 3 * 30


def test_no_tile_store_is_handled_without_error():
    plot = _bare_plot(None)                    # _tiles can be None (no basemap)
    assert plot._tile_texture(5, 1, 1) is None
