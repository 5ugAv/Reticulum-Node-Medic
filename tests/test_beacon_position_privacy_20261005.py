"""A node's beacon carries the position its keeper ALLOWED, never its live fix
(readiness ledger #157).

Firmware side: the position tail is the point the medic told the node to
announce at birth (already privacy-fuzzed), with the fuzzed bit set — or the
sentinel when sharing is Hidden. Medic side: a fuzzed claim never displaces a
confirmed location, and where it is all there is, the map draws a ring.
"""
import time

from monitor.health_beacon import HealthBeacon
from monitor.registry import NodeRegistry
from ui.map_projection import geo_points

H = "ab" * 16


def _beacon(lat, lng, fuzzed):
    b = HealthBeacon(3, 10, 100, -60, 0, True, True, False, True, True,
                     False, False, False, 1, "0.7.0")
    b.lat, b.lng, b.position_fuzzed = lat, lng, fuzzed
    return b


def _reg(with_location):
    reg = NodeRegistry()
    now = time.time()
    reg.ingest_announce(bytes.fromhex(H), b"", now)
    entry = {"name": "TESTNODE", "type": "rtnode2400", "device": H}
    if with_location:
        entry.update(lat=-37.7, lon=145.0)
    reg.set_kin_roster({H: entry})
    return reg, now


def test_a_fuzzed_claim_never_displaces_a_confirmed_location():
    reg, now = _reg(with_location=True)
    reg.get(H).latest_beacon = _beacon(-37.75, 145.05, fuzzed=True)
    (dot,) = reg.located_nodes(now)
    assert (dot["lat"], dot["lon"]) == (-37.7, 145.0)
    assert dot["approximate"] is False and dot["self_located"] is False


def test_a_fuzzed_claim_alone_is_drawn_as_approximate():
    reg, now = _reg(with_location=False)
    reg.get(H).latest_beacon = _beacon(-37.75, 145.05, fuzzed=True)
    (dot,) = reg.located_nodes(now)
    assert (dot["lat"], dot["lon"]) == (-37.75, 145.05)
    assert dot["approximate"] is True and dot["self_located"] is False
    (pt,) = geo_points([dot])
    assert pt.approximate is True


def test_an_exact_claim_still_outranks_the_birth_stamp():
    reg, now = _reg(with_location=True)
    reg.get(H).latest_beacon = _beacon(-37.75, 145.05, fuzzed=False)
    (dot,) = reg.located_nodes(now)
    assert (dot["lat"], dot["lon"]) == (-37.75, 145.05)
    assert dot["self_located"] is True and dot["approximate"] is False
    (pt,) = geo_points([dot])
    assert pt.approximate is False


def test_the_firmware_beacons_the_allowed_point_not_the_live_fix():
    src = open("firmware/rtnode-2400/HealthBeacon.h", encoding="utf-8").read()
    start = src.index("v3 position tail")
    block = src[start:src.index("#else", start)]
    assert "firewall_state.advert_enabled" in block
    assert "firewall_state.advert_lat" in block and "firewall_state.advert_lon" in block
    assert "gps.location.lat()" not in block and "gps.location.lng()" not in block
    assert "(uint8_t)gps.satellites.value(), fuzzed);" in block


def test_the_allowed_position_is_read_only_where_a_portal_exists():
    """First T114 compile of the GPS block (2026-10-05): 'firewall_state' was
    not declared — it exists only under FIREWALL_MODE (Wi-Fi boards with the
    portal). A pure RNS build such as the nRF52 T114 has no policy to read,
    so it sends the sentinel: hidden. The guard keeps that build compiling."""
    import pathlib
    src = pathlib.Path("firmware/rtnode-2400/HealthBeacon.h").read_text()
    gps = src[src.index("#if HAS_GPS"):src.index("health_pack_beacon_v3(out,")]
    assert "#ifdef FIREWALL_MODE" in gps and gps.count("#endif") >= 1
    assert gps.index("#ifdef FIREWALL_MODE") < gps.index("firewall_state.advert_enabled")
    assert "int32_t lat_u = HB_POSITION_UNKNOWN" in gps   # the default is hidden
