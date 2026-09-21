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


# -- the GPS gate: no anchor, no walk (operator, 2026-09-21) ---------------
#
# The order given outdoors, with a T114: the walk must not
# begin until the medic has a satellite fix, and the start button must not
# EXIST until then. What it protects against is the failure above — a walk
# that looks busy and banks nothing, because every ping carries km=None.

from monitor.geo import GpsFix


def _live(sats=7):
    return GpsFix(lat=-33.87, lon=151.21, sats=sats, fix_quality=1)


def test_no_fix_means_no_start_button():
    g = bw.gps_gate(None, waited_s=5.0)
    assert g["ready"] is False and g["anchor"] is None
    assert g["stage"] == "searching"


def test_a_live_fix_opens_the_gate_and_names_the_anchor():
    g = bw.gps_gate(_live(sats=9), waited_s=3.0)
    assert g["ready"] is True
    assert g["anchor"] == (-33.87, 151.21)
    assert g["sats"] == 9


def test_a_coasting_fix_is_refused_however_long_we_have_waited():
    """classify_fix calls it 'held': a fix flag with zero satellites tracked
    is the receiver replaying where it WAS. Anchoring on a remembered
    position puts a wrong distance on every sample in the walk."""
    coasting = GpsFix(lat=-33.87, lon=151.21, sats=0, fix_quality=1)
    g = bw.gps_gate(coasting, waited_s=999.0)
    assert g["ready"] is False and g["anchor"] is None
    assert g["stage"] == "held", "a coasting fix must be named, not lumped in"


def test_a_long_wait_stops_saying_wait_and_starts_saying_check():
    """A receiver that has had open sky for two minutes and found nothing is
    not slow, it is a problem — and the screen must change its advice."""
    assert bw.gps_gate(None, waited_s=1.0)["stage"] == "searching"
    assert bw.gps_gate(None, waited_s=bw.GPS_COLD_START_S)["stage"] == "slow"


def test_a_fix_that_arrives_late_still_opens_the_gate():
    assert bw.gps_gate(_live(), waited_s=10_000.0)["ready"] is True


def test_the_gate_survives_a_reader_that_hands_back_rubbish():
    """The fix reader is a file read on a device; it has returned None, a
    stale dict and an exception's worth of nothing before now."""
    class _Junk:
        pass
    g = bw.gps_gate(_Junk(), waited_s=1.0)
    assert g["ready"] is False and g["anchor"] is None


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


# -- two gates, node first then sky (operator, 2026-09-21) -----------------
#
# "if the user selects a node underneath VITALS and they click boundary walk,
# it automatically pings the node to make sure it's online first. And if it's
# not online, you can say no, not available." — and, in the clarification that
# settled the order, the GPS wait must not even be shown until the node has
# answered: there is no point waiting on satellites for a node that is dead.

def test_begin_walk_checks_the_node_before_it_mentions_gps():
    from tests.srcutil import func_source
    begin = func_source("ui/screens/scan_screen.py", "begin_walk",
                        cls="ScanScreen")
    assert "_show_walk_check" in begin
    # the CODE, not the docstring that explains the sequence
    body = begin.split('"""')[-1]
    assert "_show_walk_gate" not in body, (
        "gate 2 must be reached THROUGH gate 1, never started alongside it")


def test_the_node_check_uses_the_shared_rule_and_probe():
    """One definition of 'online' for both doors — two would drift."""
    from tests.srcutil import func_source
    run = func_source("ui/screens/scan_screen.py", "_run_walk_check",
                      cls="ScanScreen")
    assert "answers_now" in run, "the rule lives in monitor.boundary_walk"
    assert "_walk_reach_probe" in run
    assert "threading" in run, (
        "the probe blocks for ~10 s and the operator is standing outside")


def test_a_silent_node_is_refused_not_walked():
    from tests.srcutil import func_source
    done = func_source("ui/screens/scan_screen.py", "_walk_check_done",
                       cls="ScanScreen")
    assert "_show_walk_unavailable" in done and "_show_walk_gate" in done
    ref = func_source("ui/screens/scan_screen.py", "_show_walk_unavailable",
                      cls="ScanScreen")
    assert "is not available" in ref, "the operator's own word for the refusal"
    # it says what to check, and it is not a dead end
    assert "powered" in ref and "Ping it again" in ref
    # and it offers no way to walk a node that did not answer
    assert "_start_walk_now" not in ref and "_show_walk_gate" not in ref


def test_an_unrunnable_check_refuses_too():
    """'I could not check' is its own answer, and it is not 'it works'."""
    from tests.srcutil import func_source
    run = func_source("ui/screens/scan_screen.py", "_run_walk_check",
                      cls="ScanScreen")
    assert "_show_walk_unavailable" in run and "callable" in run


def test_a_cancelled_check_cannot_start_a_walk_later():
    """The probe outlives the screen that asked for it by up to ten seconds."""
    from tests.srcutil import func_source
    done = func_source("ui/screens/scan_screen.py", "_walk_check_done",
                       cls="ScanScreen")
    assert "_walk_check_token" in done and "_walk_gate" in done


def test_both_doors_arrive_through_the_same_entry_with_a_probe_wired():
    from tests.srcutil import func_source, src
    start = func_source("ui/app.py", "_start_boundary_walk",
                        cls="ReticulumNodeMedicApp")
    assert "reach_probe" in start and "_mesh_reachable" in start
    app = src("ui/app.py")
    # the VITALS door and the ANTENNA door both land on _start_boundary_walk
    assert "on_walk=self._start_boundary_walk" in app
    walk_from_pick = func_source("ui/app.py", "_walk_from_pick",
                                 cls="ReticulumNodeMedicApp")
    assert "_start_boundary_walk" in walk_from_pick


def test_gate_two_says_gate_one_passed():
    """Two waits in a row read as one unless the screen says which ended."""
    from tests.srcutil import func_source
    gate = func_source("ui/screens/scan_screen.py", "_show_walk_gate",
                       cls="ScanScreen")
    assert "answered — it is on the mesh" in gate


def test_begin_walk_opens_the_gate_instead_of_pinging_immediately():
    """The whole point of the gate: begin_walk must NOT start the cadence.
    Before 2026-09-21 it scheduled _walk_tick on the spot and the anchor was
    whichever fix happened to turn up first — or none, and the walk measured
    nothing while looking busy."""
    from tests.srcutil import func_source
    begin = func_source("ui/screens/scan_screen.py", "begin_walk",
                        cls="ScanScreen")
    assert "_walk_tick" not in begin, (
        "begin_walk must hand over to the gates, not start pinging")


def test_the_gate_asks_the_pure_rule_and_reads_the_live_fix():
    from tests.srcutil import func_source
    tick = func_source("ui/screens/scan_screen.py", "_walk_gate_tick",
                       cls="ScanScreen")
    assert "gps_gate" in tick, "the decision lives in monitor.boundary_walk"
    assert "_fix_reader" in tick, "the gate must read the LIVE fix each tick"
    assert "set_sats" in tick, "the satellite count has to be on the glass"


def test_the_start_button_does_not_exist_until_the_gate_is_ready():
    from tests.srcutil import func_source
    tick = func_source("ui/screens/scan_screen.py", "_walk_gate_tick",
                       cls="ScanScreen")
    # the button is shown/hidden off the gate's own verdict, not off a timer
    assert 'g["ready"]' in tick or "g['ready']" in tick


def test_the_gate_tells_the_operator_where_to_stand():
    """A stranger holding the medic needs to know what to do with their body
    — the order's words: STAND NEXT TO THE NODE."""
    from tests.srcutil import func_source
    show = func_source("ui/screens/scan_screen.py", "_show_walk_gate",
                       cls="ScanScreen")
    assert "Stand next to" in show
    assert "GPS found" in show, "the big button says why it appeared"


def test_the_walk_anchors_where_the_operator_pressed_start():
    """Not at the registry's remembered position — that may be stale or
    fuzzed — but at the fix measured NOW, at a spot the operator confirmed
    by standing on it."""
    from tests.srcutil import func_source
    start = func_source("ui/screens/scan_screen.py", "_start_walk_now",
                        cls="ScanScreen")
    assert "node_lat=" in start and "node_lon=" in start
    assert "anchor" in start


def test_the_walking_hud_says_what_to_do_at_every_stage():
    """Before 2026-09-21 the flashing banner was the end of the conversation:
    a stranger stood in a field holding a device that said MESH CONNECTION
    LOST and nothing about what to do next."""
    from tests.srcutil import func_source, src
    scan = src("ui/screens/scan_screen.py")
    step = func_source("ui/screens/scan_screen.py", "_walk_step_hint",
                       cls="ScanScreen")
    assert "Keep walking" in step
    assert "Stop" in step, "the lost state must name the button that saves it"
    # and the hint is refreshed as results land, not written once
    res = func_source("ui/screens/scan_screen.py", "_walk_result",
                      cls="ScanScreen")
    assert "_walk_step_hint" in res


def test_the_stop_button_does_not_read_as_a_destructive_one():
    from tests.srcutil import func_source
    show = func_source("ui/screens/scan_screen.py", "_show_walk_hud",
                       cls="ScanScreen")
    assert "Stop & save" in show, (
        "red 'Stop walk' beside Delete/Rebirth read as throwing the walk away")


def test_the_gate_panels_are_sized_by_their_text_not_by_a_guess():
    """Four birth screens once pushed their animation off the glass because
    the copy outgrew the box drawn for it (2026-08-12). These panels carry
    the longest sentences in the feature, in eight languages, on a short
    portrait panel — a fixed height here clips the instructions silently."""
    from tests.srcutil import func_source
    panel = func_source("ui/screens/scan_screen.py", "_walk_panel",
                        cls="ScanScreen")
    assert "minimum_height" in panel
    txt = func_source("ui/screens/scan_screen.py", "_walk_text",
                      cls="ScanScreen")
    assert "texture_size" in txt
    for fn in ("_show_walk_check", "_show_walk_unavailable", "_show_walk_gate"):
        body = func_source("ui/screens/scan_screen.py", fn, cls="ScanScreen")
        assert "_walk_panel()" in body, f"{fn} must not pass a guessed height"


def test_the_picker_says_what_pressing_a_node_will_start():
    """The popup teleports the operator to MAPS and starts a physical task.
    A stranger should not learn that by arriving there."""
    from tests.srcutil import func_source
    pick = func_source("ui/app.py", "_pick_node_for_walk",
                       cls="ReticulumNodeMedicApp")
    assert "walk away from it on foot" in pick


def test_the_summary_says_where_the_evidence_went():
    from tests.srcutil import func_source
    end = func_source("ui/screens/scan_screen.py", "end_walk", cls="ScanScreen")
    assert "Build next" in end, (
        "'banked as range evidence' means nothing to a newcomer unless the "
        "screen names what reads it")


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
