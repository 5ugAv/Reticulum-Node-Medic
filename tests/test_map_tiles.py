import sqlite3

import pytest

from ui.map_tiles import (
    TILE_SIZE, project_px, tile_of, fit_zoom, build_view, tiles_for_view, MBTiles,
    find_mbtiles,
)


# ---- Web Mercator projection ---------------------------------------------

def test_project_px_origin_at_zoom0():
    # zoom 0: world is one 256px tile; (lon 0, lat 0) is the centre
    x, y = project_px(0.0, 0.0, 0)
    assert x == pytest.approx(TILE_SIZE / 2)
    assert y == pytest.approx(TILE_SIZE / 2)


def test_project_px_lon_edges():
    assert project_px(0.0, -180.0, 0)[0] == pytest.approx(0.0)
    assert project_px(0.0, 180.0, 0)[0] == pytest.approx(TILE_SIZE)


def test_project_px_north_is_smaller_y():
    # world-pixel y increases DOWNWARD, so a more-northern lat has a smaller y
    north = project_px(60.0, 10.0, 5)[1]
    south = project_px(-60.0, 10.0, 5)[1]
    assert north < south


def test_project_px_clamps_poles():
    # beyond the Mercator limit must not blow up (log domain); clamps to the top
    y = project_px(89.9, 0.0, 3)[1]
    assert y == pytest.approx(0.0, abs=1e-3)


def test_tile_of_at_zoom0_is_origin():
    assert tile_of(-37.8, 144.9, 0) == (0, 0)


# ---- zoom fitting ---------------------------------------------------------

def test_fit_zoom_smaller_bbox_gets_higher_zoom():
    big = fit_zoom(-38.0, -37.0, 144.0, 145.0, 400, 300)     # ~1 degree
    small = fit_zoom(-37.81, -37.80, 144.96, 144.97, 400, 300)  # ~0.01 degree
    assert small > big


def test_fit_zoom_single_point_is_max():
    assert fit_zoom(-37.8, -37.8, 144.9, 144.9, 400, 300, max_zoom=17) == 17


# ---- viewport transform (dots align with tiles) --------------------------

def test_view_north_maps_to_higher_screen_y():
    v = build_view(-38.0, -37.0, 144.0, 145.0, 400, 300)
    s_south = v.to_screen(-38.0, 144.5)[1]
    s_north = v.to_screen(-37.0, 144.5)[1]
    assert s_north > s_south             # Kivy y-up: north is higher on screen


def test_view_centres_the_bbox():
    v = build_view(-38.0, -37.0, 144.0, 145.0, 400, 300)
    cx, cy = v.to_screen(-37.5, 144.5)   # bbox centre
    assert cx == pytest.approx(200, abs=1)
    assert cy == pytest.approx(150, abs=1)


def test_tiles_cover_the_viewport():
    v = build_view(-38.0, -37.0, 144.0, 145.0, 400, 300)
    tiles = tiles_for_view(v)
    assert tiles
    # every tile is at the fitted zoom and within world bounds
    n = 2 ** v.zoom
    assert all(t.z == v.zoom and 0 <= t.x < n and 0 <= t.y < n for t in tiles)
    # the union of tiles spans across the viewport width
    xs = [t.screen_x for t in tiles]
    assert min(xs) <= 0 and max(xs) + TILE_SIZE >= v.width


# ---- MBTiles reader (TMS y-flip) -----------------------------------------

def _make_mbtiles(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, "
                "tile_row INT, tile_data BLOB)")
    con.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    # a tile stored in TMS order: for z=2, xyz y=1 -> tms_row = (2^2-1) - 1 = 2
    con.execute("INSERT INTO tiles VALUES (2, 1, 2, ?)", (b"PNGDATA",))
    con.execute("INSERT INTO metadata VALUES ('bounds', '144.5,-38.0,145.5,-37.0')")
    con.commit(); con.close()


def test_mbtiles_get_tile_flips_tms_y(tmp_path):
    p = tmp_path / "melб.mbtiles"
    _make_mbtiles(str(p))
    mb = MBTiles(str(p))
    assert mb.get_tile(2, 1, 1) == b"PNGDATA"     # xyz y=1 -> tms row 2
    assert mb.get_tile(2, 1, 0) is None           # nothing at that xyz tile
    mb.close()


def test_mbtiles_bounds_parsed(tmp_path):
    p = tmp_path / "b.mbtiles"
    _make_mbtiles(str(p))
    mb = MBTiles(str(p))
    assert mb.bounds() == (144.5, -38.0, 145.5, -37.0)
    mb.close()


def test_find_mbtiles_opens_carried_basemap(tmp_path):
    assert find_mbtiles(str(tmp_path)) is None          # empty dir -> fallback
    _make_mbtiles(str(tmp_path / "melbourne.mbtiles"))
    mb = find_mbtiles(str(tmp_path))
    assert mb is not None and mb.get_tile(2, 1, 1) == b"PNGDATA"
    mb.close()


def test_mbtiles_bounds_absent_is_none(tmp_path):
    p = tmp_path / "nob.mbtiles"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, "
                "tile_row INT, tile_data BLOB)")
    con.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    con.commit(); con.close()
    assert MBTiles(str(p)).bounds() is None


# ---- interactive view (pan/pinch support) -----------------------------------

def test_unproject_inverts_project():
    from ui.map_tiles import project_px, unproject_px
    lat, lon = -37.79, 144.96
    for z in (8, 10, 12):
        x, y = project_px(lat, lon, z)
        la, lo = unproject_px(x, y, z)
        assert abs(la - lat) < 1e-6 and abs(lo - lon) < 1e-6


def test_view_at_centres_the_point():
    from ui.map_tiles import view_at
    v = view_at(-37.79, 144.96, 11, 700, 500)
    sx, sy = v.to_screen(-37.79, 144.96)
    assert abs(sx - 350) < 1e-6 and abs(sy - 250) < 1e-6
