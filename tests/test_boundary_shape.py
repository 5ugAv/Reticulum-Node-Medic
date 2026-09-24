"""monitor/boundary_shape.py — the boundary walk's ring, geometry only.

Operator, 2026-09-24: "a thin line around a node that dictates its
boundary edge on the map" from the boundary walk's LOSSES — never hits
(monitor.synapse_links.LinkObservation carries no position, only
monitor.synapse_range.LinkFailure does). Colour is the SCAN screen's job
(the node's live status colour); this file is pure geometry + arithmetic,
no Kivy, no theme.

Covers: bearing_deg / destination_point round-trip, node_boundary's sector
bucketing (single/multi-sector, nearer-wins, NaN/None skip, empty input,
max_age_days filtering) and boundary_segments' partial-vs-closed shape —
the exact case the operator described (three widely-spaced directions
draw three short arcs, never a shape closed by a guess) plus the full-ring
closed-loop case.
"""

import math

import pytest

from monitor.boundary_shape import (DEFAULT_SECTORS, bearing_deg,
                                    boundary_segments, destination_point,
                                    node_boundary)
from monitor.synapse_range import LinkFailure

NODE = (10.0, -20.0)          # somewhere not on the equator/prime meridian
NOW = 2_000_000_000.0


def _fail(bearing, distance_km, observed_at=NOW, node_key=None,
         wrong_distance_km=999.0):
    """A LinkFailure sitting exactly *distance_km* out from NODE on
    *bearing*, built via destination_point — so tests exercise the same
    geometry the module ships, not a hand-typed lat/lon.

    ``distance_km`` on the record is deliberately WRONG
    (*wrong_distance_km*): node_boundary must re-derive distance from the
    positions, never trust the stored field (see its docstring) — a test
    that fed the true distance in both places could not catch a regression
    to "just read f.distance_km"."""
    lat, lon = destination_point(NODE[0], NODE[1], bearing, distance_km)
    return LinkFailure(distance_km=wrong_distance_km, observed_at=observed_at,
                       lat=lat, lon=lon, node_key=node_key)


# ---- bearing_deg / destination_point -----------------------------------------

def test_bearing_deg_cardinal_directions():
    # Small steps near the equator so degrees-of-arc read as compass-true.
    assert bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=0.5)
    assert bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=0.5)
    assert bearing_deg(0.0, 0.0, -1.0, 0.0) == pytest.approx(180.0, abs=0.5)
    assert bearing_deg(0.0, 0.0, 0.0, -1.0) == pytest.approx(270.0, abs=0.5)


def test_bearing_deg_stays_in_0_360():
    for lat2, lon2 in ((5, 5), (-5, 5), (-5, -5), (5, -5)):
        b = bearing_deg(0.0, 0.0, lat2, lon2)
        assert 0.0 <= b < 360.0


@pytest.mark.parametrize("bearing", [0.0, 37.0, 90.0, 145.0, 180.0, 233.0,
                                     270.0, 315.0, 359.0])
def test_destination_point_round_trips_bearing(bearing):
    """Go out 1 km at bearing B, then the bearing BACK to the origin is
    B+180 (mod 360), within float tolerance — the operator's own check."""
    lat, lon = destination_point(NODE[0], NODE[1], bearing, 1.0)
    back = bearing_deg(lat, lon, NODE[0], NODE[1])
    expected = (bearing + 180.0) % 360.0
    diff = min(abs(back - expected), 360.0 - abs(back - expected))
    assert diff < 0.1


def test_destination_point_lands_at_the_right_distance():
    from monitor.movement import haversine_m
    lat, lon = destination_point(NODE[0], NODE[1], 42.0, 3.7)
    km = haversine_m(NODE[0], NODE[1], lat, lon) / 1000.0
    assert km == pytest.approx(3.7, abs=0.01)


# ---- node_boundary --------------------------------------------------------

def test_single_sector_only_that_sector_gets_a_radius():
    shape = node_boundary(NODE[0], NODE[1], [_fail(5.0, 2.0)])
    sectors = shape["sectors"]
    assert len(sectors) == DEFAULT_SECTORS
    filled = [s for s in sectors if s["radius_km"] is not None]
    assert len(filled) == 1
    assert filled[0]["sample_count"] == 1
    assert filled[0]["radius_km"] == pytest.approx(2.0, abs=0.01)
    assert filled[0]["bearing_from"] == 0.0 and filled[0]["bearing_to"] == 15.0
    assert shape["total_failures_used"] == 1
    assert shape["coverage_frac"] == pytest.approx(1.0 / DEFAULT_SECTORS)


def test_distance_is_recomputed_never_trusted_from_the_record():
    """_fail() stamps a deliberately wrong distance_km on the record — this
    proves node_boundary ignores it and measures from the positions."""
    shape = node_boundary(NODE[0], NODE[1], [_fail(200.0, 4.5)])
    filled = [s for s in shape["sectors"] if s["radius_km"] is not None]
    assert filled[0]["radius_km"] == pytest.approx(4.5, abs=0.01)


def test_multi_sector_each_direction_lands_in_its_own_sector():
    fails = [_fail(10.0, 1.0), _fail(130.0, 3.0), _fail(250.0, 5.0)]
    shape = node_boundary(NODE[0], NODE[1], fails)
    by_bearing = {round(s["bearing_from"]): s for s in shape["sectors"]
                  if s["radius_km"] is not None}
    assert set(by_bearing) == {0, 120, 240}       # sector starts for 10/130/250
    assert by_bearing[0]["radius_km"] == pytest.approx(1.0, abs=0.01)
    assert by_bearing[120]["radius_km"] == pytest.approx(3.0, abs=0.01)
    assert by_bearing[240]["radius_km"] == pytest.approx(5.0, abs=0.01)
    assert shape["total_failures_used"] == 3
    assert shape["coverage_frac"] == pytest.approx(3.0 / DEFAULT_SECTORS)


def test_nearer_failure_wins_within_one_sector():
    fails = [_fail(7.0, 5.0), _fail(9.0, 2.0)]     # both in sector 0 (0-15deg)
    shape = node_boundary(NODE[0], NODE[1], fails)
    sector0 = shape["sectors"][0]
    assert sector0["sample_count"] == 2
    assert sector0["radius_km"] == pytest.approx(2.0, abs=0.01)


def test_invalid_positions_are_skipped_not_crashed():
    bad = [
        LinkFailure(distance_km=3.0, observed_at=NOW, lat=None, lon=None),
        LinkFailure(distance_km=3.0, observed_at=NOW, lat=float("nan"), lon=0.0),
        LinkFailure(distance_km=3.0, observed_at=NOW, lat=0.0, lon=0.0),  # null island
        LinkFailure(distance_km=3.0, observed_at=NOW, lat=999.0, lon=0.0),  # out of range
    ]
    good = _fail(45.0, 1.5)
    shape = node_boundary(NODE[0], NODE[1], bad + [good])
    assert shape["total_failures_used"] == 1
    filled = [s for s in shape["sectors"] if s["radius_km"] is not None]
    assert len(filled) == 1 and filled[0]["radius_km"] == pytest.approx(1.5, abs=0.01)


def test_empty_failures_list_leaves_every_sector_none():
    shape = node_boundary(NODE[0], NODE[1], [])
    assert all(s["radius_km"] is None for s in shape["sectors"])
    assert shape["coverage_frac"] == 0.0
    assert shape["total_failures_used"] == 0


def test_none_in_the_failures_iterable_is_skipped():
    shape = node_boundary(NODE[0], NODE[1], [None, _fail(0.0, 1.0)])
    assert shape["total_failures_used"] == 1


def test_max_age_days_drops_old_failures_when_supplied():
    old = _fail(0.0, 1.0, observed_at=NOW - 2 * 86400)
    fresh = _fail(200.0, 6.0, observed_at=NOW - 3600)
    shape = node_boundary(NODE[0], NODE[1], [old, fresh],
                          max_age_days=1.0, now=NOW)
    assert shape["total_failures_used"] == 1
    filled = [s for s in shape["sectors"] if s["radius_km"] is not None]
    assert len(filled) == 1 and filled[0]["radius_km"] == pytest.approx(6.0, abs=0.01)


def test_max_age_days_defaults_to_no_filtering():
    """Default is None/None — an old failure counts unless the caller asks
    otherwise. Nobody gets to invent a cutoff the operator did not set."""
    old = _fail(0.0, 1.0, observed_at=NOW - 3650 * 86400)   # ten years old
    shape = node_boundary(NODE[0], NODE[1], [old])
    assert shape["total_failures_used"] == 1


def test_custom_sector_count():
    shape = node_boundary(NODE[0], NODE[1], [_fail(100.0, 2.0)], n_sectors=4)
    assert len(shape["sectors"]) == 4
    filled = [s for s in shape["sectors"] if s["radius_km"] is not None]
    assert len(filled) == 1
    assert filled[0]["bearing_from"] == 90.0 and filled[0]["bearing_to"] == 180.0


# ---- boundary_segments -----------------------------------------------------

def _walked_bearings(*pairs):
    """Failures at each (bearing, distance) pair, via _fail — used to build
    a real node_boundary shape for the segments tests below."""
    return [_fail(b, d) for b, d in pairs]


def test_three_widely_spaced_directions_draw_only_local_arcs_never_closed():
    """The operator's own example: losses at ~10, ~130, ~250 degrees. Two
    walked bearings straddling a sector line near each direction so that
    direction gets TWO adjacent filled sectors (one drawable arc); the
    three directions stay far enough apart that nothing connects them."""
    fails = _walked_bearings(
        (8.0, 1.0), (16.0, 1.1),          # sectors 0 & 1  (~10 deg)
        (128.0, 2.0), (136.0, 2.1),       # sectors 8 & 9  (~130 deg)
        (248.0, 3.0), (256.0, 3.1))       # sectors 16 & 17 (~250 deg)
    shape = node_boundary(NODE[0], NODE[1], fails)
    segs = boundary_segments(shape, NODE[0], NODE[1])
    # One arc per cluster, not a closed ring.
    assert len(segs) == 3
    assert len(segs) < DEFAULT_SECTORS
    # Every segment sits near one of the three walked directions — nothing
    # bridges the empty space between clusters (e.g. nothing near 190 deg).
    from monitor.boundary_shape import bearing_deg as _bd
    for lat1, lon1, lat2, lon2 in segs:
        b1 = _bd(NODE[0], NODE[1], lat1, lon1)
        b2 = _bd(NODE[0], NODE[1], lat2, lon2)
        assert any(abs(((b - c + 180) % 360) - 180) < 20
                  for b in (b1, b2) for c in (10.0, 130.0, 250.0))


def test_an_isolated_single_sector_draws_no_segment():
    """One loss with no neighbouring sector filled connects to nothing —
    the ring cannot draw a line to a gap."""
    shape = node_boundary(NODE[0], NODE[1], [_fail(200.0, 4.0)])
    segs = boundary_segments(shape, NODE[0], NODE[1])
    assert segs == []


def test_full_ring_closes_into_n_sectors_segments():
    fails = [_fail(i * (360.0 / DEFAULT_SECTORS) + 1.0, 5.0)
            for i in range(DEFAULT_SECTORS)]
    shape = node_boundary(NODE[0], NODE[1], fails)
    assert shape["coverage_frac"] == 1.0
    segs = boundary_segments(shape, NODE[0], NODE[1])
    assert len(segs) == DEFAULT_SECTORS
    # Chained: each segment's endpoint is the next segment's start point —
    # a genuinely CLOSED loop, not just N disconnected lines.
    for i in range(DEFAULT_SECTORS):
        end = segs[i][2:4]
        nxt_start = segs[(i + 1) % DEFAULT_SECTORS][0:2]
        assert end == pytest.approx(nxt_start, abs=1e-9)


def test_boundary_segments_on_a_shape_with_fewer_than_two_sectors():
    assert boundary_segments({"sectors": []}, NODE[0], NODE[1]) == []
    assert boundary_segments({"sectors": [{"bearing_from": 0, "bearing_to": 360,
                                          "radius_km": 1.0, "sample_count": 1}]},
                             NODE[0], NODE[1]) == []
