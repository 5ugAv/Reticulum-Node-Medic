"""Autonomous outage watch — grace windows, escalate-once, auto-resolve."""

from monitor.node_watch import (
    NodeWatcher, grace_hours, DEFAULT_GRACE_H)


def dev(nid, status="alert", lsh=100.0, powered_by="battery"):
    return {"identity": nid, "status": status, "last_seen_hours": lsh,
            "powered_by": powered_by}


def test_grace_defaults_and_override():
    assert grace_hours("battery") == DEFAULT_GRACE_H == 72.0   # safe default today
    assert grace_hours("solar") == 72.0
    assert grace_hours("mains") == 24.0                        # ready for real source
    assert grace_hours(None) == 72.0
    assert grace_hours("battery", 48.0) == 48.0                # Settings override wins


def test_no_escalation_inside_grace():
    w = NodeWatcher()
    assert w.tick([dev("a", lsh=50.0)]) == []                  # 50h < 72h grace


def test_escalates_once_past_grace():
    w = NodeWatcher()
    out = w.tick([dev("a", lsh=80.0)])
    assert [d["identity"] for d in out] == ["a"]              # crossed 72h
    assert w.tick([dev("a", lsh=90.0)]) == []                 # already warned — quiet


def test_recovery_rearms_for_the_next_outage():
    w = NodeWatcher()
    w.tick([dev("a", lsh=80.0)])                              # escalated
    assert w.tick([dev("a", status="ok", lsh=0.2)]) == []     # came back -> re-arm
    assert [d["identity"] for d in w.tick([dev("a", lsh=80.0)])] == ["a"]  # warns again


def test_intermittent_node_never_escalates():
    # a solar node that keeps re-announcing (lsh stays low) is not dead
    w = NodeWatcher()
    for _ in range(5):
        assert w.tick([dev("a", status="ok", lsh=2.0)]) == []


def test_never_heard_is_not_an_outage():
    w = NodeWatcher()
    assert w.tick([dev("a", status="unknown", lsh=None)]) == []


def test_warn_status_is_not_escalated():
    w = NodeWatcher()
    assert w.tick([dev("a", status="warn", lsh=80.0)]) == []


def test_mains_escalates_sooner_when_source_known():
    w = NodeWatcher()
    # 30h silence: past the 24h mains window, but inside the 72h battery window
    assert [d["identity"] for d in w.tick([dev("m", lsh=30.0, powered_by="mains")])] == ["m"]
    assert w.tick([dev("b", lsh=30.0, powered_by="battery")]) == []


def test_override_shrinks_window_for_all():
    w = NodeWatcher(grace_override_h=24.0)
    assert [d["identity"] for d in w.tick([dev("a", lsh=30.0)])] == ["a"]


def test_is_watching_and_remaining_hours():
    w = NodeWatcher()
    red_fresh = dev("a", lsh=50.0)                            # red, still in grace
    assert w.is_watching(red_fresh) is True
    assert abs(w.watch_remaining_hours(red_fresh) - 22.0) < 0.01
    assert w.is_watching(dev("a", lsh=80.0)) is False         # past grace
    assert w.is_watching(dev("a", status="ok", lsh=1.0)) is False


def test_state_roundtrip_persists_notified():
    w = NodeWatcher()
    w.tick([dev("a", lsh=80.0)])
    w2 = NodeWatcher()
    w2.load_state(w.to_state())
    assert w2.tick([dev("a", lsh=90.0)]) == []                # restart doesn't re-warn


def test_neighbour_never_escalates_or_is_watched():
    w = NodeWatcher()
    d = dev("nb", lsh=200.0)                      # far past any grace window
    d["provenance"] = "neighbour"
    assert w.tick([d]) == []                      # can't repair -> never escalate
    assert w.is_watching(d) is False              # and no 'will warn' message


def test_kin_still_escalates_past_grace():
    w = NodeWatcher()
    d = dev("kn", lsh=200.0); d["provenance"] = "kin"
    assert [x["identity"] for x in w.tick([d])] == ["kn"]


def test_forward_clock_jump_does_not_escalate():
    """The medic has no RTC: it boots stale and leaps forward when NTP lands.
    That jump made every node look silent for days and fired instant false
    escalations (2026-08-01 bug hunt) — the tick is skipped instead."""
    from monitor.node_watch import NodeWatcher
    w = NodeWatcher()
    dead = [{"identity": "abc", "status": "alert", "last_seen_hours": 999.0,
             "powered_by": "solar", "provenance": "kin"}]
    # establish a baseline tick
    w.tick([], now=1000.0, monotonic=1000.0)
    # NTP lands: wall clock leaps 3 days, monotonic advanced 2 seconds
    assert w.tick(dead, now=1000.0 + 3 * 86400, monotonic=1002.0) == []
    # a normal tick afterwards escalates as usual
    got = w.tick(dead, now=1000.0 + 3 * 86400 + 60, monotonic=1062.0)
    assert [d["identity"] for d in got] == ["abc"]
