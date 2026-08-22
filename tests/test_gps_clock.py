"""GPS clock discipline — the pure decision + apply logic (no hardware, no root).

The medic's Pi 5 RTC is not battery-backed and the field has no NTP, so GPS UTC is
the offline time authority. These tests pin the discipline policy and the exact
sudoers-matching argv apply_clock emits.
"""

import calendar

import pytest

from monitor import gps_clock
from monitor.gps_clock import apply_clock, clock_decision


# A known moment: 2026-08-22 01:02:03 UTC.
EPOCH = calendar.timegm((2026, 8, 22, 1, 2, 3))
assert EPOCH == 1787360523


# ---- clock_decision -------------------------------------------------------

def test_no_gps_utc_is_no_action():
    assert clock_decision(None, None, 9, 1, sys_now=0, ntp_synced=False,
                          recv_now=0) is None


def test_weak_fix_too_few_sats_refused():
    # 3 sats < min_sats(4): a weak fix can carry badly-wrong time — never used.
    assert clock_decision(EPOCH, 1000.0, 3, 1, sys_now=EPOCH + 5000,
                          ntp_synced=False, recv_now=1000.0) is None


def test_no_valid_fix_refused():
    assert clock_decision(EPOCH, 1000.0, 9, 0, sys_now=EPOCH + 5000,
                          ntp_synced=False, recv_now=1000.0) is None


def test_within_tolerance_is_no_action():
    # System clock is 3s off — inside the 10s band, never thrash.
    assert clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH + 3,
                          ntp_synced=False, recv_now=1000.0) is None


def test_drift_beyond_tolerance_offline_returns_target():
    # Offline (no NTP), clock 5000s wrong -> correct it. recv_now == gps_utc_recv
    # so no latency to add: target is exactly the satellite UTC.
    target = clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH + 5000,
                            ntp_synced=False, recv_now=1000.0)
    assert target == EPOCH


def test_ntp_synced_small_drift_defers_to_ntp():
    # NTP has it and we're only 30s apart (< 60s trust band) -> don't fight it.
    assert clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH + 30,
                          ntp_synced=True, recv_now=1000.0) is None


def test_ntp_synced_huge_drift_lets_gps_win():
    # NTP is wildly wrong (a whole day off) -> GPS overrides it.
    target = clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH + 86400,
                            ntp_synced=True, recv_now=1000.0)
    assert target == EPOCH


def test_latency_compensation_adds_elapsed_since_receipt():
    # Received the fix at recv=1000; deciding at recv_now=1010 -> 10s elapsed is
    # added, because the UTC was true at receipt, not now. (Within the freshness
    # window, so still acted upon.)
    target = clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH - 5000,
                            ntp_synced=False, recv_now=1010.0)
    assert target == EPOCH + 10


def test_stale_receipt_beyond_window_is_refused():
    # Receipt is 42s old (> GPS_UTC_MAX_AGE_S) -> refuse rather than compensate a
    # large, untrustworthy latency.
    from monitor.gps_clock import GPS_UTC_MAX_AGE_S
    assert GPS_UTC_MAX_AGE_S == 15
    assert clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH - 5000,
                          ntp_synced=False, recv_now=1042.0) is None


def test_negative_age_is_refused():
    # recv_now < gps_utc_recv: the receipt is "from the future" (clock jumped back
    # after a step) -> refuse, never subtract.
    assert clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH - 5000,
                          ntp_synced=False, recv_now=900.0) is None


def test_runaway_after_a_step_is_killed_by_freshness_gate():
    """Reproduce the feedback runaway the freshness gate exists to kill.

    gps_utc_recv was stamped while the clock was WRONG (read 1000). We step the
    clock to the satellite UTC. On the NEXT cycle the corrected clock reads ~EPOCH,
    but gps_utc_recv is still 1000 (only a fresh 0x03 frame re-stamps it) -> the
    receipt looks ancient (age ~= the whole correction) -> refused. Without the
    gate, elapsed would inflate by the correction and target would land in the
    future, stepping again and again."""
    # Cycle 1: wrong clock (sys_now == recv_now == 1000), big drift -> step.
    step = clock_decision(EPOCH, 1000.0, 9, 1, sys_now=1000.0,
                          ntp_synced=False, recv_now=1000.0)
    assert step == EPOCH
    # Cycle 2: clock now corrected to ~EPOCH, recv stamp still 1000 (stale).
    corrected_now = float(EPOCH)
    assert clock_decision(EPOCH, 1000.0, 9, 1, sys_now=corrected_now,
                          ntp_synced=False, recv_now=corrected_now) is None


# ---- apply_clock ----------------------------------------------------------

class _Runner:
    """Records argv lists; returns a fixed (rc, out, err)."""

    def __init__(self, rc=0):
        self.calls = []
        self._rc = rc

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self._rc, "", ""


def test_apply_clock_always_disables_ntp_first_then_sets_utc():
    r = _Runner(rc=0)
    ok = apply_clock(EPOCH, r, now=lambda: 555.0)
    assert ok is True
    # ALWAYS disable NTP first (set-time is refused while timesyncd is active,
    # regardless of NTPSynchronized), then one set-time with the UTC stamp. No
    # re-enable on success — GPS is the authority offline. Both forms are scoped.
    assert r.calls == [
        ["sudo", "-n", "timedatectl", "set-ntp", "false"],
        ["sudo", "-n", "timedatectl", "set-time", "2026-08-22 01:02:03 UTC"],
    ]
    assert gps_clock.last_disciplined_at == 555.0


def test_apply_clock_rolls_ntp_back_on_set_time_failure():
    r = _Runner(rc=1)                          # set-time fails
    assert apply_clock(EPOCH, r) is False
    # Rollback: don't strand the medic with NTP off AND no GPS time set.
    assert r.calls == [
        ["sudo", "-n", "timedatectl", "set-ntp", "false"],
        ["sudo", "-n", "timedatectl", "set-time", "2026-08-22 01:02:03 UTC"],
        ["sudo", "-n", "timedatectl", "set-ntp", "true"],
    ]




# ---- GpsClockDisciplinarian: fixed window + median-ring corroboration -------
# The forward-only floor was DELETED (it caused permanent lockout + didn't stop
# the ratchet). Plausibility is now a fixed window; large steps need corroboration.

from monitor import gps_clock as gc
from monitor.gps_clock import (
    GpsClockDisciplinarian, GPS_EPOCH_MIN, GPS_EPOCH_MAX, rebase_needed,
    REBASE_MIN_DELTA_S, CORROBORATION_K, CORROBORATION_RING, APPLY_FAIL_LIMIT,
    APPLY_BACKOFF_S, REASON_AWAITING, REASON_OUT_OF_WINDOW, REASON_SYNCED,
    REASON_NO_FIX, REASON_IN_SYNC,
)


def _call(d, target, sys_now, recv, sats=12, fix=3):
    """One evaluate() for a strong, fresh fix at *target* (age 0)."""
    return d.evaluate(gps_utc=target, gps_utc_recv=recv, sats=sats, fix=fix,
                      sys_now=sys_now, ntp_synced=False, recv_now=recv)


def test_no_direct_apply_path_a_single_small_step_does_not_apply():
    # The uncorroborated single-frame apply is GONE. Even a small 45 s drift takes
    # corroboration now (kills the #A ring-clear door and the #B ±59 s thrash).
    d = GpsClockDisciplinarian()
    assert _call(d, EPOCH, EPOCH - 45, recv=1.0) is None
    assert d.last_reason == REASON_AWAITING


def test_small_drift_corrects_after_corroboration():
    d = GpsClockDisciplinarian()
    assert _call(d, EPOCH + 0, EPOCH - 45, recv=1.0) is None
    assert _call(d, EPOCH + 1, EPOCH - 44, recv=2.0) is None   # offset ~45 each
    out = _call(d, EPOCH + 2, EPOCH - 43, recv=3.0)            # 3rd agree -> apply
    assert out == pytest.approx(EPOCH + 2, abs=2.0)


def test_single_glitch_frame_does_not_step_alone():
    d = GpsClockDisciplinarian()
    # a lone in-window-but-wrong far fix, then silence -> never applied
    assert _call(d, EPOCH + 10 * 86400, EPOCH, recv=1.0) is None


def test_cold_boot_corrects_despite_interleaved_small_offset_garbage():
    """#A door closed: a small-offset garbage frame must NOT clear the ring / step
    on its own, so a genuinely far-behind cold boot still corrects."""
    d = GpsClockDisciplinarian()
    OFF = 3 * 86400

    def sysn(t):
        return EPOCH + t - OFF

    assert _call(d, EPOCH + 0, sysn(0), recv=0) is None          # far-behind #1
    # a SMALL-offset garbage frame (offset ~ +30) — used to direct-apply+clear
    assert _call(d, sysn(30) + 30, sysn(30), recv=1) is None
    assert _call(d, EPOCH + 60, sysn(60), recv=2) is None        # far-behind #2
    applied = _call(d, EPOCH + 90, sysn(90), recv=3)             # far-behind #3 -> K
    assert applied == pytest.approx(EPOCH + 90, abs=2.0)


def test_urban_canyon_jittery_offsets_eventually_correct():
    """#C: a legit behind-clock whose offsets jitter a few seconds apart (poor sky
    view) must eventually discipline via the bounded-spread fallback."""
    d = GpsClockDisciplinarian()
    S = EPOCH
    out = None
    for i, off in enumerate((50, 56, 62, 68, 74)):     # spaced 6 s, spread 24 <= 30
        out = _call(d, S + off, S, recv=float(i))
        if out is not None:
            break
    assert out is not None
    assert 50 <= (out - S) <= 74                        # median of the agreeing set


def test_scattered_garbage_does_not_corroborate():
    d = GpsClockDisciplinarian()
    S = EPOCH
    for i, off in enumerate((10000, -50000, 3000, 800000, -200)):  # in-window, scattered
        assert _call(d, S + off, S, recv=float(i)) is None
    assert d.last_reason == REASON_AWAITING


def test_stale_ring_entries_expire_and_cannot_corroborate():
    d = GpsClockDisciplinarian()
    big = EPOCH + 10 * 86400
    assert _call(d, big, EPOCH, recv=0.0) is None       # vote @0
    assert _call(d, big + 1, EPOCH, recv=1.0) is None   # vote @1
    # a 3rd agreeing fix arrives >90 s later: the first two have expired, so this
    # is vote #1 again — a stale spoof offset can't linger to complete a cluster.
    assert _call(d, big, EPOCH, recv=200.0) is None
    assert len(d._ring) == 1


def test_rebase_needed_gates_on_the_seen_deadband():
    from monitor import registry
    assert REBASE_MIN_DELTA_S == registry.SEEN_CLOCK_SKEW_TOLERANCE_S
    assert rebase_needed(200.0) is True          # > 120 s -> rebase history
    assert rebase_needed(-200.0) is True
    assert rebase_needed(59.0) is False          # sub-deadband -> set clock only
    assert rebase_needed(120.0) is False


def test_alternating_small_offsets_never_trigger_a_rebase():
    """#B: even if an alternating small-offset spoof corroborates, every resulting
    step is sub-deadband, so rebase_needed is False -> no history rebase / persist
    thrash. (Proven at the gate the app uses.)"""
    d = GpsClockDisciplinarian()
    S = EPOCH
    applied = []
    for i in range(6):                            # six "+55" frames
        out = _call(d, S + 55, S, recv=float(i))
        if out is not None:
            applied.append(out - S)
    assert applied                                # it did eventually step...
    assert all(not rebase_needed(delta) for delta in applied)   # ...but never rebases


def test_status_line_reports_awaiting_out_of_window_and_synced():
    d = GpsClockDisciplinarian()
    now = float(calendar.timegm((2026, 6, 1, 0, 0, 0)))
    # awaiting: one large fix, no corroboration yet
    _call(d, EPOCH + 10 * 86400, EPOCH, recv=1.0)
    assert "Awaiting" in d.status_line(now)
    # out of window: a 2099 fix
    _call(d, float(calendar.timegm((2099, 1, 1, 0, 0, 0))), EPOCH, recv=2.0)
    assert "window" in d.status_line(now).lower()
    # synced: after a corroborated apply
    d.record_success(now)
    assert "GPS-synced" in d.status_line(now)


def test_status_line_warns_near_the_window_edge():
    d = GpsClockDisciplinarian()
    d.record_success(GPS_EPOCH_MAX - 100)         # last good, right at the edge
    line = d.status_line(GPS_EPOCH_MAX - 100)     # "now" near the cliff
    assert "window ends soon" in line.lower()


def test_no_fix_status_when_gps_utc_is_none():
    d = GpsClockDisciplinarian()
    assert d.evaluate(gps_utc=None, gps_utc_recv=None, sats=0, fix=0,
                      sys_now=EPOCH, ntp_synced=False, recv_now=1.0) is None
    assert d.last_reason == REASON_NO_FIX


def test_cold_boot_corrects_via_median_despite_interleaved_garbage():
    """A genuinely 3-days-behind cold boot corrects even when a garbage frame is
    interleaved — the good majority still forms K agreement (no single-frame
    reset). Corroboration is on the OFFSET, stable across ticks."""
    d = GpsClockDisciplinarian()
    OFF = 3 * 86400                          # 3 days behind

    def sysn(t):                             # the wrong clock, advancing in real time
        return EPOCH + t - OFF

    assert _call(d, EPOCH + 0, sysn(0), recv=0) is None          # good #1
    # a garbage frame, in-window but a different offset -> must NOT reset progress
    assert _call(d, EPOCH + 30 + 100000, sysn(30), recv=30) is None
    assert _call(d, EPOCH + 60, sysn(60), recv=60) is None       # good #2
    applied = _call(d, EPOCH + 90, sysn(90), recv=90)            # good #3 -> K
    assert applied == pytest.approx(EPOCH + 90, abs=2.0)         # corrected to true now


def test_median_of_the_agreeing_offsets_is_applied():
    d = GpsClockDisciplinarian()
    S = EPOCH
    assert _call(d, S + 100, S, recv=1) is None
    assert _call(d, S + 104, S, recv=2) is None
    out = _call(d, S + 102, S, recv=3)      # three agree within 5 s -> median 102
    assert out == pytest.approx(S + 102, abs=0.5)


def test_target_at_2099_is_refused_by_window():
    d = GpsClockDisciplinarian()
    far = float(calendar.timegm((2099, 1, 1, 0, 0, 0)))
    assert far > GPS_EPOCH_MAX
    for r in range(CORROBORATION_K + 1):     # even repeated, never applies
        assert _call(d, far, EPOCH, recv=float(r)) is None
    assert d._ring == []                     # never even entered the corroboration ring


def test_target_at_1999_is_refused_by_window():
    d = GpsClockDisciplinarian()
    old = float(calendar.timegm((1999, 1, 1, 0, 0, 0)))
    assert old < GPS_EPOCH_MIN
    assert _call(d, old, EPOCH, recv=1.0) is None


def test_no_lockout_spoof_then_real_signal_corrects():
    """The anti-lockout guarantee: a spoofed (in-window) step can be corroborated
    and applied, but once the spoof STOPS, the next real in-window fixes correct
    the clock automatically — there is no persisted floor to refuse them."""
    d = GpsClockDisciplinarian()
    SPOOF = 365 * 86400                       # +1 year, in-window
    out = None
    for t in (0, 30, 60):                     # corroborate the spoof
        out = _call(d, EPOCH + t + SPOOF, EPOCH + t, recv=float(t))
    assert out == pytest.approx(EPOCH + 60 + SPOOF, abs=2.0)     # stepped to spoof

    # Spoof stops; real signal returns. The clock now READS spoofed; real targets
    # are ~1 year behind it -> a large NEGATIVE offset, corroborated and applied.
    spoofed_clock = EPOCH + SPOOF
    out2 = None
    for tr in (200, 230, 260):
        out2 = _call(d, EPOCH + tr, spoofed_clock + tr, recv=float(tr))
    assert out2 == pytest.approx(EPOCH + 260, abs=2.0)          # corrected — NO lockout


def test_same_frame_is_not_counted_twice():
    d = GpsClockDisciplinarian()
    big = EPOCH + 10 * 86400
    assert _call(d, big, EPOCH, recv=1.0) is None
    assert _call(d, big, EPOCH, recv=1.0) is None    # same receipt -> deduped
    assert [o for (o, _r) in d._ring] == [big - EPOCH]   # only one vote recorded


def test_set_time_failure_backoff_stops_re_arming():
    d = GpsClockDisciplinarian()
    for _ in range(APPLY_FAIL_LIMIT):
        d.record_failure(now=1000.0)
    # within the backoff window, evaluate stands down entirely (no ring building)
    assert _call(d, EPOCH, EPOCH - 3600, recv=1000.0 + 10) is None
    from monitor.gps_clock import REASON_BACKOFF
    assert d.last_reason == REASON_BACKOFF
    assert d._ring == []
    # after the window, it resumes corroborating: 3 agreeing fixes -> apply
    base = 1000.0 + APPLY_BACKOFF_S + 1
    out = None
    for i in range(CORROBORATION_K):
        out = _call(d, EPOCH + i, EPOCH - 3600 + i, recv=base + i)
    assert out == pytest.approx(EPOCH + CORROBORATION_K - 1, abs=2.0)


def test_record_success_clears_backoff():
    d = GpsClockDisciplinarian()
    for _ in range(APPLY_FAIL_LIMIT):
        d.record_failure(now=1000.0)
    d.record_success()
    assert d._backoff_until == 0.0
    assert d._fail_count == 0
    assert not hasattr(d, "last_good_epoch")         # the floor is gone
