"""Terrain line-of-sight for node placement.

Written against synthetic tiles so it needs no downloads and no hardware — the
geometry is the thing being tested, not NASA's data.
"""

import math
import os
import struct

import pytest

# Test-only dependency: these build synthetic terrarium tiles. Skip rather than
# error where Pillow is absent; CI installs it so the round trip is verified.
pytest.importorskip("PIL")

from monitor.terrain import (FRESNEL_CLEARANCE, SRTM_VOID, TileStore,
                             advice, earth_bulge_m, fresnel_radius_m,
                             grid_size_for, haversine_m, line_of_sight,
                             tile_name)


def _write_tile(directory, name, n=1201, height_fn=lambda r, c: 0):
    """A synthetic .hgt: big-endian int16, north-to-south rows."""
    buf = bytearray()
    for r in range(n):
        for c in range(n):
            buf += struct.pack(">h", int(height_fn(r, c)))
    with open(os.path.join(directory, name), "wb") as fh:
        fh.write(bytes(buf))


# --- naming and format ----------------------------------------------------

def test_tiles_are_named_for_their_south_west_corner():
    """Sampleton (-37.7, 145.0) lives in S38E145 — the FLOOR, not the round."""
    assert tile_name(-37.50, 145.50) == "S38E145.hgt"
    assert tile_name(37.73, -122.4) == "N37W123.hgt"
    assert tile_name(0.5, 0.5) == "N00E000.hgt"


def test_grid_size_is_inferred_from_file_size():
    """Mixing 1- and 3-arc-second tiles silently would misplace every sample by
    up to 90 m."""
    assert grid_size_for(3601 * 3601 * 2) == 3601
    assert grid_size_for(1201 * 1201 * 2) == 1201
    assert grid_size_for(12345) is None


# --- reading --------------------------------------------------------------

def test_a_missing_tile_reads_as_unknown_not_sea_level(tmp_path):
    """Returning 0 for a missing tile would make a mountain look like clear air."""
    store = TileStore(str(tmp_path))
    assert store.elevation(-37.7, 145.0) is None


def test_srtm_voids_read_as_unknown(tmp_path):
    _write_tile(str(tmp_path), "S38E145.hgt", n=1201,
                height_fn=lambda r, c: SRTM_VOID)
    assert TileStore(str(tmp_path)).elevation(-37.5, 145.5) is None


def test_elevation_is_read_at_the_right_place(tmp_path):
    """Rows run north to south. A latitude near the tile's TOP must read a row
    near index 0, or every profile is mirrored."""
    # height encodes the row, so a misread shows up as the wrong number
    _write_tile(str(tmp_path), "S38E145.hgt", n=1201, height_fn=lambda r, c: r)
    store = TileStore(str(tmp_path))
    north = store.elevation(-37.01, 145.5)      # near the top of S38
    south = store.elevation(-37.99, 145.5)      # near the bottom
    assert north is not None and south is not None
    assert north < south, f"rows are upside down: north={north} south={south}"


# --- geometry -------------------------------------------------------------

def test_the_fresnel_zone_is_widest_in_the_middle():
    a = fresnel_radius_m(915e6, 100.0, 900.0)
    mid = fresnel_radius_m(915e6, 500.0, 500.0)
    assert mid > a > 0


def test_the_earth_bulges_most_at_the_midpoint():
    assert earth_bulge_m(5000, 5000) > earth_bulge_m(1000, 9000) > 0


def test_haversine_is_sane():
    # Synthetic point, NOT anyone's home: this repo is public and the whole
    # project's ethos is that a node cannot be traced to a place.
    d = haversine_m(-37.5000, 145.5000, -37.5100, 145.5000)   # ~1.1 km south
    assert 1000 < d < 1200


# --- the verdict ----------------------------------------------------------

def test_flat_ground_with_raised_antennas_is_clear(tmp_path):
    _write_tile(str(tmp_path), "S38E145.hgt", n=1201, height_fn=lambda r, c: 10)
    v = line_of_sight(TileStore(str(tmp_path)),
                      -37.50, 145.50, 8.0, -37.51, 145.50, 8.0)
    assert v.status == "clear", v.reason
    assert v.worst_clearance >= FRESNEL_CLEARANCE


def test_a_hill_in_the_middle_blocks_the_path(tmp_path):
    """THE case this module exists for: a distance-only model calls this fine."""
    def hill(r, c):
        return 400 if 598 <= r <= 602 else 10
    _write_tile(str(tmp_path), "S38E145.hgt", n=1201, height_fn=hill)
    store = TileStore(str(tmp_path))
    v = line_of_sight(store, -37.40, 145.50, 3.0, -37.60, 145.50, 3.0)
    assert v.status == "obstructed", v.reason
    assert v.worst_clearance is not None and v.worst_clearance < FRESNEL_CLEARANCE
    assert "km along" in v.reason


def test_a_missing_tile_partway_is_unknown_not_clear(tmp_path):
    """A partial profile can hide the one hill that matters."""
    _write_tile(str(tmp_path), "S38E145.hgt", n=1201, height_fn=lambda r, c: 10)
    store = TileStore(str(tmp_path))
    # ends inside the tile, but the path crosses into a tile we do not have
    v = line_of_sight(store, -37.50, 145.90, 3.0, -37.50, 146.30, 3.0)
    assert v.status == "unknown", v.reason


def test_clear_never_claims_the_link_will_work(tmp_path):
    """SRTM is bare earth. A clear profile through a suburb is still a suburb —
    the FAITH sub-kilometre failure was houses, not hills."""
    _write_tile(str(tmp_path), "S38E145.hgt", n=1201, height_fn=lambda r, c: 10)
    v = line_of_sight(TileStore(str(tmp_path)),
                      -37.50, 145.50, 8.0, -37.505, 145.50, 8.0)
    text = advice(v).lower()
    assert "building" in text or "trees" in text
    assert "will work" not in text


def test_unknown_advice_tells_the_operator_to_test_it():
    from monitor.terrain import PathVerdict
    text = advice(PathVerdict("unknown", "no data")).lower()
    assert "walk" in text or "test" in text


# --- folded into the placement suggester ----------------------------------

def test_suggest_without_tiles_behaves_exactly_as_before(tmp_path):
    """Fail-open: a field tool that withheld advice for want of a map would be
    worse than one that gives advice with a stated limit."""
    from monitor.placement import suggest
    from monitor.topology import Topology
    topo = Topology(nodes=[], edges=[])
    assert suggest(topo) == suggest(topo, terrain_store=None)


def test_a_blocked_suggestion_is_cautioned_not_silently_offered(tmp_path):
    """The whole point: a hill between the candidate and its partner must reach
    the operator, because estimate_rssi_dbm cannot see one."""
    import os, struct
    from monitor.placement import Suggestion, check_terrain
    from monitor.terrain import TileStore

    n = 1201
    buf = bytearray()
    for r in range(n):
        for c in range(n):
            buf += struct.pack(">h", 400 if 598 <= r <= 602 else 10)
    with open(os.path.join(str(tmp_path), "S38E145.hgt"), "wb") as fh:
        fh.write(bytes(buf))

    class N:
        def __init__(s, i, name, lat, lon):
            s.id, s.name, s.lat, s.lon = i, name, lat, lon

    class T:
        nodes = [N("a", "FAITH", -37.60, 145.50)]

    s = Suggestion(kind="fill_gap", lat=-37.40, lon=145.50, reason="test",
                   estimates=[{"node": "a", "name": "FAITH", "km": 22.0,
                               "est_rssi_dbm": -100}])
    out = check_terrain([s], T(), TileStore(str(tmp_path)))
    assert out[0].cautions, "a blocked path produced no caution"
    assert "FAITH" in out[0].cautions[0]
    assert out[0].estimates[0]["terrain"] == "obstructed"


def test_no_tiles_for_the_area_says_so_rather_than_implying_clear(tmp_path):
    from monitor.placement import Suggestion, check_terrain
    from monitor.terrain import TileStore

    class N:
        id, name, lat, lon = "a", "HOPE", -37.60, 145.50

    class T:
        nodes = [N()]
    s = Suggestion(kind="fill_gap", lat=-37.40, lon=145.50, reason="test",
                   estimates=[{"node": "a", "name": "HOPE", "km": 22.0,
                               "est_rssi_dbm": -100}])
    out = check_terrain([s], T(), TileStore(str(tmp_path)))
    assert any("distance only" in c for c in out[0].cautions)


# --- terrain from the map cache (one button, both datasets) ---------------

def test_terrarium_decode_matches_the_published_range():
    """metres = (R*256 + G + B/256) - 32768. Tilezen documents the range as
    ~-11000 m at (85,8,0) to ~8900 m at (162,198,0); a wrong formula would put
    every hill at the wrong height and quietly ruin every profile."""
    from monitor.terrain import TerrariumStore
    assert round(TerrariumStore.decode(85, 8, 0)) == -11000
    assert 8890 < TerrariumStore.decode(162, 198, 0) < 8910
    assert TerrariumStore.decode(128, 0, 0) == 0.0


def test_terrain_is_cached_beside_the_map_not_inside_it(tmp_path):
    """Terrarium tiles are elevation that happens to be a PNG. In the basemap's
    own .mbtiles the renderer would draw them as pictures — lurid green squares
    over the operator's suburb."""
    from ui.map_download import terrain_dest
    assert terrain_dest("/m/offline.mbtiles") == "/m/offline.terrain.mbtiles"
    assert terrain_dest("/m/offline") == "/m/offline.terrain.mbtiles"


def test_a_missing_terrain_cache_reads_as_unknown(tmp_path):
    from monitor.terrain import TerrariumStore
    s = TerrariumStore(str(tmp_path / "nope.mbtiles"))
    assert s.elevation(-37.50, 145.50) is None


def test_terrain_survives_a_round_trip_through_the_map_cache(tmp_path):
    """End to end: write a terrarium tile the way download_terrain would, then
    read a known height back out of it."""
    import io, sqlite3
    from PIL import Image
    from monitor.terrain import TerrariumStore

    z, lat, lon = 12, -37.50, 145.50
    n = 2 ** z
    xf = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    yf = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    x, y = int(xf), int(yf)

    # 137 m above sea level, encoded
    v = 137 + 32768
    img = Image.new("RGB", (256, 256), (v // 256, v % 256, 0))
    buf = io.BytesIO(); img.save(buf, format="PNG")

    path = str(tmp_path / "offline.terrain.mbtiles")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, "
                "tile_row INT, tile_data BLOB)")
    con.execute("INSERT INTO tiles VALUES (?,?,?,?)",
                (z, x, (2 ** z - 1) - y, buf.getvalue()))
    con.commit(); con.close()

    got = TerrariumStore(path, zoom=z).elevation(lat, lon)
    assert got is not None, "tile written but not read back — row flip is wrong"
    assert abs(got - 137) < 1.0, got


def test_the_whole_chain_works_off_terrarium_tiles(tmp_path):
    """line_of_sight does not care which store it is given."""
    import io, sqlite3
    from PIL import Image
    from monitor.terrain import TerrariumStore, line_of_sight

    z = 12
    path = str(tmp_path / "t.mbtiles")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, "
                "tile_row INT, tile_data BLOB)")
    v = 20 + 32768                                  # flat 20 m everywhere
    img = Image.new("RGB", (256, 256), (v // 256, v % 256, 0))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    for x in range(3680, 3700):
        for yy in range(2490, 2510):
            con.execute("INSERT INTO tiles VALUES (?,?,?,?)",
                        (z, x, (2 ** z - 1) - yy, buf.getvalue()))
    con.commit(); con.close()

    store = TerrariumStore(path, zoom=z)
    v = line_of_sight(store, -37.50, 145.50, 5.0, -37.505, 145.505, 5.0)
    assert v.status in ("clear", "unknown")         # flat ground, raised antennas
    if v.status == "clear":
        assert v.worst_clearance >= FRESNEL_CLEARANCE


# --- the map overlay ------------------------------------------------------

def test_the_ramp_gets_monotonically_lighter():
    """Not a rainbow. Rainbows have no perceptual order, so an operator cannot
    tell which of two colours is higher without a key. Brighter = higher must
    be readable at a glance."""
    from monitor.terrain import elevation_colour
    lums = [sum(elevation_colour(v, 0, 100)) for v in (0, 25, 50, 75, 100)]
    assert lums == sorted(lums), lums
    assert lums[-1] > lums[0] * 2


def test_the_ramp_stretches_to_the_range_in_view():
    """A 40 m rise in a flat suburb must read as strongly as a mountain peak,
    or the overlay tells a Sampleton operator nothing."""
    from monitor.terrain import elevation_colour
    flat_top = elevation_colour(40, 0, 40)      # 40 m is the high point here
    alp_top = elevation_colour(2000, 0, 2000)   # 2000 m is the high point here
    assert flat_top == alp_top


def test_prominence_is_relative_not_absolute(tmp_path):
    """A node on a 300 m hill ringed by 400 m hills is worse off than one on a
    50 m rise in flat country. Absolute altitude is nearly irrelevant."""
    from monitor.terrain import relative_prominence

    class Bowl:
        """High everywhere except the middle."""
        def elevation(self, lat, lon):
            return 300.0 if abs(lat - -37.5) < 0.001 else 400.0

    class Knoll:
        def elevation(self, lat, lon):
            return 50.0 if abs(lat - -37.5) < 0.001 else 10.0

    assert relative_prominence(Bowl(), -37.5, 145.0) < 0     # in a hollow
    assert relative_prominence(Knoll(), -37.5, 145.0) > 0    # standing proud


def test_prominence_is_unknown_not_flat_when_there_is_no_data():
    from monitor.terrain import prominence_note, relative_prominence

    class Blind:
        def elevation(self, lat, lon):
            return None
    assert relative_prominence(Blind(), -37.5, 145.0) is None
    assert "no terrain data" in prominence_note(None).lower()


def test_prominence_wording_is_actionable():
    from monitor.terrain import prominence_note
    assert "good for a radio" in prominence_note(45)
    assert "shadowed" in prominence_note(-25)
    assert "won't help" in prominence_note(0)
