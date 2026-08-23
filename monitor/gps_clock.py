"""GPS clock discipline — set the medic's clock from satellite UTC when offline.

WHY THIS EXISTS: the Pi 5's RTC is NOT battery-backed, so a power-cycled medic
boots with a wrong clock, and in the field there is no internet for NTP. The
Heltec Tracker's GNSS receiver (a UC6580) carries satellite UTC, which the
firmware pushes over the KISS link as a GPS_CMD_UTC (0x03) sub-frame; the serial
splitter decodes it to ``gps_utc`` / ``gps_utc_recv`` in its state. This module
is the PURE decision + apply logic that turns that into a disciplined clock.

SECURITY POSTURE: GPS is an UNTRUSTED input with authority over the system clock.
Two evolutions got us here:
  * An early "forward-only floor" was DELETED — it caused a permanent lockout
    (one bad corroborated jump poisoned the floor forever) and never stopped a
    ratchet.
  * A "direct-apply for small drift" branch was then DELETED too — a single
    small-offset frame that stepped-and-cleared-the-ring re-opened the cold-boot
    self-DoS, and an alternating ±59 s spoof drove a full history rebase + config
    fsync every tick. There is now NO uncorroborated single-frame step.

The remaining defences, all of which a consumer receiver can honestly provide:
  1. FIXED PLAUSIBILITY WINDOW [GPS_EPOCH_MIN, GPS_EPOCH_MAX] — real time is
     always in-window (no lockout); 2099/1999 are refused (caps any walk); and a
     stopped spoof self-corrects on the next real fix (no persisted state).
  2. CORROBORATION FOR EVERY STEP via a short ring of recent DISTINCT fixes:
     a step applies only when the ring shows agreement (a tight K-cluster, or a
     bounded-spread majority for jittery poor-sky fixes), and the ring is cleared
     ONLY on an actual apply or by time-expiry — never by a single frame.
  3. The caller GATES the expensive history rebase + config persist on a
     MEANINGFUL delta (> REBASE_MIN_DELTA_S), so a sub-deadband correction sets
     the clock cheaply without thrashing the SD.

HONEST LIMIT: a spoofer PRESENT and feeding IN-WINDOW, self-consistent times can
still place the clock (median-of-agreeing gives exact placement) — inherent to a
consumer GNSS receiver. What is guaranteed: damage is BOUNDED (never past the
window), recovery is AUTOMATIC when the real signal returns, and transient
garbage / single-frame glitches / poor-sky jitter are all handled honestly.

Everything here is injectable so it is unit-tested without a clock, radio, or root.
"""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from statistics import median
from typing import Callable, List, NamedTuple, Optional, Tuple

#: Epoch of the last successful GPS clock set, for a caller to surface
#: "clock: GPS-synced Xm ago". Module-level so a stateless caller can read it;
#: apply_clock() stamps it. Not UI — just the fact.
last_disciplined_at: Optional[float] = None

#: Freshness window for the satellite-UTC receipt (seconds). gps_utc_recv is a
#: WALL-CLOCK stamp taken in the *splitter* process; the app reads it across a
#: process boundary, so monotonic can't bridge them. We refuse to act unless the
#: receipt is recent, which bounds the latency compensation AND kills the
#: step->stale->step runaway (after a step the receipt reads stale -> refused).
GPS_UTC_MAX_AGE_S = 15.0

#: NTP-trust band: when NTP is synced we defer to it for drifts this small, but
#: still let GPS override a wildly-wrong NTP.
NTP_TRUST_BAND_S = 60.0

#: FIXED plausibility window, anchored on the software era — NOT a moving floor.
#: Any GPS target outside [MIN, MAX] is refused. MUST be bumped occasionally per
#: release; it fails SAFE (discipline just stops, never locks to a wrong time).
GPS_EPOCH_MIN = float(calendar.timegm((2025, 1, 1, 0, 0, 0)))
GPS_EPOCH_MAX = float(calendar.timegm((2040, 1, 1, 0, 0, 0)))

#: Warn the operator when the clock gets within this of GPS_EPOCH_MAX, so the
#: fixed-window cliff (discipline silently stopping) is never a surprise.
WINDOW_WARN_S = 365 * 86400.0            # 1 year

#: Median-ring corroboration. Keep the last CORROBORATION_RING DISTINCT fixes
#: (expiring entries older than RING_EXPIRY_S). Apply when either a TIGHT cluster
#: of >= CORROBORATION_K offsets agree within CORROBORATION_AGREE_S, OR (fallback
#: for jittery poor-sky fixes) there are >= CORROBORATION_K fresh entries whose
#: whole spread is within SPREAD_FALLBACK_S. Apply the MEDIAN of the chosen set.
CORROBORATION_RING = 5
CORROBORATION_K = 3
CORROBORATION_AGREE_S = 5.0
SPREAD_FALLBACK_S = 30.0
RING_EXPIRY_S = 90.0

#: Corrections at or under this are absorbed by the SEEN display deadband
#: (registry.SEEN_CLOCK_SKEW_TOLERANCE_S — kept equal; a test pins that) and the
#: node_watch cooldown, so they SET the clock but need NO history rebase and NO
#: config persist. This is what stops a small-step / alternating-±59s path from
#: thrashing the SD even if it corroborates.
REBASE_MIN_DELTA_S = 120.0

#: Don't fsync the last-sync stamp more than once per this interval on a
#: correct-but-frequently-confirmed clock (config-write rate limit).
PERSIST_MIN_INTERVAL_S = 300.0

#: set-time failure backoff. After APPLY_FAIL_LIMIT consecutive failures, stand
#: down for APPLY_BACKOFF_S so we stop flapping NTP / churning logs every tick.
APPLY_FAIL_LIMIT = 3
APPLY_BACKOFF_S = 300.0

# Status reasons the disciplinarian exposes for operator surfacing.
REASON_NO_FIX = "no_fix"
REASON_STALE = "stale"
REASON_OUT_OF_WINDOW = "out_of_window"
REASON_AWAITING = "awaiting"
REASON_IN_SYNC = "in_sync"
REASON_SYNCED = "synced"
REASON_BACKOFF = "backoff"


# The three shapes evaluate() can return, so the caller can tell a real clock
# STEP from a mere CONFIRMation (GPS present and AGREEING) from doing NOTHING.
#
# WHY CONFIRM EXISTS (proven live 2026-08-22): in the normal steady state the
# clock is already right — GPS agrees within tolerance — so clock_decision()
# returns None and, before this, NOTHING was recorded. datetime.json stayed
# empty and the date/time screen read "never synced" even with an 8-sat fix
# actively agreeing, so the operator couldn't tell "GPS checked, clock is right"
# from "GPS never worked". A CONFIRM stamps that agreement (rate-limited),
# WITHOUT stepping or rebasing the clock.
DECISION_STEP = "step"        # a corroborated, meaningful correction — set the clock
DECISION_CONFIRM = "confirm"  # a solid, fresh, agreeing fix — stamp, don't touch the clock
DECISION_NONE = "none"        # nothing trustworthy/actionable this tick


class ClockDecision(NamedTuple):
    """What evaluate() decided this tick.

    * ``kind``   — DECISION_STEP / DECISION_CONFIRM / DECISION_NONE.
    * ``target`` — the epoch to set (STEP) or the confirmed GPS UTC (CONFIRM);
      None for NONE. On CONFIRM the caller passes this to mark_synced() as the
      stamp time WITHOUT stepping the clock.
    * ``reason`` — a REASON_* for operator surfacing.
    """
    kind: str
    target: Optional[float]
    reason: str


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

    LOW-LEVEL gate only: freshness, fix quality, NTP band, tolerance. The
    plausibility window and corroboration live in :class:`GpsClockDisciplinarian`.

      * gps_utc None / fix < 1 / sats < min_sats -> no trustworthy fix.
      * receipt age < 0 or > GPS_UTC_MAX_AGE_S   -> stale (also kills the runaway).
      * ntp_synced and |target-sys_now| <= NTP band -> defer to NTP.
      * |target-sys_now| <= tolerance_s          -> already right, don't thrash.
    Otherwise return the latency-compensated ``target`` (gps_utc + age)."""
    if gps_utc is None or gps_utc_recv is None:
        return None
    if fix is None or fix < 1:
        return None
    if sats is None or sats < min_sats:
        return None
    age = recv_now - gps_utc_recv
    if age < 0 or age > GPS_UTC_MAX_AGE_S:
        return None
    target = gps_utc + age
    if ntp_synced and abs(target - sys_now) <= NTP_TRUST_BAND_S:
        return None
    if abs(target - sys_now) <= tolerance_s:
        return None
    return target


def ntp_fallback_decision(*, autosync: bool, ntp_enabled: bool,
                          gps_fresh: bool, gps_acted: bool) -> bool:
    """Whether to RE-ENABLE NTP (``timedatectl set-ntp true``) THIS tick.

    THE GAP THIS CLOSES: when GPS disciplines the clock it DISABLES timesyncd
    (``set-ntp false``) so satellite UTC is the sole authority — correct off-grid,
    where there is no NTP to reach anyway. But a medic that GPS-synced once and
    then sits INDOORS (no sky -> no GPS) with WiFi/internet back would silently
    keep NTP OFF and slowly drift, having thrown away the one time source it can
    actually reach. This detector restores NTP as the FALLBACK for exactly that
    case: GPS can't help, so let timesyncd take over again.

    NO NETWORK PROBE — ON PURPOSE. Enabling timesyncd is cheap and HARMLESS whether
    or not there is internet: offline it just coasts (the clock keeps ticking) and
    syncs automatically the moment a network returns. timesyncd ITSELF is the
    authority on reachability — we do NOT second-guess it with a curl. An
    offline-first field device must not phone home every 30 s, nor eat a 5 s probe
    timeout each tick; and a TCP-443 curl wouldn't even prove NTP (UDP 123) can
    sync, so the probe was pure cost. We just turn NTP on and let it do its job;
    the status line stays honest by reading ntp_synchronized() (see _maybe_restore_ntp).

    WHY THE TWO TOGGLES ARE STABLE (they trigger on OPPOSITE conditions, so they
    can never oscillate against each other):
        * GPS PRESENT (fresh fix)   -> apply_clock() sets NTP OFF (GPS wins).
        * GPS ABSENT (no/stale fix) -> this sets NTP ON (let timesyncd try).
    A tick is in exactly one of those worlds, so at most one toggle fires, and it
    fires only when the state actually needs to change (see the ``ntp_enabled``
    short-circuit). NTP is disabled ONLY on a genuine GPS STEP, so marginal-sky
    flicker can't oscillate it. When GPS returns, apply_clock disables NTP — no fight.

    Returns True only when EVERY condition holds; otherwise a pure no-op:
      * ``autosync`` off  -> stand down entirely (the operator set the clock by
        hand; same posture as _check_gps_clock).
      * ``gps_acted``     -> GPS touched the clock THIS tick; don't re-enable NTP
        in the same breath apply_clock just turned it off — never fight.
      * ``ntp_enabled``   -> already on; toggling again would be pure thrash.
      * ``gps_fresh``     -> GPS has a usable fix; it is the authority, leave NTP
        off (this is the steady off-grid state).
    """
    if not autosync:
        return False
    if gps_acted:
        return False
    if ntp_enabled:
        return False
    if gps_fresh:
        return False
    return True


def enable_ntp(run: Runner) -> bool:
    """Re-enable systemd-timesyncd via the ALREADY-SCOPED sudoers command, so a
    medic back online with no GPS gets NTP correction again. argv MUST match
    provisioning/sudoers.d/nodemedic (NM_CLOCK ``set-ntp true``). Returns True on
    success. This is the mirror of apply_clock's ``set-ntp false``; the two are
    stable because they fire on opposite conditions (see ntp_fallback_decision)."""
    rc, _out, _err = run(["sudo", "-n", "timedatectl", "set-ntp", "true"])
    return rc == 0


def _in_window(epoch) -> bool:
    """True if *epoch* is a finite number inside the fixed plausibility window.
    Rejects bool (isinstance(True, int) is True), NaN/inf, and out-of-era values —
    anything we must never let become the system clock."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return False
    e = float(epoch)
    if e != e or e in (float("inf"), float("-inf")):     # NaN / inf
        return False
    return GPS_EPOCH_MIN <= e <= GPS_EPOCH_MAX


def rebase_needed(delta: float) -> bool:
    """Whether a step of *delta* seconds warrants a full history rebase + persist.
    Sub-deadband corrections are absorbed by the SEEN deadband + node_watch
    cooldown and must NOT thrash the SD."""
    return isinstance(delta, (int, float)) and abs(delta) > REBASE_MIN_DELTA_S


class GpsClockDisciplinarian:
    """Stateful policy over :func:`clock_decision`: fixed plausibility window and
    median-ring corroboration for EVERY step (no uncorroborated single-frame
    apply, no forward-only floor). Exposes a status reason for operator surfacing.

    One instance lives for the medic's run (the app owns it). Pure apart from the
    injected clocks passed to :meth:`evaluate`, so it unit-tests without hardware."""

    def __init__(self):
        # Ring of the last few DISTINCT (offset, recv_time) entries. We
        # corroborate on the OFFSET (target - sys_now), not the absolute target:
        # the wrong clock and real time tick at the SAME rate, so a genuinely
        # far-behind clock yields a STABLE offset across ticks even though the
        # absolute target advances each tick. Cleared ONLY on an apply; stale
        # entries fall off by time-expiry.
        self._ring: List[Tuple[float, float]] = []
        self._last_recv: Optional[float] = None     # dedupe distinct frames
        self._fail_count = 0
        self._backoff_until = 0.0
        self._last_good_at: Optional[float] = None   # last time GPS set/confirmed
        # Epoch of the last datetime.json sync STAMP (step or confirmation). Used
        # ONLY to rate-limit confirmation stamps so a steadily-correct clock does
        # not fsync the config every 30 s tick (PERSIST_MIN_INTERVAL_S).
        self._last_persist_at: Optional[float] = None
        self.last_reason: str = REASON_NO_FIX
        # NTP-fallback status, set by the online-detector (_maybe_restore_ntp)
        # when GPS can't help and timesyncd has been re-enabled to take over.
        # Purely for HONEST status on the datetime screen. TWO flags, because
        # enabling timesyncd is NOT the same as the clock being on internet time:
        #   * ntp_fallback  -> NTP is ON as the fallback (GPS absent), but
        #     timesyncd may still be COASTING (it takes seconds-to-minutes to
        #     reach a server, and a captive portal may never let it).
        #   * ntp_synced    -> timesyncd has ACTUALLY synchronised the clock
        #     (ntp_synchronized() is True). Only then may the screen claim the
        #     clock is on internet time; until then it honestly says "awaiting".
        # Both are cleared the moment GPS takes the clock back (apply/confirm),
        # because NTP is then off again — a flag must never outlive its reality.
        self.ntp_fallback: bool = False
        self.ntp_synced: bool = False

    def _refusal_reason(self, gps_utc, gps_utc_recv, sats, fix, sys_now,
                        ntp_synced, recv_now, tolerance_s, min_sats) -> str:
        """Classify a clock_decision()==None so the operator sees WHY. Mirrors the
        gate's checks (clock_decision stays the single authority for the go/no-go;
        this only labels)."""
        if (gps_utc is None or gps_utc_recv is None or fix is None or fix < 1
                or sats is None or sats < min_sats):
            return REASON_NO_FIX
        age = recv_now - gps_utc_recv
        if age < 0 or age > GPS_UTC_MAX_AGE_S:
            return REASON_STALE
        if not _in_window(gps_utc + age):
            return REASON_OUT_OF_WINDOW
        return REASON_IN_SYNC          # within NTP band / tolerance: clock is right

    def evaluate(self, gps_utc: Optional[int], gps_utc_recv: Optional[float],
                 sats: Optional[int], fix: Optional[int], sys_now: float,
                 ntp_synced: bool, recv_now: float, *,
                 tolerance_s: float = 10.0, min_sats: int = 4) -> ClockDecision:
        """Decide what to do with the clock this tick: STEP it, CONFIRM it, or
        do NOTHING. Applies the full policy and updates ``self.last_reason``.

        Returns a :class:`ClockDecision` (never a bare epoch): the caller steps +
        rebases + stamps on STEP, stamps ONLY on CONFIRM, and does nothing on NONE.
        """
        if recv_now < self._backoff_until:
            self.last_reason = REASON_BACKOFF
            return ClockDecision(DECISION_NONE, None, REASON_BACKOFF)
        # Same frame as last acted on -> ignore (dedupe distinct fixes). Leave the
        # reason as-is (the previous frame's classification still stands).
        if gps_utc_recv is not None and gps_utc_recv == self._last_recv:
            return ClockDecision(DECISION_NONE, None, self.last_reason)

        target = clock_decision(gps_utc, gps_utc_recv, sats, fix, sys_now,
                                ntp_synced, recv_now,
                                tolerance_s=tolerance_s, min_sats=min_sats)
        if target is None:
            # No STEP. Either the fix is untrustworthy (weak/stale/out-of-window/
            # no-fix -> a real refusal), or it is SOLID and the clock already
            # AGREES with it -> a CONFIRMATION (the normal steady state). Same
            # solidity gates a step demands; only the corroboration-to-step is
            # skipped, because nothing changes. Honesty: a confirmation must
            # reflect a REAL, CURRENT, agreeing fix — never a stale or weak one.
            reason = self._refusal_reason(
                gps_utc, gps_utc_recv, sats, fix, sys_now, ntp_synced, recv_now,
                tolerance_s, min_sats)
            self.last_reason = reason
            if reason == REASON_IN_SYNC:
                confirmed = self._confirmation_target(
                    gps_utc, gps_utc_recv, sys_now, recv_now, tolerance_s)
                if confirmed is not None:
                    # A genuine agreement (within tolerance of a solid fix), not
                    # merely inside the wider NTP band. GPS is actively holding
                    # the clock right, so surface it as GPS-synced...
                    self._last_good_at = recv_now
                    # ...but only STAMP the config at most once per interval, so a
                    # correct-but-constantly-confirmed clock doesn't churn the SD.
                    if self._persist_due(confirmed):
                        self._last_persist_at = confirmed
                        return ClockDecision(DECISION_CONFIRM, confirmed,
                                             REASON_IN_SYNC)
                    return ClockDecision(DECISION_NONE, None, REASON_IN_SYNC)
                # Inside the NTP trust band but NOT within tolerance: NTP owns the
                # clock, so this is not a GPS confirmation. Fall through to NONE.
            return ClockDecision(DECISION_NONE, None, reason)

        # FIXED plausibility window — the anti-lockout, anti-ratchet bound.
        if not _in_window(target):
            self.last_reason = REASON_OUT_OF_WINDOW
            return ClockDecision(DECISION_NONE, None, REASON_OUT_OF_WINDOW)

        # A distinct, in-window, actionable fix.
        self._last_recv = gps_utc_recv
        offset = target - sys_now
        # Expire stale entries so an offset from minutes ago can't linger, then
        # add this one. Ring is NEVER cleared by a single frame — only on apply.
        self._ring = [(o, r) for (o, r) in self._ring
                      if recv_now - r <= RING_EXPIRY_S]
        self._ring.append((offset, recv_now))
        if len(self._ring) > CORROBORATION_RING:
            self._ring.pop(0)

        chosen = self._corroborated_offset()
        if chosen is None:
            self.last_reason = REASON_AWAITING
            return ClockDecision(DECISION_NONE, None, REASON_AWAITING)
        applied = sys_now + chosen
        if not _in_window(applied):            # median could sit at the very edge
            self.last_reason = REASON_OUT_OF_WINDOW
            return ClockDecision(DECISION_NONE, None, REASON_OUT_OF_WINDOW)
        self._ring.clear()                     # consumed by an actual apply
        self.last_reason = REASON_SYNCED
        return ClockDecision(DECISION_STEP, applied, REASON_SYNCED)

    def _confirmation_target(self, gps_utc, gps_utc_recv, sys_now, recv_now,
                             tolerance_s) -> Optional[float]:
        """The latency-compensated GPS target IFF the clock GENUINELY AGREES with
        the fix (|target - sys_now| <= tolerance_s). The caller has already
        verified solidity + freshness + in-window (reason == IN_SYNC); this is the
        final honesty gate that separates a true agreement from merely sitting
        inside the wider NTP trust band (where NTP, not GPS, holds the clock and a
        'GPS-synced' claim would be a lie). Returns None when not a real confirm."""
        age = recv_now - gps_utc_recv
        target = gps_utc + age
        if abs(target - sys_now) <= tolerance_s:
            return float(target)
        return None

    def _persist_due(self, target: float) -> bool:
        """Whether a confirmation should STAMP datetime.json now, rate-limited to
        at most once per PERSIST_MIN_INTERVAL_S. First confirmation always stamps
        (no prior stamp); thereafter only once the interval has elapsed, so a
        steadily-correct clock confirms in-memory every tick but fsyncs rarely."""
        lp = self._last_persist_at
        return lp is None or (target - lp) >= PERSIST_MIN_INTERVAL_S

    def _corroborated_offset(self) -> Optional[float]:
        """The agreed correction offset, or None if the ring doesn't yet agree.
        Robust to a single interleaved garbage frame AND to poor-sky jitter."""
        offsets = [o for (o, _r) in self._ring]
        # Fast path: a TIGHT cluster of K offsets (good signal, or a spoofer
        # feeding a consistent offset — the accepted honest limit).
        for anchor in offsets:
            agreeing = [o for o in offsets if abs(o - anchor) <= CORROBORATION_AGREE_S]
            if len(agreeing) >= CORROBORATION_K:
                return median(agreeing)
        # Fallback: enough fresh entries whose WHOLE spread is bounded — a legit
        # behind-clock in an urban canyon (offsets a few seconds apart) still
        # corrects, while scattered garbage (large spread) still won't.
        if len(offsets) >= CORROBORATION_K and (max(offsets) - min(offsets)) <= SPREAD_FALLBACK_S:
            return median(offsets)
        return None

    def record_success(self, now: Optional[float] = None) -> None:
        """Call after a step fully applied. Stamps the last-good time (for status),
        clears failure backoff. No floor to advance (fixed window needs none)."""
        self._fail_count = 0
        self._backoff_until = 0.0
        if now is not None:
            self._last_good_at = now
            # A step just stamped datetime.json, so seed the confirmation
            # rate-limit: the next in-sync ticks won't re-stamp for an interval.
            self._last_persist_at = now
        self.last_reason = REASON_SYNCED

    def gps_absent(self) -> bool:
        """True when the last evaluation found NO usable satellite time — either no
        fix at all or a stale receipt. These are the ONLY states where GPS genuinely
        cannot discipline the clock, so they are the only states in which the
        online-detector should let NTP take over. Every other reason (in-sync,
        synced, awaiting corroboration, out-of-window, backoff) means GPS is
        present/holding/trying, and we defer to it — that opposite-condition split
        is what keeps the NTP-off (GPS) and NTP-on (fallback) toggles from fighting."""
        return self.last_reason in (REASON_NO_FIX, REASON_STALE)

    def record_failure(self, now: float) -> None:
        """Call after apply_clock failed. Back off after repeated failures."""
        self._fail_count += 1
        if self._fail_count >= APPLY_FAIL_LIMIT:
            self._backoff_until = now + APPLY_BACKOFF_S

    def status_line(self, now: float) -> str:
        """A short, honest one-liner for the datetime screen: the operator must be
        able to tell GPS-synced from awaiting-corroboration from refused, and get
        a heads-up before the fixed window's far edge."""
        warn = ""
        if now >= GPS_EPOCH_MAX - WINDOW_WARN_S:
            warn = " (GPS time window ends soon — update the medic)"
        r = self.last_reason
        if r == REASON_BACKOFF:
            return "GPS clock update failing — will retry" + warn
        if r == REASON_OUT_OF_WINDOW:
            return "GPS time outside the plausible window — ignored" + warn
        if r == REASON_STALE:
            if self.ntp_fallback:
                # HONEST: only claim the clock is ON internet time once timesyncd
                # has actually synchronised; while merely enabled-but-coasting say
                # "awaiting" (we turned NTP on, the clock isn't from it yet).
                if self.ntp_synced:
                    return "GPS signal stale — using internet time (NTP)" + warn
                return "GPS signal stale — NTP on, awaiting internet time" + warn
            return "GPS signal stale — waiting for a fresh fix" + warn
        if r == REASON_NO_FIX:
            if self.ntp_fallback:
                if self.ntp_synced:
                    return "No GPS fix — using internet time (NTP)" + warn
                return "No GPS fix — NTP on, awaiting internet time" + warn
            return "No GPS fix yet" + warn
        if r == REASON_AWAITING:
            return "Awaiting GPS corroboration…" + warn
        # in_sync / synced
        if self._last_good_at is not None:
            from provisioning.tool_datetime import format_synced_ago
            return "GPS-" + format_synced_ago(self._last_good_at, now) + warn
        return "GPS not yet synced" + warn


def _fmt_utc(epoch: float) -> str:
    """Format an epoch as ``YYYY-MM-DD HH:MM:SS`` UTC wall-clock for timedatectl."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


Runner = Callable[[List[str]], Tuple[int, str, str]]


def apply_clock(target_epoch: float,
                run: Runner,
                now: Callable[[], float] = time.time) -> bool:
    """Set the system clock to *target_epoch* (UTC) via the ALREADY-SCOPED sudoers
    commands. ``run`` is an injected ``callable(list[str]) -> (rc, out, err)``.

    ``timedatectl set-time`` is REFUSED whenever systemd-timesyncd is ACTIVE
    (NTP=yes) — regardless of whether it ever synchronized, which offline it never
    does. So we ALWAYS disable NTP first (harmless if already off). We do NOT
    re-enable NTP on success (offline, GPS is the authority; re-enabling when back
    online is a SEPARATE online-detector concern). On FAILURE we roll NTP back on,
    so we never strand the medic with NTP off AND no GPS time set.

    Emitted argv MUST match provisioning/sudoers.d/nodemedic (NM_CLOCK):
        /usr/bin/timedatectl set-ntp false
        /usr/bin/timedatectl set-ntp true
        /usr/bin/timedatectl set-time *
    Returns True only if set-time succeeded."""
    global last_disciplined_at
    run(["sudo", "-n", "timedatectl", "set-ntp", "false"])
    stamp = _fmt_utc(target_epoch) + " UTC"
    rc, _out, _err = run(["sudo", "-n", "timedatectl", "set-time", stamp])
    if rc == 0:
        last_disciplined_at = now()
        return True
    run(["sudo", "-n", "timedatectl", "set-ntp", "true"])
    return False
