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


# ---- GpsClockDisciplinarian: sane-jump ceiling + corroboration + guards -----
# GPS is an UNTRUSTED input with clock authority; these pin the layered policy.

from monitor.gps_clock import (
    GpsClockDisciplinarian, SANE_STEP_S, CORROBORATION_K,
    CORROBORATION_TIMEOUT_S, APPLY_FAIL_LIMIT, APPLY_BACKOFF_S,
)


def _fresh(target, recv):
    """Build an evaluate() call for a strong, fresh fix landing exactly at
    *target* (age 0, sats 12, fix 3). recv is the distinct receipt time."""
    return dict(gps_utc=target, gps_utc_recv=recv, sats=12, fix=3, recv_now=recv)


def test_small_correction_applies_immediately():
    d = GpsClockDisciplinarian()
    # 1 hour off (< 2-day ceiling) -> apply on the first fix, no corroboration.
    out = d.evaluate(sys_now=EPOCH - 3600, ntp_synced=False, **_fresh(EPOCH, 1.0))
    assert out == EPOCH


def test_large_correction_needs_k_corroborating_fixes():
    d = GpsClockDisciplinarian()
    big = EPOCH + 10 * 86400              # 10 days ahead of the system clock
    sysn = EPOCH
    # first K-1 distinct agreeing fixes only ACCUMULATE, they don't apply
    for i in range(CORROBORATION_K - 1):
        assert d.evaluate(sys_now=sysn, ntp_synced=False,
                          **_fresh(big + i * 0.5, recv=100.0 + i)) is None
    # the Kth agreeing fix applies
    out = d.evaluate(sys_now=sysn, ntp_synced=False,
                     **_fresh(big, recv=100.0 + CORROBORATION_K))
    assert out == pytest.approx(big, abs=2.0)


def test_single_glitch_frame_never_steps_a_large_jump():
    d = GpsClockDisciplinarian()
    # one lone far-future fix, then fixes stop -> nothing is ever applied
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False,
                      **_fresh(EPOCH + 5 * 365 * 86400, recv=1.0)) is None


def test_disagreeing_large_fixes_restart_corroboration():
    d = GpsClockDisciplinarian()
    a = EPOCH + 10 * 86400
    b = EPOCH + 20 * 86400               # disagrees with a by 10 days
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False, **_fresh(a, 1.0)) is None
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False, **_fresh(b, 2.0)) is None
    # b only has 1 vote now; one more agreeing b is still short of K
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False, **_fresh(b, 3.0)) is None


def test_corroboration_resets_if_fixes_stop():
    d = GpsClockDisciplinarian()
    big = EPOCH + 10 * 86400
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False, **_fresh(big, 1.0)) is None
    # a fix arrives after the corroboration timeout -> pending was reset, so this
    # is vote #1 again, not #2
    late = 1.0 + CORROBORATION_TIMEOUT_S + 5
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False, **_fresh(big, late)) is None
    assert d._pending_count == 1


def test_backward_jump_is_refused_once_we_have_good_time():
    d = GpsClockDisciplinarian(last_good_epoch=EPOCH)
    # a fix a full hour BEHIND known-good time is spoof/fault -> refused, even
    # though it is a small, fresh, strong fix
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False,
                      **_fresh(EPOCH - 3600, recv=1.0)) is None


def test_same_frame_is_not_counted_twice():
    d = GpsClockDisciplinarian()
    big = EPOCH + 10 * 86400
    # same receipt time (same 0x03 frame) read twice must not self-corroborate
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False, **_fresh(big, 1.0)) is None
    assert d.evaluate(sys_now=EPOCH, ntp_synced=False, **_fresh(big, 1.0)) is None
    assert d._pending_count == 1


def test_set_time_failure_backoff_stops_re_arming():
    d = GpsClockDisciplinarian()
    # simulate APPLY_FAIL_LIMIT consecutive failures
    for _ in range(APPLY_FAIL_LIMIT):
        d.record_failure(now=1000.0)
    # within the backoff window, evaluate stands down even for a good small fix
    assert d.evaluate(sys_now=EPOCH - 3600, ntp_synced=False,
                      **_fresh(EPOCH, recv=1000.0 + 10)) is None
    # after the window, it acts again
    out = d.evaluate(sys_now=EPOCH - 3600, ntp_synced=False,
                     **_fresh(EPOCH, recv=1000.0 + APPLY_BACKOFF_S + 1))
    assert out == EPOCH


def test_record_success_clears_backoff_and_sets_floor():
    d = GpsClockDisciplinarian()
    for _ in range(APPLY_FAIL_LIMIT):
        d.record_failure(now=1000.0)
    d.record_success(EPOCH)
    assert d.last_good_epoch == EPOCH
    assert d._backoff_until == 0.0
