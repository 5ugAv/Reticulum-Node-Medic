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
    # Received the fix at recv=1000; deciding at recv_now=1042 -> 42s elapsed is
    # added, because the UTC was true at receipt, not now.
    target = clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH - 5000,
                            ntp_synced=False, recv_now=1042.0)
    assert target == EPOCH + 42


def test_latency_compensation_never_negative():
    # A clock that jumped backwards (recv_now < gps_utc_recv) must not subtract.
    target = clock_decision(EPOCH, 1000.0, 9, 1, sys_now=EPOCH - 5000,
                            ntp_synced=False, recv_now=900.0)
    assert target == EPOCH


# ---- apply_clock ----------------------------------------------------------

class _Runner:
    """Records argv lists; returns a fixed (rc, out, err)."""

    def __init__(self, rc=0):
        self.calls = []
        self._rc = rc

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self._rc, "", ""


def test_apply_clock_builds_settime_utc_matching_sudoers():
    r = _Runner(rc=0)
    ok = apply_clock(EPOCH, r, ntp_synced=False, now=lambda: 555.0)
    assert ok is True
    # Offline: no NTP toggle, one set-time with the UTC stamp for EPOCH.
    assert r.calls == [
        ["sudo", "-n", "timedatectl", "set-time", "2026-08-22 01:02:03 UTC"],
    ]
    assert gps_clock.last_disciplined_at == 555.0


def test_apply_clock_toggles_ntp_off_first_when_synced():
    r = _Runner(rc=0)
    ok = apply_clock(EPOCH, r, ntp_synced=True)
    assert ok is True
    # set-time is refused while NTP is active -> disable it first, then set.
    assert r.calls == [
        ["sudo", "-n", "timedatectl", "set-ntp", "false"],
        ["sudo", "-n", "timedatectl", "set-time", "2026-08-22 01:02:03 UTC"],
    ]


def test_apply_clock_returns_false_on_runner_failure():
    r = _Runner(rc=1)
    assert apply_clock(EPOCH, r, ntp_synced=False) is False
