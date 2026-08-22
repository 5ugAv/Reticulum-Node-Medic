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
