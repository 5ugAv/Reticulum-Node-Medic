"""A carried map must not say where its keeper lives: Node Medic 1's region
and terrain tiers were centred on the first fix and the terrain file's
metadata NAMED the point ("terrain 200km @ -37.512345,145.523456"); every clone
carried it (2026-10-07)."""
import os
import re
import sqlite3

from ui import map_download as md


def test_the_coarse_centre_is_a_grid_point_not_the_keeper():
    lat, lon = md.coarse_centre(-37.512345, 145.523456)
    assert (lat, lon) == (-37.5, 145.5)
    assert md.coarse_centre(51.5074, -0.1278) == (51.5, 0.0)


def test_an_anonymised_circle_still_contains_the_requested_one():
    clat, clon, r = md.anonymised_circle(-37.512345, 145.523456, 200.0)
    assert (clat, clon) == (-37.5, 145.5)
    assert r > 200.0 and abs((r - 200.0) - md._km_between(-37.512345, 145.523456, clat, clon)) < 1e-9


def test_area_names_never_carry_a_coordinate():
    assert md.area_name("terrain", 200) == "terrain 200km"
    assert "37" not in md.area_name("offline", 225.4)


def _fake_fetch(z, x, y, **kw):
    return b"\x89PNG" + bytes([z, x & 0xFF, y & 0xFF])


def test_region_and_terrain_metadata_are_centred_on_the_grid(tmp_path):
    dest = str(tmp_path / "offline.mbtiles")
    md.download_region(-37.512345, 145.523456, dest, radius_km=5, zmin=9, zmax=9,
                       fetch=_fake_fetch, rate_limit_s=0)
    meta = dict(sqlite3.connect(dest).execute("SELECT name, value FROM metadata").fetchall())
    assert meta["name"] == "offline 5km" or meta["name"].startswith("offline ")
    assert not re.search(r"-?\d{2}\.\d{2,}", meta["name"])      # no coordinate in the name
    assert meta["center"].startswith("145.5,-37.5,")
    md.download_terrain(-37.512345, 145.523456, dest, radius_km=5, fetch=_fake_fetch, rate_limit_s=0)
    tmeta = dict(sqlite3.connect(md.terrain_dest(dest)).execute("SELECT name, value FROM metadata").fetchall())
    assert tmeta["name"].startswith("terrain ") and "@" not in tmeta["name"]
    assert tmeta["center"].startswith("145.5,-37.5,")


def test_sanitise_scrubs_old_files_and_can_drop_the_node_detail_zooms(tmp_path):
    path = str(tmp_path / "old.mbtiles")
    w = md.MBTilesWriter(path, "terrain 200km @ -37.512345,145.523456", (142.7, -39.5, 147.3, -35.9),
                         9, 15, center="145.523456,-37.512345,12")
    for z in (9, 12, 13, 15):
        w.put(z, 1, 1, b"x")
    w.commit(); w.close()
    meta = md.sanitise_carried_maps(path)
    assert meta["name"] == "terrain 200km" and meta["center"] == "145.5,-37.5,12"
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == 4
    meta = md.sanitise_carried_maps(path, drop_detail=True)
    rows = sqlite3.connect(path).execute("SELECT zoom_level FROM tiles ORDER BY 1").fetchall()
    assert rows == [(9,), (12,)] and meta["maxzoom"] == "12"


def test_the_clone_sanitises_what_it_hands_down():
    from tests.srcutil import func_source
    body = func_source("workflows/clone.py", "copy_offline_maps")
    assert "sanitise_carried_maps" in body and "drop_detail={drop}" in body
    assert 'getattr(wf, "fresh_fleet", False)' in body
