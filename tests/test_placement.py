"""Interference log + placement suggestion engine — pure, no hardware."""

import pytest

from monitor.interference_log import (
    InterferenceLog, NOISE_LOG_THRESHOLD_DBM, NEARBY_M,
)
from monitor.placement import (
    suggest, suggest_fill_gaps, suggest_extend_reach, estimate_rssi_dbm,
    ESTIMATE_CAUTION,
)
from monitor.topology import build_topology
from monitor.registry import NodeRegistry
from monitor.health_beacon import encode, decode

NOW = 1_000_000.0
MEL = (-37.79, 144.96)


def _beacon(rssi=-70):
    return decode(encode(uptime_s=100, heap_kb=140, wifi_rssi_dbm=rssi,
                         reset_reason=0, wifi_up=True, lora_up=True,
                         tcp_backbone_up=True, local_tcp_server_up=True,
                         wdt_armed=True, psram=True, fault=False,
                         board_id=0x3F, fw=(0, 6, 2)))


# ---- interference log --------------------------------------------------------

def test_logs_only_degraded_and_located_readings():
    log = InterferenceLog()
    assert log.maybe_log(-110, *MEL, t=NOW) is None          # below threshold: clean
    assert log.maybe_log(NOISE_LOG_THRESHOLD_DBM, None, None, t=NOW) is None  # no fix
    e = log.maybe_log(-98, *MEL, t=NOW, snr_db=-3.2)
    assert e is not None and log.entries == [e]


def test_near_and_caution_within_radius():
    log = InterferenceLog()
    log.maybe_log(-98, *MEL, t=NOW)
    close = (MEL[0] + 0.001, MEL[1])                          # ~111 m north
    far = (MEL[0] + 0.01, MEL[1])                             # ~1.1 km north
    assert len(log.near(*close)) == 1
    assert log.near(*far) == []
    note = log.caution_for(*close)
    assert note is not None and "-98 dBm" in note and "Triage" in note
    assert log.caution_for(*far) is None


def test_remove_and_roundtrip():
    log = InterferenceLog()
    e = log.maybe_log(-96, *MEL, t=NOW)
    again = InterferenceLog.from_dict(log.to_dict())
    assert len(again.entries) == 1
    log.remove(e)
    assert log.entries == []


# ---- placement suggestions -----------------------------------------------------

def _registry_with_gap():
    r = NodeRegistry()
    # ~2 km apart — inside the 3 km "a relay should work" default
    r.register("aaaa", name="Northcote", lat=-37.770, lon=145.000)
    r.register("cccc", name="Coburg", lat=-37.756, lon=144.985)
    r.ingest("aaaa", _beacon(), now=NOW)                     # heard; cccc silent
    return r


def test_fill_gap_suggests_the_midpoint_with_estimates():
    topo = build_topology(_registry_with_gap(), paths=[], now=NOW)
    sugs = suggest(topo)
    assert sugs and sugs[0].kind == "fill_gap"
    s = sugs[0]
    assert "Northcote" in s.reason and "Coburg" in s.reason
    assert s.lat == pytest.approx((-37.770 - 37.756) / 2)
    names = {e["name"] for e in s.estimates}
    assert names == {"Northcote", "Coburg"}
    assert all(e["est_rssi_dbm"] < -60 for e in s.estimates)
    assert ESTIMATE_CAUTION in s.cautions                    # always honest


def test_gap_suggestion_flags_nearby_interference():
    topo = build_topology(_registry_with_gap(), paths=[], now=NOW)
    mid = suggest(topo)[0]
    log = InterferenceLog()
    log.maybe_log(-97, mid.lat + 0.0012, mid.lon, t=NOW)      # ~130 m away
    flagged = suggest(topo, interference_log=log)[0]
    assert any("Interference was logged" in c for c in flagged.cautions)


def test_extend_reach_when_no_gaps():
    r = NodeRegistry()
    r.register("aaaa", name="Solo", lat=-37.79, lon=144.96)
    r.ingest("aaaa", _beacon(), now=NOW)
    topo = build_topology(r, paths=[], now=NOW)               # one node: no gap pairs
    sugs = suggest(topo)
    assert sugs and sugs[0].kind == "extend_reach"
    s = sugs[0]
    assert "Solo" in s.reason and s.estimates[0]["km"] == pytest.approx(1.2)
    assert s.lat != -37.79 or s.lon != 144.96                 # actually moved


def test_observed_reach_measured_from_working_located_links():
    from monitor.placement import observed_reach_km
    r = NodeRegistry()
    r.register("aaaa", name="A", lat=-37.770, lon=145.000)
    r.register("bbbb", name="B", lat=-37.752, lon=145.000)   # ~2 km north of A
    # a path via A to B = a working A<->B link between two LOCATED nodes
    topo = build_topology(r, paths=[{"hash": "bbbb", "via": "aaaa", "hops": 2}],
                          now=NOW)
    reach = observed_reach_km(topo)
    assert reach == pytest.approx(2.0, abs=0.1)


def test_no_located_links_means_no_observed_reach():
    from monitor.placement import observed_reach_km
    topo = build_topology(_registry_with_gap(), paths=[], now=NOW)  # no links between located pairs
    assert observed_reach_km(topo) is None


def test_extension_step_scales_with_the_mesh_not_a_constant():
    r = NodeRegistry()
    r.register("aaaa", name="A", lat=-37.770, lon=145.000)
    r.register("bbbb", name="B", lat=-37.752, lon=145.000)   # ~2 km working link
    r.ingest("aaaa", _beacon(), now=NOW)
    r.ingest("bbbb", _beacon(), now=NOW)
    topo = build_topology(r, paths=[{"hash": "bbbb", "via": "aaaa", "hops": 2}],
                          now=NOW)
    sugs = suggest_extend_reach(topo)
    # steps a full observed reach (~2 km), not the 1.2 km newborn-mesh fallback
    assert sugs and sugs[0].estimates[0]["km"] == pytest.approx(2.0, abs=0.1)


def test_gap_qualification_widens_with_observed_reach():
    r = NodeRegistry()
    # a proven ~2 km link A-B...
    r.register("aaaa", name="A", lat=-37.770, lon=145.000)
    r.register("bbbb", name="B", lat=-37.752, lon=145.000)
    # ...and C ~3.5 km from A with no link: beyond the old 3 km constant,
    # inside the adaptive 2 x observed-reach (~4 km)
    r.register("cccc", name="C", lat=-37.7385, lon=145.000)
    r.ingest("aaaa", _beacon(), now=NOW)
    topo = build_topology(r, paths=[{"hash": "bbbb", "via": "aaaa", "hops": 2}],
                          now=NOW)
    sugs = suggest_fill_gaps(topo)
    bridged = {s.reason for s in sugs}
    assert any("C" in reason for reason in bridged)


def test_estimate_rssi_falls_with_distance_and_is_plausible():
    near, mid, far = (estimate_rssi_dbm(k) for k in (0.3, 1.2, 3.0))
    assert near > mid > far
    assert -130 < far < mid < near < -60                      # sane dBm territory
