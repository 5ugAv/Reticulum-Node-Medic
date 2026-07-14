"""Offline basemap download — tile maths, circle clipping, MBTiles writing.

The network fetch is injected, so the whole pipeline is exercised here without
touching a tile server. The key invariant: what MBTilesWriter writes,
ui.map_tiles.MBTiles must read back (they must agree on the TMS y-flip).
"""

import pytest

from ui.map_download import (
    radius_bounds, tiles_in_radius, estimate_download, MBTilesWriter,
    download_region, is_online, _km_between, _tile_center,
    DEFAULT_MIN_ZOOM, DEFAULT_MAX_ZOOM,
)
from ui.map_tiles import MBTiles, tile_of

MEL = (-37.81, 144.96)          # Melbourne-ish download point


# ---- geometry ------------------------------------------------------------

def test_radius_bounds_encloses_point_and_is_wider_in_lon():
    w, s, e, n = radius_bounds(*MEL, 100.0)
    lat, lon = MEL
    assert s < lat < n and w < lon < e
    # a degree of longitude is shorter than latitude away from the equator,
    # so the same km reaches further in lon-degrees
    assert (e - w) > (n - s)


def test_tiles_in_radius_all_within_the_circle():
    lat, lon = MEL
    tiles = tiles_in_radius(lat, lon, 80.0, zmin=10, zmax=10)
    assert tiles
    for z, x, y in tiles:
        clat, clon = _tile_center(x, y, z)
        assert _km_between(lat, lon, clat, clon) <= 80.0
        assert 0 <= x < 2 ** z and 0 <= y < 2 ** z


def test_tiles_in_radius_clips_to_circle_not_bounding_box():
    lat, lon = MEL
    z = 11
    circle = len(tiles_in_radius(lat, lon, 100.0, zmin=z, zmax=z))
    # brute bounding-box count at the same zoom
    w, s, e, n = radius_bounds(lat, lon, 100.0)
    x0, y0 = tile_of(n, w, z)
    x1, y1 = tile_of(s, e, z)
    box = (x1 - x0 + 1) * (y1 - y0 + 1)
    assert circle < box                       # corners of the square dropped


def test_tiles_in_radius_more_tiles_at_higher_zoom():
    lat, lon = MEL
    low = len(tiles_in_radius(lat, lon, 100.0, zmin=8, zmax=10))
    high = len(tiles_in_radius(lat, lon, 100.0, zmin=8, zmax=12))
    assert high > low


def test_tiles_are_unique():
    tiles = tiles_in_radius(*MEL, 100.0, zmin=8, zmax=12)
    assert len(tiles) == len(set(tiles))


def test_estimate_matches_tile_count():
    lat, lon = MEL
    count, mb = estimate_download(lat, lon, 100.0, 8, 11)
    assert count == len(tiles_in_radius(lat, lon, 100.0, 8, 11))
    assert mb > 0


# ---- writer <-> reader round trip ---------------------------------------

def _writer(tmp_path):
    return MBTilesWriter(str(tmp_path / "m.mbtiles"), "test",
                         (144.0, -38.0, 146.0, -37.0), 10, 12)


def test_writer_tile_is_read_back_by_the_reader(tmp_path):
    p = tmp_path / "m.mbtiles"
    w = MBTilesWriter(str(p), "test", (144.0, -38.0, 146.0, -37.0), 11, 11)
    w.put(11, 1850, 1266, b"PNGDATA")
    w.close()
    # the reader applies the TMS flip; the writer must have stored it flipped
    mb = MBTiles(str(p))
    assert mb.get_tile(11, 1850, 1266) == b"PNGDATA"
    assert mb.get_tile(11, 1850, 1267) is None
    mb.close()


def test_writer_bounds_metadata_round_trips(tmp_path):
    p = tmp_path / "m.mbtiles"
    MBTilesWriter(str(p), "test", (144.5, -38.0, 145.5, -37.0), 10, 12).close()
    # reader returns (west, south, east, north)
    assert MBTiles(str(p)).bounds() == (144.5, -38.0, 145.5, -37.0)


def test_writer_has_and_is_idempotent(tmp_path):
    w = _writer(tmp_path)
    assert w.has(11, 5, 5) is False
    w.put(11, 5, 5, b"A")
    assert w.has(11, 5, 5) is True
    w.put(11, 5, 5, b"B")                     # replace, not duplicate
    w.commit()
    n = w.conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    assert n == 1
    w.close()


# ---- the download loop (fake fetch) -------------------------------------

def _fake_fetch(z, x, y):
    return f"tile:{z}/{x}/{y}".encode()


def test_download_region_writes_every_tile_and_reader_sees_them(tmp_path):
    p = str(tmp_path / "region.mbtiles")
    events = []
    summary = download_region(*MEL, p, radius_km=60.0, zmin=8, zmax=10,
                              fetch=_fake_fetch, rate_limit_s=0,
                              on_progress=events.append)
    assert summary["fetched"] == summary["total"] > 0
    assert summary["failed"] == 0 and summary["cancelled"] is False
    assert events and events[-1] == summary        # final progress == summary
    mb = MBTiles(p)
    z, x, y = tiles_in_radius(*MEL, 60.0, 8, 10)[0]
    assert mb.get_tile(z, x, y) == _fake_fetch(z, x, y)
    mb.close()


def test_download_region_resumes_without_refetching(tmp_path):
    p = str(tmp_path / "region.mbtiles")
    first = download_region(*MEL, p, radius_km=40.0, zmin=9, zmax=10,
                            fetch=_fake_fetch, rate_limit_s=0)
    second = download_region(*MEL, p, radius_km=40.0, zmin=9, zmax=10,
                             fetch=_fake_fetch, rate_limit_s=0)
    assert first["fetched"] == first["total"]
    assert second["fetched"] == 0 and second["skipped"] == second["total"]


def test_download_region_counts_failures_without_aborting(tmp_path):
    p = str(tmp_path / "region.mbtiles")
    def flaky(z, x, y):
        return None if (x % 2 == 0) else b"ok"
    summary = download_region(*MEL, p, radius_km=40.0, zmin=10, zmax=10,
                              fetch=flaky, rate_limit_s=0)
    assert summary["failed"] > 0
    assert summary["fetched"] + summary["failed"] == summary["total"]


def test_is_online_false_for_an_unreachable_endpoint():
    # a discard/blackhole address on a closed port -> connect fails fast -> False
    assert is_online(host="192.0.2.1", port=9, timeout=0.5) is False


def test_download_region_can_be_cancelled(tmp_path):
    p = str(tmp_path / "region.mbtiles")
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 5                   # cancel after a few tiles
    summary = download_region(*MEL, p, radius_km=100.0, zmin=8, zmax=12,
                              fetch=_fake_fetch, rate_limit_s=0, stop=stop)
    assert summary["cancelled"] is True
    assert summary["done"] < summary["total"]
