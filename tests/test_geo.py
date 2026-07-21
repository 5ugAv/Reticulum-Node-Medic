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


# ---- GPS fix freshness (confirm-before-commit safety) -----------------------

def test_classify_fix_live_held_none():
    from monitor.geo import GpsFix, classify_fix
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=8, fix_quality=1)) == "live"
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=0, fix_quality=1)) == "held"
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=0, fix_quality=0)) == "none"
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=5, fix_quality=0)) == "none"
    assert classify_fix(None) == "none"


def test_fix_trust_verdicts_guard_stale_positions():
    from monitor.geo import GpsFix, fix_trust
    live = fix_trust(GpsFix(lat=-37.7, lon=145.0, sats=8, fix_quality=1))
    assert live["level"] == "live" and live["ok"] is True

    held = fix_trust(GpsFix(lat=-37.7, lon=145.0, sats=0, fix_quality=1))
    assert held["level"] == "held" and held["ok"] is False
    assert "where you were" in held["detail"].lower()   # warns it may be stale

    none = fix_trust(None)
    assert none["level"] == "none" and none["ok"] is False


# ---- address geocoding (field operator knows an address, not coords) ---------

def test_geocode_address_parses_nominatim():
    from monitor.geo import geocode_address
    fake = ('[{"lat": "-37.744", "lon": "145.001", '
            '"display_name": "366 High St, Preston VIC, Australia"}]')
    r = geocode_address("366 High St Preston", fetch=lambda url: fake)
    assert r["lat"] == -37.744 and r["lon"] == 145.001
    assert "Preston" in r["name"]
    # the query is URL-encoded into the request
    seen = {}
    geocode_address("366 High St, Preston", fetch=lambda url: seen.setdefault("u", url) or fake)
    assert "366" in seen["u"] and "High" in seen["u"]


def test_geocode_address_none_on_no_match_offline_or_empty():
    from monitor.geo import geocode_address
    assert geocode_address("nowhere at all", fetch=lambda url: "[]") is None   # no match
    def boom(url):
        raise OSError("offline")
    assert geocode_address("anywhere", fetch=boom) is None                     # offline
    assert geocode_address("", fetch=lambda url: "[]") is None                 # empty
    assert geocode_address("x", fetch=lambda url: "not json") is None          # bad body
