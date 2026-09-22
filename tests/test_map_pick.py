"""Choosing a node's location on the birth map (operator, 2026-09-22):

  "when they type in an address there is a preview of the map there, but
   there is no option to zoom in to the map. You can move it around a little
   bit, but the user should be able to zoom in and out of the map, and also
   select a location by pressing an icon on the map and saying 'select here'."

What was there: ConfirmLocationPopup embeds a full interactive MapPlot —
drag-to-pan, tap-to-place, and PINCH-to-zoom already wired — but no +/−
buttons (SCAN has them, the popup did not), no centre marker, no way to adopt
the view centre, and no word about whether the level shown has any tiles.

Pure maths lives in ui/map_pick.py (Kivy-free) and is tested directly; the
widget wiring is pinned by reading source, the repo's pattern (see
test_walk_live_check.py / srcutil.py — Kivy widgets cannot be built headless
in CI).
"""

from ui.map_tiles import MercatorView, project_px, view_at
from ui.map_pick import (
    can_zoom, centre_of, format_pin, next_zoom, render_zoom, tile_caption,
    tile_report,
)
from tests.srcutil import func_source, src


# -- zoom stepping is clamped to what the cache has (+ the overzoom allowance) --

def test_plus_steps_to_the_next_cached_level_skipping_gaps():
    assert next_zoom([4, 8, 12], 4, +1, overzoom_max=16, fallback_max=15) == 8
    assert next_zoom([4, 8, 12], 8, -1, overzoom_max=16, fallback_max=15) == 4


def test_plus_past_the_cached_edge_overzooms_one_level_at_a_time_to_the_cap():
    # the drawer scales the nearest cached ancestor ("blurry beats black"),
    # but only as far as OVERZOOM_MAX — a pin cannot be placed on a suburb
    # from a state view, and it cannot be placed from a smear either.
    assert next_zoom([4, 8, 12], 12, +1, overzoom_max=16, fallback_max=15) == 13
    assert next_zoom([4, 8, 12], 15, +1, overzoom_max=16, fallback_max=15) == 16
    assert next_zoom([4, 8, 12], 16, +1, overzoom_max=16, fallback_max=15) == 16


def test_minus_from_an_overzoomed_level_lands_back_on_a_cached_one():
    assert next_zoom([4, 8, 12], 14, -1, overzoom_max=16, fallback_max=15) == 12


def test_minus_stops_at_the_shallowest_cached_level():
    assert next_zoom([4, 8, 12], 4, -1, overzoom_max=16, fallback_max=15) == 4


def test_no_cache_at_all_uses_the_fallback_range():
    # the interactive range is 2..fallback_max, then the overzoom allowance
    assert next_zoom([], 15, +1, overzoom_max=16, fallback_max=15) == 16
    assert next_zoom([], 3, -1, overzoom_max=16, fallback_max=15) == 2
    assert next_zoom([], 2, -1, overzoom_max=16, fallback_max=15) == 2


def test_can_zoom_is_false_exactly_where_a_press_would_do_nothing():
    zs = [4, 8, 12]
    assert can_zoom(zs, 4, -1, overzoom_max=16, fallback_max=15) is False
    assert can_zoom(zs, 4, +1, overzoom_max=16, fallback_max=15) is True
    assert can_zoom(zs, 16, +1, overzoom_max=16, fallback_max=15) is False
    assert can_zoom(zs, 16, -1, overzoom_max=16, fallback_max=15) is True


def test_render_zoom_snaps_below_the_edge_and_caps_above_it():
    # the level the pane actually draws for a requested one — never a level
    # with no tiles below the cached edge, never past the overzoom cap above it
    assert render_zoom([4, 8, 12], 10, overzoom_max=16) == 8
    assert render_zoom([4, 8, 12], 12, overzoom_max=16) == 12
    assert render_zoom([4, 8, 12], 14, overzoom_max=16) == 14
    assert render_zoom([4, 8, 12], 99, overzoom_max=16) == 16
    assert render_zoom([], 14, overzoom_max=16) == 14


# -- the view's centre is what 'Select here' adopts ---------------------------

def test_centre_of_a_view_built_on_a_point_is_that_point():
    lat, lon = -37.8136, 144.9631
    view = view_at(lat, lon, 14, 600.0, 400.0)
    clat, clon = centre_of(view)
    assert abs(clat - lat) < 1e-9 and abs(clon - lon) < 1e-9


def test_panning_the_view_moves_the_centre_with_it():
    lat, lon = -37.8136, 144.9631
    view = view_at(lat, lon, 14, 600.0, 400.0)
    # drag the map so the world scrolls 300 px east, 100 px north (y-down)
    moved = MercatorView(zoom=view.zoom, off_x=view.off_x + 300.0,
                         off_y=view.off_y - 100.0, width=view.width,
                         height=view.height)
    clat, clon = centre_of(moved)
    assert clon > lon, "east scroll = the centre now sits further east"
    assert clat > lat, "north scroll = the centre now sits further north"
    # and it is EXACTLY the geo point under the pane's middle pixel
    px, py = project_px(clat, clon, 14)
    assert abs(px - (moved.off_x + 300.0)) < 1e-6
    assert abs(py - (moved.off_y + 200.0)) < 1e-6


def test_select_here_shows_the_same_numbers_it_saves():
    """The coordinate line and the saved pin come from ONE pair of floats
    formatted ONE way — what the screen prints is what the certificate gets."""
    view = view_at(-37.8136, 144.9631, 15, 700.0, 380.0)
    lat, lon = centre_of(view)
    assert format_pin(lat, lon) == f"{lat:.6f}, {lon:.6f}"
    # six decimals = ~0.1 m; the screen never shows a rounder number than it saves
    assert format_pin(-37.8136, 144.9631) == "-37.813600, 144.963100"


# -- the caption says what the cache actually has at this zoom -------------

class _P:
    def __init__(self, z, x, y):
        self.z, self.x, self.y = z, x, y


def test_tile_report_counts_only_the_tiles_the_drawer_found():
    placements = [_P(14, 1, 1), _P(14, 1, 2), _P(14, 2, 1), _P(14, 2, 2)]
    textures = {(14, 1, 1): "tex", (14, 2, 2): "tex", (13, 0, 0): "ancestor"}
    misses = {(14, 1, 2), (14, 2, 1)}
    assert tile_report(placements, textures, misses) == (2, 4)


def test_caption_with_no_carried_map_says_so_and_names_the_other_roads():
    text = tile_caption(None, 0, 0, zooms=[])
    assert "No offline map" in text
    assert "address" in text and "GPS" in text


def test_caption_at_a_fully_cached_level_says_carried():
    text = tile_caption(14, 6, 6, zooms=[4, 8, 14])
    assert "Zoom 14" in text and "carried" in text
    assert "enlarged" not in text


def test_caption_past_the_cache_admits_the_picture_is_enlarged():
    text = tile_caption(15, 0, 6, zooms=[4, 8, 14])
    assert "Zoom 15" in text
    assert "no tiles" in text.lower() and "enlarged" in text


def test_caption_with_a_spotty_level_gives_the_count():
    text = tile_caption(14, 2, 6, zooms=[4, 8, 14])
    assert "2 of 6" in text and "enlarged" in text


# -- the wiring, pinned by source ----------------------------------------------

def test_the_map_plot_delegates_its_zoom_rules_to_the_pure_helpers():
    """One set of rules for stepping and rendering: the widget must call the
    tested helpers, not carry a private copy that can drift."""
    step = func_source("ui/screens/scan_screen.py", "_step_to_next_zoom",
                       cls="MapPlot")
    assert "next_zoom(" in step and "OVERZOOM_MAX" in step
    draw = func_source("ui/screens/scan_screen.py", "_draw_tiled", cls="MapPlot")
    assert "render_zoom(" in draw and "OVERZOOM_MAX" in draw


def test_the_map_plot_reports_its_view_and_centre_to_the_popup():
    plot = src("ui/screens/scan_screen.py")
    assert "on_view=None" in plot, "MapPlot takes an on_view callback"
    for name in ("centre_latlon", "view_tile_report", "can_zoom"):
        func_source("ui/screens/scan_screen.py", name, cls="MapPlot")
    draw = func_source("ui/screens/scan_screen.py", "_draw_tiled", cls="MapPlot")
    assert "_notify_view" in draw, "every drawn view reaches the popup"


def test_the_popup_has_plus_minus_buttons_that_do_not_depend_on_pinch():
    popup = src("ui/widgets/confirm_location.py")
    init = func_source("ui/widgets/confirm_location.py", "__init__",
                       cls="ConfirmLocationPopup")
    assert "zoom_by(" in init, "+/− call the widget's explicit zoom step"
    assert '("+", +1)' in init and '("−", -1)' in init
    assert "FloatLayout" in popup, "the +/− sit OVER the map, as on SCAN"


def test_the_popup_draws_a_centre_marker_and_offers_select_here():
    popup = src("ui/widgets/confirm_location.py")
    assert "class _Crosshair" in popup
    sel = func_source("ui/widgets/confirm_location.py", "_select_here",
                      cls="ConfirmLocationPopup")
    assert "centre_latlon()" in sel, "Select here adopts the MAP CENTRE"
    assert "_move_pin(" in sel, "…through the same path a tap / address uses"
    init = func_source("ui/widgets/confirm_location.py", "__init__",
                       cls="ConfirmLocationPopup")
    assert "Select here" in init
    assert "_find_address" in init and "_use_gps" in init, (
        "the typed address and GPS remain ways to get there")


def test_the_marker_never_swallows_a_touch_meant_for_the_map():
    cross = src("ui/widgets/confirm_location.py")
    at = cross.index("class _Crosshair")
    body = cross[at:cross.index("\nclass ", at + 1)]      # this class only
    assert "on_touch_down" not in body, (
        "a plain Widget passes touches through; a handler here would eat the "
        "pan/pinch/tap under the marker")


def test_the_popup_prints_both_numbers_the_pin_and_the_centre():
    view_cb = func_source("ui/widgets/confirm_location.py", "_on_view",
                          cls="ConfirmLocationPopup")
    assert "centre_of(" in view_cb and "format_pin(" in view_cb
    assert "tile_caption(" in view_cb, "the honest zoom/tiles caption"
    assert "can_zoom(" in view_cb, "+/− grey out where a press would do nothing"
    coord = func_source("ui/widgets/confirm_location.py", "_coord_text",
                        cls="ConfirmLocationPopup")
    assert "format_pin(" in coord


def test_the_map_does_not_fetch_tiles_mid_birth():
    """Zoom works with the carried cache only — a birth never reaches for
    the network to fill a level (the popup opens offline in the field)."""
    popup = src("ui/widgets/confirm_location.py")
    for banned in ("download_region", "download_node_details", "add_point_detail",
                   "urllib", "requests"):
        assert banned not in popup, banned
    pick = src("ui/map_pick.py")
    assert "import kivy" not in pick and "from kivy" not in pick, (
        "the helper stays Kivy-free so it can be tested directly")


def test_the_popup_only_chooses_a_location_never_how_it_is_shared():
    """The location-privacy design (monitor/geo.py FUZZ rules) is untouched:
    the popup hands back exact coordinates to the caller, as before, and has
    no opinion on what is announced."""
    popup = src("ui/widgets/confirm_location.py")
    assert "fuzz" not in popup.lower()
    confirm = func_source("ui/widgets/confirm_location.py", "_confirm",
                          cls="ConfirmLocationPopup")
    assert "self._on_confirm(self._lat, self._lon)" in confirm
