import pytest

from monitor.geo import (
    GpsFix,
    read_gps,
    format_coord,
    maps_url,
    navigation_links,
)


def test_read_gps_with_fix():
    fix = read_gps(reader=lambda: (-37.814, 144.963))
    assert isinstance(fix, GpsFix)
    assert fix.has_fix
    assert fix.lat == -37.814
    assert fix.lon == 144.963
    assert fix.source == "pi_gps"


def test_read_gps_no_fix_returns_none():
    assert read_gps(reader=lambda: None) is None


def test_read_gps_reader_error_returns_none():
    def boom():
        raise OSError("no gpsd")
    assert read_gps(reader=boom) is None


def test_format_coord_six_decimals_signed():
    assert format_coord(-37.814) == "-37.814000"
    assert format_coord(144.963) == "144.963000"


def test_maps_url_google_directions_contains_coords():
    url = maps_url(-37.814, 144.963, provider="google")
    assert "google.com" in url
    assert "-37.814000" in url and "144.963000" in url


def test_maps_url_apple():
    url = maps_url(-37.814, 144.963, provider="apple")
    assert "maps.apple.com" in url
    assert "-37.814000" in url


def test_navigation_links_has_all_forms():
    links = navigation_links(-37.814, 144.963)
    assert "google.com" in links["google"]
    assert "apple.com" in links["apple"]
    assert links["raw"] == "-37.814000, 144.963000"


# ---- location privacy (fuzzed public pin) ---------------------------------

def test_fuzz_is_deterministic_per_node():
    from monitor.geo import fuzz_location
    a = fuzz_location(-37.79, 144.96, "ad272c6b")
    b = fuzz_location(-37.79, 144.96, "ad272c6b")
    assert a == b                       # same node -> same fake pin, forever
    c = fuzz_location(-37.79, 144.96, "deadbeef")
    assert (a[0], a[1]) != (c[0], c[1])  # different node -> different offset


def test_fuzz_offsets_within_radius_but_never_at_centre():
    import math
    from monitor.geo import fuzz_location, FUZZ_RADIUS_M
    for key in ("n1", "n2", "n3", "n4", "n5"):
        flat, flon, r = fuzz_location(-37.79, 144.96, key)
        assert r == FUZZ_RADIUS_M
        dlat_m = (flat - -37.79) * 111_320.0
        dlon_m = (flon - 144.96) * 111_320.0 * math.cos(math.radians(-37.79))
        dist = math.hypot(dlat_m, dlon_m)
        assert 0.25 * r <= dist <= r     # offset real, bounded, off-centre
