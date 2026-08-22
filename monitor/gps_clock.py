"""GPS clock discipline — set the medic's clock from satellite UTC when offline.

WHY THIS EXISTS: the Pi 5's RTC is NOT battery-backed, so a power-cycled medic
boots with a wrong clock, and in the field there is no internet for NTP. The
Heltec Tracker's GNSS receiver (a UC6580) carries satellite UTC, which the
firmware pushes over the KISS link as a GPS_CMD_UTC (0x03) sub-frame; the serial
splitter decodes it to ``gps_utc`` / ``gps_utc_recv`` in its state. This module
is the PURE decision + apply logic that turns that into a disciplined clock.

SECURITY POSTURE: GPS is an UNTRUSTED input with authority over the system clock.
An earlier design used a persisted forward-only floor (last_good_epoch); it was
REMOVED because it created two critical failures — a PERMANENT LOCKOUT (one bad
corroborated jump to 2099 set the floor to 2099, and every real 2026 fix then
read "backward" and was refused forever, with GPS the only offline source) and it
did not stop a slow forward RATCHET. The floor is replaced by two bounds that
cannot lock the tool out and cannot be ratcheted:

  1. A FIXED PLAUSIBILITY WINDOW anchored on the software era ([GPS_EPOCH_MIN,
     GPS_EPOCH_MAX]) — real time is ALWAYS in-window, so there is no lockout; an
     out-of-window target (2099, or a garbage 1999) is refused, capping any walk
     at GPS_EPOCH_MAX; and the moment a spoof stops, the next real in-window fix
     corrects the clock automatically (no persisted state to escape).
  2. MEDIAN-RING CORROBORATION for any step beyond a small drift — a ring of the
     last few DISTINCT fixes must show K agreeing before a step applies, and the
     MEDIAN of the agreeing set is used. A single interleaved garbage frame no
     longer resets progress (the good majority still forms), so a genuinely
     far-behind cold boot still corrects.

HONEST LIMIT: a fixed window cannot stop a spoofer who is PRESENT and feeding
IN-WINDOW times — that is inherent to a consumer GNSS receiver. What it
guarantees is that damage is BOUNDED (never past the window) and RECOVERY IS
AUTOMATIC the instant the real signal returns. That is the honest posture.

GPS is the OFFLINE AUTHORITY: with no NTP, the satellites are the only
trustworthy time source, so a good fix wins. When NTP *is* synced we defer to it
for small differences but still let GPS correct a wildly-wrong NTP. Everything
here is injectable so it is unit-tested without a clock, a radio, or root.
"""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from statistics import median
from typing import Callable, List, Optional, Tuple

#: Epoch of the last successful GPS clock set, for a caller to surface
#: "clock: GPS-synced Xm ago". Module-level so a stateless caller can read it;
#: apply_clock() stamps it. Not UI — just the fact.
last_disciplined_at: Optional[float] = None

#: Freshness window for the satellite-UTC receipt (seconds). The receipt time
#: (gps_utc_recv) is a WALL-CLOCK stamp taken in the *splitter* process; the app
#: reads it across a process boundary, so time.monotonic() can't bridge them.
#: We instead REFUSE to act unless the receipt is recent, which:
#:   (a) bounds the uncompensated error to this window; and
#:   (b) KILLS the feedback runaway: after we step the clock, gps_utc_recv (taken
#:       on the OLD clock) reads stale against the new clock -> refused -> no more
#:       action until a fresh 0x03 frame re-stamps gps_utc_recv on the corrected
#:       clock, at which point the drift is < tolerance and we no-op.
GPS_UTC_MAX_AGE_S = 15.0

#: FIXED plausibility window, anchored on the software era — NOT a moving floor.
#: Any GPS target outside [MIN, MAX] is refused. MUST be bumped occasionally per
#: release (a fixed window that outlives the software is the only failure mode,
#: and it fails SAFE — GPS discipline simply stops, it never locks the tool to a
#: wrong time). A generous ~15-year span so it needs bumping rarely. A fixed
#: window beats a ratcheting floor: it can never be walked forward past MAX and
#: it can never refuse a real time (real time is always inside it).
GPS_EPOCH_MIN = float(calendar.timegm((2025, 1, 1, 0, 0, 0)))
GPS_EPOCH_MAX = float(calendar.timegm((2040, 1, 1, 0, 0, 0)))

#: Drifts at or under this apply DIRECTLY (normal clock drift; not worth the
#: airtime of corroboration and not a ratchet risk — bounded by the window). Any
#: LARGER step must be corroborated. This removes the old uncorroborated
#: small-jump path (2-day steps) that let a spoofer ratchet the clock.
DIRECT_APPLY_S = 60.0

#: Median-ring corroboration. Keep the last CORROBORATION_RING distinct fixes;
#: apply a large step only when >= CORROBORATION_K of them agree within
#: CORROBORATION_AGREE_S, and apply the MEDIAN of the agreeing set. A single
#: interleaved garbage frame cannot reset progress — the good majority forms
#: anyway — so a genuinely far-behind cold boot still reaches K.
CORROBORATION_RING = 5
CORROBORATION_K = 3
CORROBORATION_AGREE_S = 5.0

#: set-time failure backoff. If set-time keeps failing, don't re-arm the
#: set-ntp-false -> set-time -> set-ntp-true dance every 30 s tick (log churn and
#: NTP flap). After APPLY_FAIL_LIMIT consecutive failures, stand down for
#: APPLY_BACKOFF_S. (Re-enabling NTP when the medic comes back ONLINE is a
#: genuinely separate concern — left as a documented TODO for an online-detector.)
APPLY_FAIL_LIMIT = 3
APPLY_BACKOFF_S = 300.0              # 5 minutes


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

    This is the LOW-LEVEL gate: freshness, fix quality, NTP band, and tolerance.
    The plausibility window, corroboration, and backoff live in
    :class:`GpsClockDisciplinarian`, which layers on top of this.

    Rules, in order (each a reason to do nothing):
      * gps_utc is None            -> no trustworthy satellite time at all.
      * fix < 1                    -> receiver has no valid fix.
      * sats < min_sats            -> weak fix. A 1- or 2-satellite fix can carry
                                      badly-wrong time; we NEVER discipline off it.

    FRESHNESS GATE: the receipt age is ``recv_now - gps_utc_recv``, both wall-clock
    stamps. We refuse if it is negative (clock jumped back / stale stamp) or older
    than ``GPS_UTC_MAX_AGE_S`` — this also kills the step->stale->step runaway.

    LATENCY COMPENSATION: within the fresh window, the UTC value was true at the
    moment we RECEIVED it, so the true current UTC is ``gps_utc + age``.

      * ntp_synced AND |target - sys_now| <= NTP-trust band (60s) -> defer to NTP.
      * |target - sys_now| <= tolerance_s -> close enough; never thrash the clock.

    Otherwise return ``target`` (the caller decides whether to apply it).
    """
    # No trustworthy time, or a fix too weak to trust the time it carries.
    if gps_utc is None or gps_utc_recv is None:
        return None
    if fix is None or fix < 1:
        return None
    if sats is None or sats < min_sats:
        return None

    # Freshness gate — kills the step->stale->step runaway. A negative age means
    # the receipt is from the future (clock jumped back after a step): refuse.
    age = recv_now - gps_utc_recv
    if age < 0 or age > GPS_UTC_MAX_AGE_S:
        return None
    target = gps_utc + age

    # When NTP already has it, defer for small drifts — but let GPS override a
    # wildly-wrong NTP (the band is wide, so only a gross error slips through).
    NTP_TRUST_BAND_S = 60.0
    if ntp_synced and abs(target - sys_now) <= NTP_TRUST_BAND_S:
        return None

    # Close enough already — never thrash a clock that is basically right.
    if abs(target - sys_now) <= tolerance_s:
        return None

    return target


def _in_window(epoch: float) -> bool:
    """True if *epoch* is a finite value inside the fixed plausibility window.
    Rejects bool (isinstance(True, int) is True), NaN/inf, and huge/out-of-era
    values — anything we must never let become the system clock."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return False
    try:
        e = float(epoch)
    except (TypeError, ValueError):
        return False
    if e != e or e in (float("inf"), float("-inf")):   # NaN / inf
        return False
    return GPS_EPOCH_MIN <= e <= GPS_EPOCH_MAX


class GpsClockDisciplinarian:
    """Stateful policy over :func:`clock_decision`: fixed plausibility window,
    median-ring corroboration for steps beyond a small drift, and set-time
    backoff. NO forward-only floor (see the module docstring for why it was
    removed — lockout + ratchet).

    One instance lives for the medic's run (the app owns it). It is pure apart
    from the injected clocks the caller passes to :meth:`evaluate`, so it unit-
    tests without hardware."""

    def __init__(self):
        # Ring of the last few DISTINCT in-window correction OFFSETS. We
        # corroborate on the OFFSET (target - sys_now), not the absolute target:
        # the wrong clock and real time tick at the SAME rate, so a genuinely
        # far-behind clock yields a STABLE offset across ticks even though the
        # absolute target advances ~one tick each time. Absolute targets 30 s
        # apart would never "agree within 5 s"; offsets do.
        self._ring: List[float] = []
        # Dedupe: corroboration must count DISTINCT 0x03 frames, never the same
        # receipt read twice (else one garbage frame could self-corroborate).
        self._last_recv: Optional[float] = None
        self._fail_count = 0
        self._backoff_until = 0.0

    def evaluate(self, gps_utc: Optional[int], gps_utc_recv: Optional[float],
                 sats: Optional[int], fix: Optional[int], sys_now: float,
                 ntp_synced: bool, recv_now: float, *,
                 tolerance_s: float = 10.0, min_sats: int = 4) -> Optional[float]:
        """Return the epoch to set the clock to, or None. Applies the full policy."""
        # Backing off from repeated set-time failures — don't re-arm every tick.
        if recv_now < self._backoff_until:
            return None
        # Same frame as last time we acted on -> ignore (dedupe distinct fixes).
        if gps_utc_recv is not None and gps_utc_recv == self._last_recv:
            return None

        target = clock_decision(gps_utc, gps_utc_recv, sats, fix, sys_now,
                                ntp_synced, recv_now,
                                tolerance_s=tolerance_s, min_sats=min_sats)
        if target is None:
            return None

        # FIXED plausibility window — the anti-lockout, anti-ratchet bound. A real
        # time is always inside it (so it can never refuse the truth); 2099 / 1999
        # are outside it and refused, capping any forward walk at GPS_EPOCH_MAX.
        if not _in_window(target):
            return None

        # A distinct, in-window, actionable fix — remember it so a re-read can't
        # double-count.
        self._last_recv = gps_utc_recv

        drift = abs(target - sys_now)
        if drift <= DIRECT_APPLY_S:
            self._ring.clear()                  # normal drift: apply, no corroboration
            return target

        # LARGER step: corroborate on the time-invariant offset (see __init__).
        offset = target - sys_now
        self._ring.append(offset)
        if len(self._ring) > CORROBORATION_RING:
            self._ring.pop(0)
        # A single interleaved bad frame can't reset progress: we look for ANY
        # cluster of >= K offsets that agree within the tolerance.
        for anchor in self._ring:
            agreeing = [o for o in self._ring if abs(o - anchor) <= CORROBORATION_AGREE_S]
            if len(agreeing) >= CORROBORATION_K:
                applied = sys_now + median(agreeing)
                self._ring.clear()
                # Median could sit a hair outside the window only at the very
                # edge; guard so we never emit an out-of-window clock.
                return applied if _in_window(applied) else None
        return None

    def record_success(self) -> None:
        """Call after a step fully applied (set + rebase). Clears failure backoff.
        No floor to advance — the plausibility window is fixed, not ratcheted."""
        self._fail_count = 0
        self._backoff_until = 0.0

    def record_failure(self, now: float) -> None:
        """Call after apply_clock failed. After repeated failures, back off so we
        stop flapping NTP and churning logs every tick."""
        self._fail_count += 1
        if self._fail_count >= APPLY_FAIL_LIMIT:
            self._backoff_until = now + APPLY_BACKOFF_S


def _fmt_utc(epoch: float) -> str:
    """Format an epoch as ``YYYY-MM-DD HH:MM:SS`` UTC wall-clock for timedatectl."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


Runner = Callable[[List[str]], Tuple[int, str, str]]


def apply_clock(target_epoch: float,
                run: Runner,
                now: Callable[[], float] = time.time) -> bool:
    """Set the system clock to *target_epoch* (UTC) via the ALREADY-SCOPED sudoers
    commands. ``run`` is an injected ``callable(list[str]) -> (rc, out, err)`` so
    this is testable without root.

    ``timedatectl set-time`` is REFUSED whenever systemd-timesyncd is ACTIVE
    (NTP=yes) — regardless of whether it ever actually synchronized, which offline
    it never does. So we ALWAYS disable NTP first (harmless if already off); the
    established provisioning/tool_datetime.py does the same.

    We do NOT re-enable NTP on success: offline, GPS is now the authoritative time
    source. (Re-enabling NTP when the medic comes back online is a SEPARATE concern
    for an online-detector, not this discipline loop.) On FAILURE, however, we roll
    NTP back on — leaving it disabled with no GPS time set would be the worst of
    both worlds.

    The emitted argv MUST match provisioning/sudoers.d/nodemedic (NM_CLOCK):
        /usr/bin/timedatectl set-ntp false
        /usr/bin/timedatectl set-ntp true
        /usr/bin/timedatectl set-time *
    or sudo would prompt for a password and fail. The medic runs UTC, so the
    stamp is formatted as UTC and passed with a trailing "UTC".

    Returns True only if the set-time command succeeded.
    """
    global last_disciplined_at
    # Always disable NTP first — set-time is refused while timesyncd is active.
    run(["sudo", "-n", "timedatectl", "set-ntp", "false"])
    stamp = _fmt_utc(target_epoch) + " UTC"
    rc, _out, _err = run(["sudo", "-n", "timedatectl", "set-time", stamp])
    if rc == 0:
        last_disciplined_at = now()
        return True
    # set-time failed after we disabled NTP: roll back so we don't strand the
    # medic with NTP off AND no GPS time set.
    run(["sudo", "-n", "timedatectl", "set-ntp", "true"])
    return False
