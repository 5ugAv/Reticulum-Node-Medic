"""Node-reported neighbours become node-to-node lines (2026-09-29).

Every edge the medic's own path table yields starts at the medic; these are
the links the medic never witnessed, so the matching is strict and the tests
are mostly about what is REFUSED.
"""
import time

from monitor import health_beacon as hb
from monitor import neighbours as N
from monitor.topology import TopoEdge


class _Obs:
    def __init__(self, at): self.observed_at = at


class _Rec:
    def __init__(self, dst, neighbours=None, seen_at=None):
        self.dst_hash = dst
        self.latest_beacon = type("B", (), {"neighbours": neighbours or []})()
        self.seen = _Obs(seen_at) if seen_at is not None else None


A = "5a11001100000000000000000000000b"
B = "5a120012000000000000000000000012"
C = "f7b0aaaa000000000000000000000000"       # shares B's 16-bit prefix
SELF = "5a0a000a00000000000000000000000e"


def test_a_short_hash_resolves_to_exactly_one_known_node():
    assert N.resolve_short_hash(0xf7b0, [A, B]) == B
    assert N.resolve_short_hash(0xc627, [A, B]) == A


def test_an_unknown_short_hash_draws_nothing():
    """The node heard somebody the medic has never met: no line, no guess."""
    assert N.resolve_short_hash(0x1234, [A, B]) is None


def test_an_ambiguous_short_hash_draws_nothing():
    """Two bytes WILL collide on a big enough mesh. Two known nodes sharing a
    prefix: refuse, rather than draw a line to the wrong one."""
    assert N.resolve_short_hash(0xf7b0, [A, B, C]) is None


def test_a_node_is_never_its_own_neighbour():
    assert N.resolve_short_hash(0xc627, [A, B], exclude=(A,)) is None


def test_a_fresh_report_becomes_a_reported_edge():
    now = time.time()
    recs = {A: _Rec(A, [{"short_hash": 0xf7b0, "snr_db": 7, "age_s": 60}], seen_at=now - 30),
            B: _Rec(B)}
    edges = N.reported_edges(recs, now)
    assert len(edges) == 1
    e = edges[0]
    assert {e.a, e.b} == {A, B} and e.kind == "reported" and e.transport == "lora"
    assert e.snr == 7 and e.rssi is None, "SNR is the node's figure; it is not an RSSI"


def test_freshness_is_the_nodes_claim_plus_our_clock():
    """'No older than 900 s' heard 3 hours ago is a 3h15m-old link: not drawn."""
    now = time.time()
    recs = {A: _Rec(A, [{"short_hash": 0xf7b0, "snr_db": 7, "age_s": 900}], seen_at=now - 3 * 3600),
            B: _Rec(B)}
    assert N.reported_edges(recs, now) == []


def test_the_stale_bucket_is_never_drawn():
    now = time.time()
    recs = {A: _Rec(A, [{"short_hash": 0xf7b0, "snr_db": 7, "age_s": None}], seen_at=now),
            B: _Rec(B)}
    assert N.reported_edges(recs, now) == []


def test_a_report_with_no_time_is_no_report():
    now = time.time()
    recs = {A: _Rec(A, [{"short_hash": 0xf7b0, "snr_db": 7, "age_s": 60}], seen_at=None),
            B: _Rec(B)}
    assert N.reported_edges(recs, now) == []


def test_both_directions_collapse_to_one_link_keeping_the_stronger_snr():
    now = time.time()
    recs = {A: _Rec(A, [{"short_hash": 0xf7b0, "snr_db": 3, "age_s": 60}], seen_at=now),
            B: _Rec(B, [{"short_hash": 0xc627, "snr_db": 9, "age_s": 60}], seen_at=now)}
    edges = N.reported_edges(recs, now)
    assert len(edges) == 1 and edges[0].snr == 9


def test_a_v4_beacon_survives_the_registry_round_trip():
    """The whole chain depends on this: a node's report must still be there
    after the registry re-encodes the beacon to save it. Three bugs hid here
    on the day it was written — the v2 early return, the V3 header cap, and
    to_bytes() not passing the tail — each one fatal to the feature."""
    kw = dict(uptime_s=1, heap_kb=1, wifi_rssi_dbm=-60, reset_reason=1, wifi_up=True,
              lora_up=True, tcp_backbone_up=False, local_tcp_server_up=False,
              wdt_armed=True, psram=True, fault=False, board_id=9, airtime_lock=False,
              fw=(1, 2, 3))
    nb = [(bytes.fromhex(A), 9, 45), (bytes.fromhex(B), -2, 800)]
    for extra in ({}, {"lat": -37.7, "lng": 145.0}):
        raw = hb.encode(format_version=hb.FORMAT_VERSION_V4, neighbours=nb, **extra, **kw)
        assert raw[0] == hb.FORMAT_VERSION_V4, "header must say v4"
        b = hb.decode(raw)
        assert len(b.neighbours) == 2
        b2 = hb.decode(b.to_bytes())
        assert b2.neighbours == b.neighbours, "lost on the round-trip"


def test_build_topology_adds_reported_edges_between_nodes():
    """The medic never heard A hear B; A said so. Folded like every other edge,
    and never between two aspects of one device."""
    from monitor.topology import build_topology
    now = time.time()

    class Reg:
        def __init__(self):
            self.nodes = {A: _Rec(A, [{"short_hash": 0xf7b0, "snr_db": 5, "age_s": 60}], seen_at=now),
                          B: _Rec(B)}
            for r in self.nodes.values():
                r.name, r.lat, r.lon, r.node_type = "n", None, None, "pi"
                r.status = lambda _now: "ok"          # the real record's status(now)
                r.signal_dbm = lambda: None
                r.device_id = None
                r.mesh_hops = None
                r.mesh_rssi = None
        def all(self, _now): return list(self.nodes.values())
    topo = build_topology(Reg(), [], now)
    reported = [e for e in topo.edges if e.kind == "reported"]
    assert len(reported) == 1 and {reported[0].a, reported[0].b} == {A, B}
