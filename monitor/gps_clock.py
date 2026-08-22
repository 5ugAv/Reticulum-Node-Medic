"""GPS clock discipline — set the medic's clock from satellite UTC when offline.

WHY THIS EXISTS: the Pi 5's RTC is NOT battery-backed, so a power-cycled medic
boots with a wrong clock, and in the field there is no internet for NTP. The
Heltec Tracker's GNSS receiver (a UC6580) carries satellite UTC, which the
firmware pushes over the KISS link as a GPS_CMD_UTC (0x03) sub-frame; the serial
splitter decodes it to ``gps_utc`` / ``gps_utc_recv`` in its state. This module
is the PURE decision + apply logic that turns that into a disciplined clock.

GPS is the OFFLINE AUTHORITY: when there is no NTP, the satellites are the only
trustworthy time source, so a good fix wins. When NTP *is* synced we defer to it
for small differences (don't fight over seconds) but still let GPS correct a
wildly-wrong NTP. Everything here is injectable so it is unit-tested without a
clock, a radio, or root.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

#: Epoch of the last successful GPS clock set, for a caller to surface
#: "clock: GPS-synced Xm ago". Module-level so a stateless caller can read it;
#: apply_clock() stamps it. Not UI — just the fact.
last_disciplined_at: Optional[float] = None


def clock_decision(gps_utc: Optional[int],
                   gps_utc_recv: Optional[float],
                   sats: Optional[int],
                   fix: Optional[int],
                   sys_now: float,
                   ntp_synced: bool,
                   recv_now: float,
                   *,
                   tolerance_s: float = 10.0,
                   min_sats: int = 4) -> Optional[float]:
    """Return the epoch the system clock SHOULD be set to, or None for no-action.

    Rules, in order (each a reason to do nothing):
      * gps_utc is None            -> no trustworthy satellite time at all.
      * fix < 1                    -> receiver has no valid fix.
      * sats < min_sats            -> weak fix. A 1- or 2-satellite fix can carry
                                      badly-wrong time; we NEVER discipline off it.

    LATENCY COMPENSATION: the UTC value was true at the moment we RECEIVED it
    (gps_utc_recv), not now. Between receipt and this decision some real time has
    elapsed, so the true current UTC is ``gps_utc + (recv_now - gps_utc_recv)``.
    We clamp the elapsed term at >= 0 so a clock that jumped backwards can't
    subtract time from the target.

      * ntp_synced AND |target - sys_now| <= NTP-trust band (60s) -> defer to NTP.
        When NTP already has the time, we don't fight it over a few seconds. But a
        HUGE disagreement means NTP is wildly wrong (or spoofed) and GPS still
        wins — so the band is generous, not infinite.
      * |target - sys_now| <= tolerance_s -> close enough; never thrash the clock.

    Otherwise return ``target`` (the caller sets the clock to it).
    """
    # No trustworthy time, or a fix too weak to trust the time it carries.
    if gps_utc is None or gps_utc_recv is None:
        return None
    if fix is None or fix < 1:
        return None
    if sats is None or sats < min_sats:
        return None

    # The fix was true at receipt; add the real time elapsed since (never negative).
    elapsed = max(0.0, recv_now - gps_utc_recv)
    target = gps_utc + elapsed

    # When NTP already has it, defer for small drifts — but let GPS override a
    # wildly-wrong NTP (the band is wide, so only a gross error slips through).
    NTP_TRUST_BAND_S = 60.0
    if ntp_synced and abs(target - sys_now) <= NTP_TRUST_BAND_S:
        return None

    # Close enough already — never thrash a clock that is basically right.
    if abs(target - sys_now) <= tolerance_s:
        return None

    return target


def _fmt_utc(epoch: float) -> str:
    """Format an epoch as ``YYYY-MM-DD HH:MM:SS`` UTC wall-clock for timedatectl."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


Runner = Callable[[List[str]], Tuple[int, str, str]]


def apply_clock(target_epoch: float,
                run: Runner,
                ntp_synced: bool,
                now: Callable[[], float] = time.time) -> bool:
    """Set the system clock to *target_epoch* (UTC) via the ALREADY-SCOPED sudoers
    commands. ``run`` is an injected ``callable(list[str]) -> (rc, out, err)`` so
    this is testable without root.

    ``timedatectl set-time`` REFUSES to run while NTP is active, so when NTP is
    synced we first disable it. We do NOT re-enable NTP afterwards: offline, GPS
    is the primary time source. (Re-enabling NTP when the medic comes back online
    is a SEPARATE concern for an online-detector, not this discipline loop.)

    The emitted argv MUST match provisioning/sudoers.d/nodemedic (NM_CLOCK):
        /usr/bin/timedatectl set-ntp false
        /usr/bin/timedatectl set-time *
    or sudo would prompt for a password and fail. The medic runs UTC, so the
    stamp is formatted as UTC and passed with a trailing "UTC".

    Returns True only if the set-time command succeeded.
    """
    global last_disciplined_at
    if ntp_synced:
        # Manual set is refused while NTP owns the clock — turn it off first.
        run(["sudo", "-n", "timedatectl", "set-ntp", "false"])
    stamp = _fmt_utc(target_epoch) + " UTC"
    rc, _out, _err = run(["sudo", "-n", "timedatectl", "set-time", stamp])
    if rc == 0:
        last_disciplined_at = now()
        return True
    return False
