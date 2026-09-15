"""The boundary walk — the operator's range-truth protocol (spec 2026-08-13,
built 2026-09-15): walk away from a node with the medic, ping every 20 s,
flash MESH CONNECTION LOST when the link drops, and turn every ping — hit or
miss — into range evidence at a GPS-known distance.

Pure state machine like its sibling monitor/first_link.py: injected clocks,
injected GPS, injected ping results; the MAPS screen renders it."""
import monitor.boundary_walk as bw
from monitor.boundary_walk import BoundaryWalkSession


def _s(node=(0.0, 0.0)):
    return BoundaryWalkSession(node_key="ab" * 16, node_name="RAIN",
                               node_lat=node[0], node_lon=node[1], now=1000.0)


def test_pings_are_due_every_twenty_seconds():
    s = _s()
    assert s.due(1000.0)                      # first ping fires immediately
    s.begin_ping(1000.0)
    s.ping_result(1002.0, ok=True, gps=(0.0, 0.001))
    assert not s.due(1010.0)
    assert s.due(1000.0 + bw.PING_EVERY_S)


def test_a_hit_records_distance_from_the_node():
    s = _s()
    s.begin_ping(1000.0)
    s.ping_result(1002.0, ok=True, snr_db=7.5, gps=(0.0, 0.009))  # ~1 km
    a = s.samples[-1]
    assert a["connected"] and 0.9 < a["km"] < 1.1 and a["snr_db"] == 7.5
    assert s.state == "linked"
    assert 0.9 < s.max_linked_km < 1.1


def test_one_miss_is_evidence_but_not_yet_lost():
    """One dropped packet is not a dropped mesh — the banner waits for the
    second consecutive miss; the SAMPLE records the failure regardless."""
    s = _s()
    s.begin_ping(1000.0); s.ping_result(1001.0, ok=True, gps=(0.0, 0.005))
    s.begin_ping(1020.0); s.ping_result(1021.0, ok=False, gps=(0.0, 0.010))
    assert s.samples[-1]["connected"] is False
    assert s.state == "linked"                # still hopeful
    s.begin_ping(1040.0); s.ping_result(1041.0, ok=False, gps=(0.0, 0.011))
    assert s.state == "lost"
    text, flashing = s.banner()
    assert "MESH CONNECTION LOST" in text and flashing


def test_a_hit_after_loss_regains_the_link():
    s = _s()
    for t, ok in ((1000, False), (1020, False)):
        s.begin_ping(t); s.ping_result(t + 1, ok=ok, gps=(0.0, 0.010))
    assert s.state == "lost"
    s.begin_ping(1040.0); s.ping_result(1041.0, ok=True, gps=(0.0, 0.008))
    assert s.state == "linked"
    assert not s.banner()[1]


def test_no_gps_no_distance_but_the_outcome_still_counts():
    """A ping with no fix records connected-ness with km=None — honesty about
    what was measured; evidence needs the distance, so it is excluded there."""
    s = _s()
    s.begin_ping(1000.0); s.ping_result(1001.0, ok=False, gps=None)
    assert s.samples[-1]["km"] is None


def test_evidence_splits_hits_from_misses_and_skips_unlocated():
    s = _s()
    for t, ok, gps in ((1000, True, (0.0, 0.005)), (1020, False, (0.0, 0.012)),
                       (1040, False, None)):
        s.begin_ping(t); s.ping_result(t + 1, ok=ok, gps=gps)
    obs, fails = s.evidence(medic_id="MEDIC")
    assert len(obs) == 1 and obs[0].heard_from == "ab" * 16
    assert obs[0].source == "boundary_walk" and obs[0].distance_km
    assert len(fails) == 1 and abs(fails[0].distance_km - 1.33) < 0.2
    assert fails[0].note == "boundary walk"


def test_a_node_without_coordinates_anchors_at_the_first_fix():
    """The walk starts AT the node, so the first GPS fix is the anchor when
    the registry holds no position — the spec's walk-away still measures."""
    s = BoundaryWalkSession(node_key="cd" * 16, node_name="X",
                            node_lat=None, node_lon=None, now=0.0)
    s.begin_ping(0.0); s.ping_result(1.0, ok=True, gps=(10.0, 10.0))
    assert s.samples[-1]["km"] == 0.0         # first fix IS the anchor
    s.begin_ping(20.0); s.ping_result(21.0, ok=True, gps=(10.0, 10.009))
    assert 0.9 < s.samples[-1]["km"] < 1.1


def test_persist_and_load_round_trip(tmp_path):
    s = _s()
    s.begin_ping(1000.0); s.ping_result(1001.0, ok=True, gps=(0.0, 0.005))
    s.begin_ping(1020.0); s.ping_result(1021.0, ok=False, gps=(0.0, 0.012))
    obs, fails = s.evidence(medic_id="MEDIC")
    bw.append_evidence(obs, fails, base_dir=str(tmp_path))
    assert len(bw.load_walk_failures(base_dir=str(tmp_path))) == 1
    obs2 = bw.load_walk_observations(base_dir=str(tmp_path))
    assert len(obs2) == 1 and obs2[0].snr_db == obs[0].snr_db


def test_summary_tells_the_walk_story():
    s = _s()
    for t, ok, lon in ((1000, True, 0.005), (1020, True, 0.009),
                       (1040, False, 0.013), (1060, False, 0.014)):
        s.begin_ping(t); s.ping_result(t + 1, ok=ok, gps=(0.0, lon))
    line = s.summary()
    assert "2" in line and "km" in line       # hits and a distance in the story


# -- the walk is wired to the glass and the spine (source guards) ----------

def test_the_walk_is_reachable_and_feeds_placement():
    from tests.srcutil import func_source, src
    detail = src("ui/screens/node_detail_screen.py")
    assert "Boundary walk" in detail and "_on_walk" in detail
    app = src("ui/app.py")
    assert "_start_boundary_walk" in app and "_walk_probe" in app
    assert "load_walk_failures" in app, (
        "banked losses must reach the placement spine, or the walk "
        "measures into a drawer nobody opens")
    scan = src("ui/screens/scan_screen.py")
    assert "MESH CONNECTION LOST" in src("monitor/boundary_walk.py")
    assert "begin_walk" in scan and "set_walk_trail" in scan


def test_the_walk_probe_stays_inside_the_cadence():
    """rnpath waits must fit under PING_EVERY_S or pings pile up — due()
    refuses while one is in flight, so an over-long probe would silently
    halve the sample rate."""
    from tests.srcutil import func_source
    probe = func_source("ui/app.py", "_walk_probe", cls=None) if False else \
        func_source("ui/app.py", "_walk_probe")
    import re
    waits = [int(m) for m in re.findall(r"rnpath -w (\d+)", probe)]
    assert waits and all(w < 20 for w in waits), waits
