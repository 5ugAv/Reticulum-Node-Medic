"""GPS clock discipline — set the medic's clock from satellite UTC when offline.

WHY THIS EXISTS: the Pi 5's RTC is NOT battery-backed, so a power-cycled medic
boots with a wrong clock, and in the field there is no internet for NTP. The
Heltec Tracker's GNSS receiver (a UC6580) carries satellite UTC, which the
firmware pushes over the KISS link as a GPS_CMD_UTC (0x03) sub-frame; the serial
splitter decodes it to ``gps_utc`` / ``gps_utc_recv`` in its state. This module
is the PURE decision + apply logic that turns that into a disciplined clock.

SECURITY POSTURE: GPS is now an UNTRUSTED input with authority to set the system
clock, and a clock step ripples into every wall-clock-stamped record (liveness,
escalation, history). So the discipline is conservative and layered:
  * small corrections apply immediately (normal drift + stale-RTC boot);
  * LARGE corrections must be CORROBORATED by several agreeing fresh fixes;
  * good time never runs backwards (a backward jump is spoof or fault);
  * a fresh fix is required (weak/stale fixes are refused — see clock_decision).
Corroboration is best-effort: it defeats transient garbage and single-frame
glitches — the realistic field failure — but CANNOT fully defeat a sustained RF
spoofer feeding a consumer receiver. That is honestly out of scope for a field
medic on one GNSS board.

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

#: Freshness window for the satellite-UTC receipt (seconds). The receipt time
#: (gps_utc_recv) is a WALL-CLOCK stamp taken in the *splitter* process; the app
#: reads it across a process boundary, so time.monotonic() can't bridge them.
#: We instead REFUSE to act unless the receipt is recent, which:
#:   (a) bounds the uncompensated error to this window — trivial next to the
#:       minutes-to-hours error we exist to fix; and
#:   (b) KILLS the feedback runaway: after we step the clock, gps_utc_recv (taken
#:       on the OLD clock) reads stale against the new clock -> refused -> no more
#:       action until a fresh 0x03 frame re-stamps gps_utc_recv on the corrected
#:       clock, at which point the drift is < tolerance and we no-op.
GPS_UTC_MAX_AGE_S = 15.0

#: Sane-jump ceiling. Corrections within this apply IMMEDIATELY — that covers
#: normal drift and the common stale-RTC cold boot (a Pi with no RTC battery
#: typically boots at the last-known / build epoch, well under two days off in
#: practice, but we allow two days of slack). Anything larger is extraordinary
#: and must be corroborated before we let an untrusted fix leap the clock.
SANE_STEP_S = 2 * 86400.0            # 2 days

#: A LARGE correction (> SANE_STEP_S) must be seen from K DISTINCT fresh fixes
#: whose targets agree within CORROBORATION_AGREE_S before it applies. This lets
#: a genuinely-far-behind cold boot correct (a few agreeing fixes over seconds)
#: while rejecting a one-off glitch/garbage frame. If fixes stop arriving the
#: pending state resets after CORROBORATION_TIMEOUT_S. Best-effort only — see the
#: module docstring's spoofer note.
CORROBORATION_K = 3
CORROBORATION_AGREE_S = 5.0
CORROBORATION_TIMEOUT_S = 60.0

#: GPS time only moves FORWARD. Once we have good time, reject a target more than
#: this far behind it: a backward jump is either a spoof or a fault, never a real
#: clock. Small tolerance so ordinary jitter around the second boundary is fine.
BACKWARD_TOLERANCE_S = 10.0

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
    The sane-jump ceiling, corroboration, and backward-guard live in
    :class:`GpsClockDisciplinarian`, which layers on top of this.

    Rules, in order (each a reason to do nothing):
      * gps_utc is None            -> no trustworthy satellite time at all.
      * fix < 1                    -> receiver has no valid fix.
      * sats < min_sats            -> weak fix. A 1- or 2-satellite fix can carry
                                      badly-wrong time; we NEVER discipline off it.

    FRESHNESS GATE: the receipt age is ``recv_now - gps_utc_recv``, both wall-clock
    stamps. We refuse if it is negative (clock jumped back / stale stamp) or older
    than ``GPS_UTC_MAX_AGE_S``. This is what stops the runaway (see the constant's
    note) — a stale receipt after a step is refused rather than re-compensated.

    LATENCY COMPENSATION: within the fresh window, the UTC value was true at the
    moment we RECEIVED it, so the true current UTC is ``gps_utc + age``. The gate
    keeps ``age`` small, so the compensation is a bounded few seconds.

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

    # Freshness gate — bounds the compensation AND kills the step->stale->step
    # runaway. A negative age means the receipt is from the future (clock jumped
    # back after we stepped it): refuse.
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


class GpsClockDisciplinarian:
    """Stateful policy over :func:`clock_decision`: sane-jump ceiling, multi-fix
    corroboration for large steps, forward-only guard, and set-time backoff.

    One instance lives for the medic's run (the app owns it). It is pure apart
    from the injected clocks the caller passes to :meth:`evaluate`, so it unit-
    tests without hardware. ``last_good_epoch`` is seeded by the caller from a
    tiny persisted store so the forward-only guard survives a restart."""

    def __init__(self, last_good_epoch: Optional[float] = None):
        self.last_good_epoch = last_good_epoch
        self._pending_target: Optional[float] = None
        self._pending_count = 0
        self._pending_since: Optional[float] = None
        # Dedupe: corroboration must count DISTINCT 0x03 frames, never the same
        # receipt read twice (else one garbage frame could self-corroborate).
        self._last_recv: Optional[float] = None
        self._fail_count = 0
        self._backoff_until = 0.0

    def _reset_pending(self) -> None:
        self._pending_target = None
        self._pending_count = 0
        self._pending_since = None

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
        # A distinct, actionable fix — remember it so a re-read can't double-count.
        self._last_recv = gps_utc_recv

        # Forward-only: good time never runs backwards. A target well behind our
        # last-known-good epoch is a spoof or a fault, not a real clock.
        if (self.last_good_epoch is not None
                and target < self.last_good_epoch - BACKWARD_TOLERANCE_S):
            return None

        drift = abs(target - sys_now)
        if drift <= SANE_STEP_S:
            self._reset_pending()               # small step: no corroboration needed
            return target

        # LARGE step: require K agreeing fresh fixes before touching the clock.
        if (self._pending_since is not None
                and recv_now - self._pending_since > CORROBORATION_TIMEOUT_S):
            self._reset_pending()               # fixes stopped -> start over
        if self._pending_count == 0:
            self._pending_target = target
            self._pending_count = 1
            self._pending_since = recv_now
            return None
        if abs(target - self._pending_target) <= CORROBORATION_AGREE_S:
            self._pending_count += 1
            if self._pending_count >= CORROBORATION_K:
                applied = self._pending_target
                self._reset_pending()
                return applied
            return None
        # This fix disagreed with the pending one -> restart corroboration on it.
        self._pending_target = target
        self._pending_count = 1
        self._pending_since = recv_now
        return None

    def record_success(self, applied_epoch: float) -> None:
        """Call after apply_clock succeeded. Advances the forward-only floor and
        clears any failure backoff."""
        self.last_good_epoch = applied_epoch
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
    established provisioning/tool_datetime.py does the same. Doing it only when
    "NTPSynchronized" was true left the feature dead in its own scenario (a
    power-cycled, offline medic reads NTPSynchronized=no yet set-time is refused).

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
