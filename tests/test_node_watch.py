"""Autonomous outage watch — grace windows, escalate-once, auto-resolve, and
(2026-10-04) only the silence the medic has WITNESSED: a medic powered off for
four days used to escalate every kin node on its first tick after boot
(readiness ledger #134)."""

from monitor.node_watch import (
    NodeWatcher, grace_hours, DEFAULT_GRACE_H, RESUME_GAP_S, SILENCE_RED_H)

T0 = 1_000_000.0          # wall clock at a watcher's first tick
H = 3600.0


def dev(nid, status="alert", lsh=100.0, powered_by="battery"):
    return {"identity": nid, "status": status, "last_seen_hours": lsh,
            "powered_by": powered_by}


class _Clock:
    """Wall + monotonic time the tests hand to the watcher explicitly."""

    def __init__(self, listened_h):
        self.wall, self.mono = T0 + listened_h * H, listened_h * H

    def advance(self, hours):
        self.wall += hours * H
        self.mono += hours * H


def listening(hours=200.0, **kw):
    """A watcher that has been hearing the mesh for *hours* already — long
    enough that every row's silence is silence it can vouch for."""
    w = NodeWatcher(**kw)
    w.tick([], now=T0, monotonic=0.0)             # its first tick, long ago
    return w, _Clock(hours)


def tick(w, c, devices):
    return w.tick(devices, now=c.wall, monotonic=c.mono)


def test_grace_defaults_and_override():
    assert grace_hours("battery") == DEFAULT_GRACE_H == 72.0   # safe default today
    assert grace_hours("solar") == 72.0
    assert grace_hours("mains") == 24.0                        # ready for real source
    assert grace_hours(None) == 72.0
    assert grace_hours("battery", 48.0) == 48.0                # Settings override wins


def test_no_escalation_inside_grace():
    w, c = listening()
    assert tick(w, c, [dev("a", lsh=50.0)]) == []              # 50h < 72h grace


def test_escalates_once_past_grace():
    w, c = listening()
    out = tick(w, c, [dev("a", lsh=80.0)])
    assert [d["identity"] for d in out] == ["a"]              # crossed 72h
    c.advance(1)
    assert tick(w, c, [dev("a", lsh=90.0)]) == []             # already warned — quiet


def test_recovery_rearms_for_the_next_outage():
    w, c = listening()
    tick(w, c, [dev("a", lsh=80.0)])                          # escalated
    c.advance(1)
    assert tick(w, c, [dev("a", status="ok", lsh=0.2)]) == []  # came back -> re-arm
    c.advance(1)
    assert [d["identity"] for d in tick(w, c, [dev("a", lsh=80.0)])] == ["a"]


def test_intermittent_node_never_escalates():
    # a solar node that keeps re-announcing (lsh stays low) is not dead
    w, c = listening()
    for _ in range(5):
        c.advance(1)
        assert tick(w, c, [dev("a", status="ok", lsh=2.0)]) == []


def test_never_heard_is_not_an_outage():
    w, c = listening()
    assert tick(w, c, [dev("a", status="unknown", lsh=None)]) == []


def test_warn_status_is_not_escalated():
    w, c = listening()
    assert tick(w, c, [dev("a", status="warn", lsh=80.0)]) == []


def test_mains_escalates_sooner_when_source_known():
    w, c = listening()
    # 30h silence: past the 24h mains window, but inside the 72h battery window
    assert [d["identity"] for d in tick(w, c, [dev("m", lsh=30.0, powered_by="mains")])] == ["m"]
    c.advance(1)
    assert tick(w, c, [dev("b", lsh=30.0, powered_by="battery")]) == []


def test_override_shrinks_window_for_all():
    w, c = listening(grace_override_h=24.0)
    assert [d["identity"] for d in tick(w, c, [dev("a", lsh=30.0)])] == ["a"]


def test_is_watching_and_remaining_hours():
    w, c = listening()
    red_fresh = dev("a", lsh=50.0)                            # red, still in grace
    assert w.is_watching(red_fresh, monotonic=c.mono) is True
    assert abs(w.watch_remaining_hours(red_fresh, monotonic=c.mono) - 22.0) < 0.01
    assert w.is_watching(dev("a", lsh=80.0), monotonic=c.mono) is False   # past grace
    assert w.is_watching(dev("a", status="ok", lsh=1.0), monotonic=c.mono) is False


def test_a_fault_red_node_heard_an_hour_ago_is_not_watched():
    """Red INSIDE the silence window means the node is talking and its own
    beacon reported a fault (lora_up=False, undervoltage...). There is no
    outage to time, so the detail page must not say "unreachable — the medic
    is watching it, will warn in 3 days" about a node heard an hour ago
    (readiness ledger #137)."""
    w, c = listening()
    fault_red = dev("f", status="alert", lsh=1.0)
    assert w.is_watching(fault_red, monotonic=c.mono) is False
    assert w.watch_remaining_hours(fault_red, monotonic=c.mono) is None
    # right at the silence line it is still the beacon talking, not silence
    assert w.is_watching(dev("f", lsh=SILENCE_RED_H), monotonic=c.mono) is False
    # just past it the red is silence, and the watch begins
    assert w.is_watching(dev("f", lsh=SILENCE_RED_H + 0.1), monotonic=c.mono) is True
    assert w.watch_remaining_hours(dev("f", lsh=SILENCE_RED_H + 0.1), monotonic=c.mono) > 0


def test_state_roundtrip_persists_notified():
    w, c = listening()
    tick(w, c, [dev("a", lsh=80.0)])
    w2 = NodeWatcher()
    c.advance(1 / 60)                                         # a one-minute restart
    w2.load_state(w.to_state(), now=c.wall, monotonic=c.mono)
    assert tick(w2, c, [dev("a", lsh=90.0)]) == []            # restart doesn't re-warn


def test_neighbour_never_escalates_or_is_watched():
    w, c = listening()
    d = dev("nb", lsh=200.0)                      # far past any grace window
    d["provenance"] = "neighbour"
    assert tick(w, c, [d]) == []                  # can't repair -> never escalate
    assert w.is_watching(d, monotonic=c.mono) is False   # and no 'will warn' message


def test_kin_still_escalates_past_grace():
    w, c = listening()
    d = dev("kn", lsh=200.0); d["provenance"] = "kin"
    assert [x["identity"] for x in tick(w, c, [d])] == ["kn"]


# -- only the silence the medic witnessed (#134) ----------------------------

def test_a_medic_that_just_booted_does_not_escalate_what_it_never_heard():
    """Four-day-old kin rows on a FRESH watcher: the rows say 96 h, the medic
    has listened for 0 h. Nothing escalates until the medic has itself heard
    nothing for a whole grace window."""
    w = NodeWatcher()
    c = _Clock(0.0)
    stale = [dev("a", lsh=96.0), dev("b", lsh=200.0, powered_by="mains")]
    assert tick(w, c, stale) == []                 # first tick after boot: nothing
    c.advance(23.0)
    assert tick(w, c, stale) == []                 # 23 h witnessed: still inside mains grace
    c.advance(2.0)
    assert [d["identity"] for d in tick(w, c, stale)] == ["b"]   # mains: 24 h witnessed
    c.advance(48.0)
    assert [d["identity"] for d in tick(w, c, stale)] == ["a"]   # battery: 73 h witnessed


def test_the_tap_message_counts_witnessed_silence_too():
    """'Will warn in about N days' has to agree with when the warning would
    actually fire — from the medic's listening clock, not the row's age."""
    w = NodeWatcher()
    c = _Clock(0.0)
    tick(w, c, [])                                 # boot
    c.advance(10.0)
    row = dev("a", lsh=100.0)
    assert w.is_watching(row, monotonic=c.mono) is True
    assert abs(w.watch_remaining_hours(row, monotonic=c.mono) - 62.0) < 0.01
    assert abs(w.silence_hours(row, monotonic=c.mono) - 10.0) < 0.01


def test_a_quick_restart_keeps_the_listening_clock():
    """A deploy restarts the UI in under a minute; the medic kept hearing the
    mesh (rnsd never stopped). The saved listening clock carries over."""
    w, c = listening(50.0)
    tick(w, c, [dev("a", lsh=100.0)])              # 50 h witnessed, inside grace
    state = w.to_state()
    w2 = NodeWatcher()
    c.advance(1 / 60)                              # back a minute later
    w2.load_state(state, now=c.wall, monotonic=5.0)   # a fresh process: new monotonic
    row = dev("a", lsh=100.0)
    assert abs(w2.silence_hours(row, monotonic=5.0) - 50.0) < 0.05
    c2_mono = 5.0 + 30 * H
    got = w2.tick([row], now=c.wall + 30 * H, monotonic=c2_mono)
    assert [d["identity"] for d in got] == ["a"]   # 80 h witnessed across the restart


def test_a_long_power_off_restarts_the_listening_clock():
    """Off for longer than RESUME_GAP_S = the medic heard nothing in the gap:
    the saved clock is not trusted and listening starts again at boot."""
    w, c = listening(50.0)
    tick(w, c, [dev("a", lsh=100.0)])
    state = w.to_state()
    w2 = NodeWatcher()
    later = c.wall + RESUME_GAP_S + 60
    w2.load_state(state, now=later, monotonic=5.0)
    assert w2.silence_hours(dev("a", lsh=100.0), monotonic=5.0) == 0.0
    assert w2.tick([dev("a", lsh=100.0)], now=later, monotonic=5.0) == []
    assert "a" in state["notified"] or True       # the warned set still loads
    assert w2._notified == set(state["notified"])


def test_forward_clock_jump_does_not_escalate():
    """The medic has no RTC: it boots stale and leaps forward when NTP lands.
    That jump made every node look silent for days and fired instant false
    escalations (2026-08-01 bug hunt) — the tick is skipped instead."""
    w = NodeWatcher()
    dead = [{"identity": "abc", "status": "alert", "last_seen_hours": 999.0,
             "powered_by": "solar", "provenance": "kin"}]
    # baseline, then a normal tick 100 h later (plenty of witnessed silence)
    w.tick([], now=1000.0, monotonic=1000.0)
    w.tick([], now=1000.0 + 100 * H, monotonic=1000.0 + 100 * H)
    base_w, base_m = 1000.0 + 100 * H, 1000.0 + 100 * H
    # NTP lands: wall clock leaps 3 days, monotonic advanced 2 seconds
    assert w.tick(dead, now=base_w + 3 * 86400, monotonic=base_m + 2.0) == []
    # The jump now arms a COOLDOWN (review item 4): a few ticks stay suppressed
    # while the rebased ages settle, not just the single jump tick.
    base = base_w + 3 * 86400
    for i in range(1, 4):
        assert w.tick(dead, now=base + 60 * i, monotonic=base_m + 2.0 + 60 * i) == []
    # after the cooldown, escalation resumes as usual
    got = w.tick(dead, now=base + 60 * 5, monotonic=base_m + 2.0 + 60 * 5)
    assert [d["identity"] for d in got] == ["abc"]


def test_note_clock_step_suppresses_a_few_ticks():
    """After the medic itself steps the clock (GPS discipline), escalations are
    suppressed for a few ticks while the rebased ages settle (review item 4)."""
    from monitor.node_watch import COOLDOWN_TICKS
    w = NodeWatcher()
    dead = [{"identity": "abc", "status": "alert", "last_seen_hours": 999.0,
             "powered_by": "solar", "provenance": "kin"}]
    w.tick([], now=1000.0, monotonic=1000.0)        # baseline
    t = 1000.0 + 100 * H                            # 100 h of listening since
    w.tick([], now=t, monotonic=t)
    w.note_clock_step()
    for _ in range(COOLDOWN_TICKS):
        t += 60
        assert w.tick(dead, now=t, monotonic=t) == []
    t += 60
    assert [d["identity"] for d in w.tick(dead, now=t, monotonic=t)] == ["abc"]
