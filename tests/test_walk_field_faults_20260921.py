"""What three fault-hunting agents found in the boundary walk on the day of
the first real one (2026-09-21), while the operator was out walking it.
Findings merged from three lenses (code path, field conditions, device
plumbing); one design. Pure rules are tested as rules; screen wiring is
pinned by reading the source, the way tests/test_walk_live_check.py does,
because the Kivy screen cannot be built headless here."""
import json
import os
import re

import monitor.boundary_walk as bw
from monitor.boundary_walk import BoundaryWalkSession
from monitor.synapse_range import CO_LOCATED_KM

SCAN = "ui/screens/scan_screen.py"
APP = "ui/app.py"


def _body(path, name):
    src = open(path).read()
    m = re.search(r"    def " + re.escape(name) + r"\(.*?(?=\n    def |\Z)",
                  src, re.S)
    assert m, f"{name} missing from {path}"
    return m.group(0)


def _s():
    return BoundaryWalkSession(node_key="ab" * 16, node_name="RTnodet114",
                               node_lat=-37.7, node_lon=145.0, now=1000.0)


# -- evidence rules --------------------------------------------------------

def test_a_silent_ping_at_the_anchor_is_not_a_zero_km_loss():
    """The first ping fires the moment the walk starts, standing AT the node.
    One LoRa frame can die of anything; banked, that miss became "measured a
    loss at 0 km — trust the loss" in the range model. Observations already
    drop co-located samples; failures must play by the same rule."""
    w = _s()
    w.begin_ping(1000.0)
    w.ping_result(1001.0, False, gps=(-37.7, 145.0))          # at the anchor
    w.begin_ping(1020.0)
    w.ping_result(1021.0, True, gps=(-37.701, 145.0))
    w.begin_ping(1040.0)
    w.ping_result(1041.0, False, gps=(-37.705, 145.0))        # ~0.55 km out
    obs, fails = w.evidence()
    assert len(obs) == 1
    assert len(fails) == 1 and fails[0].distance_km > CO_LOCATED_KM


def test_each_loss_keeps_its_own_time():
    w = _s()
    for t, ok in ((1000.0, False), (1020.0, False)):
        w.begin_ping(t)
        w.ping_result(t + 1, ok, gps=(-37.705, 145.0))
    _obs, fails = w.evidence()
    assert [f.observed_at for f in fails] == [1001.0, 1021.0]


def test_counts_say_how_many_pings_could_not_be_placed():
    """Indoors the fix coasts, samples carry no distance, and the banner used
    to keep counting them as if they were placed. The screen paints from
    counts(); the summary names the unplaced ones."""
    w = _s()
    w.begin_ping(1000.0); w.ping_result(1001.0, True, gps=(-37.701, 145.0))
    w.begin_ping(1020.0); w.ping_result(1021.0, True, gps=None)
    w.begin_ping(1040.0); w.ping_result(1041.0, False, gps=None)
    c = w.counts()
    assert (c["hits"], c["misses"], c["unplaced"]) == (2, 1, 2)
    assert c["max_km"] == w.max_linked_km
    assert "2" in w.summary() and "satellite" in w.summary()
    assert w.last_unplaced() is True


def test_a_truncated_evidence_line_does_not_hide_the_rest(tmp_path):
    """A power cut mid-append leaves half a line. One bad line used to raise
    out of the loader; the only caller caught it and dropped EVERY walk ever
    banked, silently. Bad lines are skipped, good ones survive."""
    w = _s()
    w.begin_ping(1000.0); w.ping_result(1001.0, True, gps=(-37.701, 145.0))
    w.begin_ping(1020.0); w.ping_result(1021.0, False, gps=(-37.705, 145.0))
    obs, fails = w.evidence()
    bw.append_evidence(obs, fails, base_dir=str(tmp_path))
    with open(tmp_path / bw._OBS_FILE, "a") as fh:
        fh.write('{"heard_by": "MEDIC", "dist')          # truncated
    with open(tmp_path / bw._FAIL_FILE, "a") as fh:
        fh.write(json.dumps({"note": "missing distance"}) + "\n")
    assert len(bw.load_walk_observations(str(tmp_path))) == 1
    assert len(bw.load_walk_failures(str(tmp_path))) == 1


# -- screen and app wiring (pinned in code, not prose) ---------------------

def test_the_screensaver_never_covers_a_walk_or_its_gate():
    # Since 2026-09-22 the screensaver defers on ONE predicate shared with
    # the busy marker; the walk and its gate must be in that predicate.
    assert "self._busy_reason()" in _body(APP, "_show_screensaver")
    busy = _body(APP, "_busy_reason")
    assert "_walk" in busy and "_walk_gate" in busy


def test_auto_backpack_stands_down_while_a_walk_is_running():
    """Two live fixes 150 m out would switch the medic to Backpack and restart
    rnsd — a self-inflicted "edge of reach" three minutes into every walk."""
    body = _body(APP, "_check_movement")
    assert "_walk" in body


def test_closing_the_app_banks_a_running_walk():
    assert "end_walk" in _body(APP, "on_stop")


def test_starting_another_walk_banks_the_running_one():
    body = _body(SCAN, "begin_walk")
    assert "persist=" in body and "samples" in body


def test_a_sample_is_placed_where_the_ping_was_sent():
    """rnpath -w 15 returns early on an answer and waits the full 15 s on
    silence, so a position read when the RESULT lands stamps every loss
    ~20 m further out than the hit before it. Read the fix at send time."""
    tick, result = _body(SCAN, "_walk_tick"), _body(SCAN, "_walk_result")
    assert "walk_position(" in tick
    assert "_fix_reader" not in result and "_gps_reader" not in result


def test_a_failed_save_is_said_not_swallowed():
    body = _body(SCAN, "end_walk")
    assert "Could not save" in body
    save = body.index("append_evidence(obs, fails)")
    assert "except" in body[save:body.index("Could not save")]


def test_hint_says_when_a_ping_could_not_be_placed():
    body = _body(SCAN, "_walk_step_hint")
    assert "last_unplaced()" in body
    assert body.count("tr(") >= 5, "lost/placed, lost/unplaced, first, unplaced, keep walking"


def test_map_taps_are_inert_during_a_walk():
    body = _body(SCAN, "_on_map_pick")
    assert "_walk" in body


def test_start_button_needs_a_steady_fix():
    body = _body(SCAN, "_walk_gate_tick")
    assert "_walk_ready_ticks" in body


def test_gate1_tells_could_not_check_from_did_not_answer():
    body = _body(SCAN, "_run_walk_check")
    assert "answers_now" in body and "checked=" in body
    reach = _body(APP, "_mesh_reachable")
    assert "return None" in reach


def test_walk_probe_logs_its_timing():
    assert "[walk] probe" in _body(APP, "_walk_probe")


def test_walk_evidence_is_keyed_by_the_mesh_address():
    assert "probe_hash_for" in _body(APP, "_start_boundary_walk")
