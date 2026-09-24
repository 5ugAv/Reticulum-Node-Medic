"""monitor/boundary_rings.py — the SCAN boundary-ring layer's pure core
(2026-09-24 review fixes #1-#4, #6, #14, #15).

Real behavioural tests against a real ``NodeRegistry``/``NodeRecord`` and
real ``LinkFailure`` objects — no source-text greps, no Kivy, no
``App.get_running_app()``. This is the function ``ui/app.py``'s
``_boundary_provider`` closure now calls; the closure itself is a thin
shim (registry + banked failures + clock) with nothing left to unit test
in isolation.
"""

import pytest

from monitor.boundary_rings import BOUNDARY_MAX_AGE_DAYS, boundary_rings_for
from monitor.boundary_shape import destination_point
from monitor.health_beacon import HealthBeacon
from monitor.observation import Observation
from monitor.registry import NodeRecord, NodeRegistry
from monitor.synapse_range import LinkFailure

NOW = 2_000_000_000.0
NODE = (10.0, -20.0)          # the record's own exact, roster-stamped position


def _fail(lat, lon, node_key, confirmed=True, observed_at=NOW,
         bearing=None, distance_km=None):
    """A LinkFailure at an explicit (lat, lon), or — given *bearing* +
    *distance_km* instead — one placed by the module's own geometry
    (monitor.boundary_shape.destination_point) out from NODE, so a ring is
    guaranteed to actually draw (a lone sector has no neighbour and
    boundary_segments draws nothing for it)."""
    if bearing is not None:
        lat, lon = destination_point(NODE[0], NODE[1], bearing, distance_km)
    return LinkFailure(distance_km=distance_km or 1.0, observed_at=observed_at,
                       lat=lat, lon=lon, node_key=node_key, confirmed=confirmed)


def _fail_pair(node_key, base_bearing=30.0, confirmed=True, observed_at=NOW):
    """Two failures in ADJACENT sectors (15 degrees apart — DEFAULT_SECTORS'
    width) so boundary_segments actually draws a ring, not an isolated dot."""
    return [_fail(None, None, node_key, confirmed=confirmed,
                  observed_at=observed_at, bearing=base_bearing, distance_km=2.0),
           _fail(None, None, node_key, confirmed=confirmed,
                 observed_at=observed_at, bearing=base_bearing + 15.0,
                 distance_km=2.1)]


def _beacon(lat=None, lng=None):
    """A minimal, syntactically valid v3 HealthBeacon — only lat/lng matter
    to these tests."""
    return HealthBeacon(format_version=3, uptime_s=100, free_heap_kb=140,
                        wifi_rssi_dbm=-60, reset_reason=0, wifi_up=True,
                        lora_up=True, tcp_backbone_up=True,
                        local_tcp_server_up=True, wdt_armed=True, psram=True,
                        fault=False, airtime_lock=False, board_id=0x3F,
                        firmware_version="0.6.2", lat=lat, lng=lng)


def _reachable_kin_record(dst_hash, identity_hash, name="RAIN",
                          lat=NODE[0], lon=NODE[1]):
    return NodeRecord(dst_hash=dst_hash, name=name, identity_hash=identity_hash,
                      lat=lat, lon=lon, mesh_hops=1,
                      seen=Observation.at(NOW, "mesh"))


# ---- one ring per device, never per aspect row ------------------------------

def test_two_aspect_rows_produce_exactly_one_ring_at_the_consolidated_position():
    reg = NodeRegistry()
    a = _reachable_kin_record("health-dst", "device-rain")
    b = _reachable_kin_record("lxmd-dst", "device-rain")
    reg.nodes[a.dst_hash] = a
    reg.nodes[b.dst_hash] = b
    # One failure keyed by the shared identity, one keyed by the SECOND
    # aspect's own dst_hash — both must land on the SAME device.
    fails = (_fail_pair("device-rain", base_bearing=10.0)
            + _fail_pair("lxmd-dst", base_bearing=100.0))
    rings = boundary_rings_for(reg, fails, NOW)
    assert len(rings) == 1
    ring = rings[0]
    assert ring["lat"] == NODE[0] and ring["lon"] == NODE[1]
    assert ring["status"] == a.status(NOW) == "ok"
    assert ring["segments"]                 # something actually drew
    assert ring["coverage_frac"] is not None
    assert ring["total_failures_used"] == 4


def test_a_second_unrelated_device_gets_its_own_ring():
    reg = NodeRegistry()
    a = _reachable_kin_record("dev-a-dst", "device-a", name="A")
    reg.nodes[a.dst_hash] = a
    other_node = (40.0, 60.0)
    b = NodeRecord(dst_hash="dev-b-dst", name="B", identity_hash="device-b",
                   lat=other_node[0], lon=other_node[1], mesh_hops=1,
                   seen=Observation.at(NOW, "mesh"))
    reg.nodes[b.dst_hash] = b
    fails_a = _fail_pair("device-a", base_bearing=10.0)
    fails_b = [LinkFailure(distance_km=2.0, observed_at=NOW,
                           lat=destination_point(other_node[0], other_node[1],
                                                bearing, 2.0)[0],
                           lon=destination_point(other_node[0], other_node[1],
                                                bearing, 2.0)[1],
                           node_key="device-b", confirmed=True)
              for bearing in (200.0, 215.0)]
    rings = boundary_rings_for(reg, fails_a + fails_b, NOW)
    assert len(rings) == 2
    seen_positions = {(r["lat"], r["lon"]) for r in rings}
    assert seen_positions == {NODE, other_node}


# ---- pre-migration failures (no node_key) are excluded, never guessed ------

def test_a_pre_migration_failure_with_no_node_key_is_excluded():
    reg = NodeRegistry()
    a = _reachable_kin_record("health-dst", "device-rain")
    reg.nodes[a.dst_hash] = a
    unmatched = _fail(NODE[0], NODE[1], node_key=None)   # old-format line
    rings = boundary_rings_for(reg, [unmatched], NOW)
    assert rings == []

    # Adding a real, keyed pair alongside the orphan still produces exactly
    # one ring — the orphan neither contributes nor blocks it.
    rings2 = boundary_rings_for(reg, [unmatched] + _fail_pair("device-rain"), NOW)
    assert len(rings2) == 1


# ---- the fuzzed beacon claim never outranks the exact stored position ------

def test_a_fuzzed_beacon_position_never_wins_over_the_exact_stored_position():
    """monitor.geo.FUZZ_RADIUS_M fuzzes an announced position by up to
    800 m on purpose (monitor/location_share.py). Ranking the ring off it
    would use privacy-fuzzed data as measurement ground truth — the exact
    opposite of what the fuzz exists for."""
    reg = NodeRegistry()
    fuzzed_elsewhere = (NODE[0] + 5.0, NODE[1] + 5.0)     # miles from the truth
    a = _reachable_kin_record("health-dst", "device-rain")
    a.latest_beacon = _beacon(lat=fuzzed_elsewhere[0], lng=fuzzed_elsewhere[1])
    reg.nodes[a.dst_hash] = a
    rings = boundary_rings_for(reg, _fail_pair("device-rain"), NOW)
    assert len(rings) == 1
    assert rings[0]["lat"] == NODE[0] and rings[0]["lon"] == NODE[1]
    assert (rings[0]["lat"], rings[0]["lon"]) != fuzzed_elsewhere


def test_no_exact_position_on_file_means_no_ring_not_a_crash():
    reg = NodeRegistry()
    a = NodeRecord(dst_hash="health-dst", name="RAIN", identity_hash="device-rain",
                   lat=None, lon=None, mesh_hops=1, seen=Observation.at(NOW, "mesh"))
    reg.nodes[a.dst_hash] = a
    rings = boundary_rings_for(reg, _fail_pair("device-rain"), NOW)
    assert rings == []


# ---- confirmed-only radius (fix #5, exercised end to end here too) --------

def test_an_isolated_unconfirmed_miss_draws_nothing_but_a_confirmed_run_does():
    reg = NodeRegistry()
    a = _reachable_kin_record("health-dst", "device-rain")
    reg.nodes[a.dst_hash] = a
    unconfirmed = _fail_pair("device-rain", confirmed=False)
    rings = boundary_rings_for(reg, unconfirmed, NOW)
    assert rings == []
    confirmed = _fail_pair("device-rain", confirmed=True)
    rings2 = boundary_rings_for(reg, unconfirmed + confirmed, NOW)
    assert len(rings2) == 1
    assert rings2[0]["total_failures_used"] == 2   # only the confirmed pair


# ---- one bad node never blanks the whole layer (fix #3) --------------------

def test_one_node_with_an_invalid_centre_does_not_block_the_others():
    reg = NodeRegistry()
    bad = NodeRecord(dst_hash="bad-dst", name="BAD", identity_hash="device-bad",
                     lat=float("nan"), lon=0.0, mesh_hops=1,
                     seen=Observation.at(NOW, "mesh"))
    good = _reachable_kin_record("good-dst", "device-good", name="GOOD")
    reg.nodes[bad.dst_hash] = bad
    reg.nodes[good.dst_hash] = good
    fails = (_fail_pair("device-bad", base_bearing=10.0)
            + _fail_pair("device-good", base_bearing=200.0))
    rings = boundary_rings_for(reg, fails, NOW)
    assert len(rings) == 1
    assert rings[0]["lat"] == NODE[0] and rings[0]["lon"] == NODE[1]


def test_one_node_whose_shape_raises_does_not_block_the_others(monkeypatch):
    """A node_boundary/boundary_segments exception for one device is caught,
    logged, and skipped — every other device's ring still draws."""
    from monitor import boundary_shape

    real_node_boundary = boundary_shape.node_boundary

    def _boom(node_lat, node_lon, *a, **k):
        if node_lat == NODE[0] and node_lon == NODE[1]:
            raise RuntimeError("synthetic failure for the 'device-rain' ring")
        return real_node_boundary(node_lat, node_lon, *a, **k)

    monkeypatch.setattr(boundary_shape, "node_boundary", _boom)

    reg = NodeRegistry()
    bad = _reachable_kin_record("health-dst", "device-rain")
    other_node = (-5.0, 33.0)
    good = NodeRecord(dst_hash="good-dst", name="GOOD", identity_hash="device-good",
                      lat=other_node[0], lon=other_node[1], mesh_hops=1,
                      seen=Observation.at(NOW, "mesh"))
    reg.nodes[bad.dst_hash] = bad
    reg.nodes[good.dst_hash] = good
    fails_bad = _fail_pair("device-rain", base_bearing=10.0)
    fails_good = [LinkFailure(
        distance_km=2.0, observed_at=NOW,
        lat=destination_point(other_node[0], other_node[1], b, 2.0)[0],
        lon=destination_point(other_node[0], other_node[1], b, 2.0)[1],
        node_key="device-good", confirmed=True)
        for b in (50.0, 65.0)]
    rings = boundary_rings_for(reg, fails_bad + fails_good, NOW)
    assert len(rings) == 1
    assert rings[0]["lat"] == other_node[0] and rings[0]["lon"] == other_node[1]


# ---- own-identity / own-destination exclusion (fix #11) --------------------

def test_the_medics_own_destination_never_draws_a_ring():
    reg = NodeRegistry()
    own = _reachable_kin_record("own-dst", "device-own", name="MEDIC")
    reg.nodes[own.dst_hash] = own
    reg.set_own_destinations(["own-dst"])
    rings = boundary_rings_for(reg, _fail_pair("device-own"), NOW)
    assert rings == []


# ---- max_age_days is actually wired through (fix #6) ------------------------

def test_max_age_days_is_passed_through_and_ages_out_old_losses():
    reg = NodeRegistry()
    a = _reachable_kin_record("health-dst", "device-rain")
    reg.nodes[a.dst_hash] = a
    old = _fail_pair("device-rain", base_bearing=10.0,
                     observed_at=NOW - (BOUNDARY_MAX_AGE_DAYS + 5) * 86400)
    rings = boundary_rings_for(reg, old, NOW)
    assert rings == []       # too old under the DEFAULT max_age_days


# ---- a skip logs a line, never swallows silently (fix #14) -----------------

def test_a_skipped_ring_logs_a_line(capsys):
    reg = NodeRegistry()
    bad = NodeRecord(dst_hash="bad-dst", name="BAD", identity_hash="device-bad",
                     lat=None, lon=None, mesh_hops=1,
                     seen=Observation.at(NOW, "mesh"))
    reg.nodes[bad.dst_hash] = bad
    boundary_rings_for(reg, _fail_pair("device-bad"), NOW)
    out = capsys.readouterr().out
    assert "[boundary]" in out
    assert "bad-dst" in out or "BAD" in out


# ---- no failures at all -> no work, no crash --------------------------------

def test_no_failures_returns_empty_without_touching_the_registry():
    class _ExplodingRegistry:
        def all(self, now):
            raise AssertionError("must not be called when there are no failures")

        def consolidated_records(self, now):
            raise AssertionError("must not be called when there are no failures")

    assert boundary_rings_for(_ExplodingRegistry(), [], NOW) == []
