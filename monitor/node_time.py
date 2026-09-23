"""Time over the mesh — the NODE's side (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23; revised after review the same day).

A Pi propagation node on a solar bank dies overnight and boots again in the
sun with no correct time: no RTC, no internet in the field, no NTP. A
wrong clock corrupts its own logs and LXMF message expiry and misleads
VITALS. So the node ASKS its medic for the time over LoRa (TIME_REQ), the
medic answers with a SIGNED time (TIME), and the node sets its clock
through one tiny root helper and says what it did (TIME_ACK, with a
status byte).

This module is the policy, pure: the trust anchor, the sanity window, the
30 s slop, the monotonic floor, the issued-nonce memory, the NTP rule, the
helper invocation, the ask schedule with its backoff. It runs ON THE NODE
(shipped in the reporter package, workflows.build._HEALTH_MODULES) and is
imported by monitor.pi_health_reporter, which owns the RNS calls. Every
outside effect — the clock, timedatectl, the packets, the state file — is
injected, so all of it is tested with plain values and fake RNS. Nothing
in here may accept a time from anyone but the trusted medic.

Freshness, in layers: Reticulum's transport refuses a replayed packet by
its packet-hash list (first layer); at the protocol, the FLOOR (an epoch
must be newer than the last one applied, persisted across restarts) and
the ISSUED NONCES (an answer is "asked" only when its nonce is one this
node sent, within ISSUED_NONCE_TTL_S) own freshness. A pushed TIME (nonce
not ours) still passes the floor — it is signed for this node.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Callable, Optional, Tuple

from monitor.health_reply import (TIME_STATUS_HELPER_FAILED, TIME_STATUS_NOT_NEEDED,
                                  TIME_STATUS_REFUSED_NTP, TIME_STATUS_REFUSED_STALE,
                                  TIME_STATUS_SET, verify_time)

#: Written at birth (workflows.build.install_time_trust) and by the reporter
#: push: which medic this node believes about the time. Keyed by the medic's
#: health-reply identity hash; reply_dest is how the node recalls that
#: identity from an announce it heard.
TRUST_PATH = "~/.rnm-health/trusted_medic.json"
#: The monotonic floor, persisted: the last epoch this node APPLIED. A TIME
#: whose epoch is not newer is refused (status 4) — a captured TIME cannot
#: wind the clock back after a restart either (2026-09-23 review).
TIME_STATE_PATH = "~/.rnm-health/time_state.json"
#: The ONLY road to the clock. The reporter runs as the build user; this
#: helper (installed 0755 root, sudoers NOPASSWD for that one path) checks
#: its argument and runs `date -s` — nothing else.
SETTIME_HELPER = "/usr/local/sbin/nm-settime"
#: A single unicast packet crosses N LoRa hops and lands late. The figure
#: "roughly 1-3 s per hop" is an ESTIMATE from the 1.8 kbps airtime of an
#: ~81-byte packet plus per-relay processing — not a measurement; nothing
#: here has been timed on air (2026-09-23). Moving the clock for less than
#: this would chase the latency, not the truth; logs, LXMF expiry and
#: VITALS are all fine at ±30 s.
CLOCK_SLOP_S = 30
#: Sanity window for a time anyone offers: 2026-01-01 .. 2100-01-01 UTC.
#: Before the first is a clock that has not been set; after the second is
#: nonsense. Both are exactly 10 digits, which the helper also insists on.
EPOCH_MIN = 1767225600
EPOCH_MAX = 4102444800
#: On reporter start, wait this long before asking — NTP may still fix
#: the clock on a node that does have internet (2026-09-23).
ASK_GRACE_S = 90
#: ...then ask this often while timedatectl says NTP is not synchronised
#: and no TIME has been applied yet.
ASK_EVERY_S = 600
#: Once a TIME was applied, re-ask this often while NTP stays unsynced: a
#: node whose clock was set at dawn drifts, and the medic's push is only
#: opportunistic (it rides on an operator's ping). No latch (review).
RE_ASK_S = 24 * 3600
#: After this many consecutive IDENTICAL refusals (no trust file / medic
#: not recalled / no road) the ask goes hourly — a node with no medic in
#: range must not warm a path every ten minutes forever.
ASK_BACKOFF_AFTER = 3
ASK_BACKOFF_S = 3600
#: A nonce this node issued stays "ours" this long; bounded too.
ISSUED_NONCE_TTL_S = 15 * 60
ISSUED_NONCE_MAX = 32

_HEX32 = set("0123456789abcdef")


# -- the trust anchor ------------------------------------------------------------

def _is_hash(s) -> bool:
    return isinstance(s, str) and len(s) == 32 and set(s.lower()) <= _HEX32


def trust_anchor(identity_hash: str, reply_dest: str, name: str, since: str) -> dict:
    """The trust file's content. Refuses anything that is not two 16-byte
    hashes: a guessed anchor is worse than none (no time is accepted)."""
    if not (_is_hash(identity_hash) and _is_hash(reply_dest)):
        raise ValueError("trust anchor needs 32-hex identity and reply-destination hashes")
    return {"identity_hash": identity_hash.lower(), "reply_dest": reply_dest.lower(),
            "name": str(name or ""), "since": str(since or "")}


def trust_anchor_json(anchor: dict) -> str:
    """Canonical text — sorted keys, two-space indent, trailing newline — so
    a read-back can compare bytes rather than parse and hope."""
    return json.dumps(anchor, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def load_trust(path: str = TRUST_PATH) -> Optional[dict]:
    """The anchor, or None when there is no file or it is not an anchor."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            data = json.load(fh)
        return trust_anchor(data.get("identity_hash"), data.get("reply_dest"),
                            data.get("name"), data.get("since"))
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def recall_trusted_medic(rns, trust: dict):
    """The medic's identity, recalled from the announce of its reply
    destination — and ONLY if that identity's hash is the anchored one.
    None when no announce has been heard yet, or another key sits behind
    the destination hash."""
    try:
        ident = rns.Identity.recall(bytes.fromhex(trust["reply_dest"]))
        if ident is None or ident.hash.hex() != trust["identity_hash"]:
            return None
        return ident
    except Exception:                                              # noqa: BLE001
        return None


# -- the monotonic floor ------------------------------------------------------------

def load_last_applied(path: str = TIME_STATE_PATH) -> Optional[int]:
    """The last epoch this node applied, or None (no file, or not an int)."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            data = json.load(fh)
        v = data.get("last_applied_epoch")
        if isinstance(v, bool) or not isinstance(v, int):
            return None
        return v
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def save_last_applied(epoch: int, path: str = TIME_STATE_PATH) -> bool:
    """Atomic write (tmp + rename). False, never an exception, when the
    directory cannot be written — the clock was still set; the floor is
    simply not remembered across a restart."""
    try:
        p = os.path.expanduser(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"last_applied_epoch": int(epoch)}, fh, sort_keys=True, indent=2)
            fh.write("\n")
        os.replace(tmp, p)
        return True
    except (OSError, ValueError, TypeError):
        return False


def passes_floor(epoch: int, last_applied: Optional[int]) -> bool:
    """Strictly newer than the last applied epoch, or no floor yet."""
    return last_applied is None or int(epoch) > int(last_applied)


# -- the nonces this node issued -----------------------------------------------------------

class IssuedNonces:
    """The TIME_REQ nonces this node sent, with a TTL and a cap. A TIME whose
    nonce is here answers an ask; any other TIME is a push. Both still pass
    the floor and the signature — this only labels the outcome honestly."""

    def __init__(self, ttl_s: float = ISSUED_NONCE_TTL_S, max_entries: int = ISSUED_NONCE_MAX,
                 now: Callable[[], float] = None):
        import time as _time
        self._ttl, self._max, self._now = ttl_s, max_entries, now or _time.monotonic
        self._lock = threading.Lock()
        self._rows: dict = {}

    def issue(self, nonce: bytes) -> None:
        with self._lock:
            self._expire()
            while len(self._rows) >= self._max:
                del self._rows[min(self._rows, key=self._rows.get)]
            self._rows[bytes(nonce)] = self._now()

    def was_issued(self, nonce: bytes) -> bool:
        """True (and the nonce is spent) when this node issued it."""
        with self._lock:
            self._expire()
            return self._rows.pop(bytes(nonce), None) is not None

    def _expire(self) -> None:
        cutoff = self._now() - self._ttl
        for k in [k for k, t in self._rows.items() if t < cutoff]:
            del self._rows[k]


# -- sanity, slop, the helper ----------------------------------------------------------

def epoch_is_sane(t) -> bool:
    try:
        return EPOCH_MIN <= int(t) < EPOCH_MAX
    except (TypeError, ValueError):
        return False


def should_apply(medic_time_s: int, node_now_s: float) -> bool:
    """Move the clock only when it is off by more than the LoRa slop."""
    return abs(int(medic_time_s) - int(node_now_s)) > CLOCK_SLOP_S


def settime_argv(epoch: int) -> list:
    return ["sudo", "-n", SETTIME_HELPER, str(int(epoch))]


def run_argv(argv) -> Tuple[int, str]:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    return (p.returncode, (p.stdout or "") + (p.stderr or ""))


def apply_time(epoch: int, run: Callable = run_argv) -> Tuple[bool, str]:
    """Set the clock through the root helper. (ok, what it printed). The
    helper prints `date -u` afterwards, which is the read-back."""
    try:
        code, out = run(settime_argv(epoch))
    except Exception as e:                                         # noqa: BLE001
        return (False, str(e))
    return (code == 0, (out or "").strip())


def parse_ntp_synchronized(text: str) -> Optional[bool]:
    s = (text or "").strip().lower()
    if s == "yes":
        return True
    if s == "no":
        return False
    return None


def ntp_synchronized(run: Callable = run_argv) -> Optional[bool]:
    """timedatectl's own word; None when it could not be read (which is
    NOT "synchronised" — the asker keeps asking). A shell call: run it on
    a worker thread, never on the packet callback or the heartbeat."""
    try:
        code, out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    except Exception:                                              # noqa: BLE001
        return None
    if code != 0:
        return None
    return parse_ntp_synchronized(out)


# -- the ask schedule ----------------------------------------------------------------------

def ask_interval_s(last_set_at: Optional[float], consecutive_refusals: int) -> float:
    """How long between asks: hourly once ASK_BACKOFF_AFTER identical
    refusals are in a row; RE_ASK_S once a TIME was applied; ASK_EVERY_S
    before that."""
    if int(consecutive_refusals or 0) >= ASK_BACKOFF_AFTER:
        return float(ASK_BACKOFF_S)
    if last_set_at is not None:
        return float(RE_ASK_S)
    return float(ASK_EVERY_S)


def should_ask(now: float, started_at: float, last_ask_at: Optional[float],
               last_set_at: Optional[float], ntp_synced: Optional[bool],
               consecutive_refusals: int = 0) -> bool:
    """Ask after the grace, then on ask_interval_s, while NTP is not
    synchronised. No latch: a set clock re-asks after RE_ASK_S. An
    unreadable NTP state does not count as synchronised. All stamps are
    the SAME monotonic clock — a backwards wall-clock set must never
    silence the asker (review, 2026-09-23)."""
    if ntp_synced is True:
        return False
    if now - started_at < ASK_GRACE_S:
        return False
    if last_ask_at is None:
        return True
    return now - last_ask_at >= ask_interval_s(last_set_at, consecutive_refusals)


class TimeState:
    """What the reporter remembers between the asker and the 0x05 handler.
    Every stamp is time.monotonic() — never a wall-clock deadline (a
    backwards set once silenced the heartbeat for the size of the jump)."""
    def __init__(self):
        self.last_set_at: Optional[float] = None     # monotonic, when a TIME was applied
        self.last_ask_at: Optional[float] = None     # monotonic
        self.ntp_synced: Optional[bool] = None       # last reading, for the log
        self.consecutive_refusals = 0
        self.last_refusal: Optional[str] = None
        self.issued = IssuedNonces()
        #: (nonce, before, status) of an ack that could not be sent: retried
        #: once after the next announce (review item 12).
        self.pending_ack: Optional[Tuple[bytes, int, int]] = None
        self._lock = threading.Lock()

    def note_refusal(self, reason: str) -> None:
        """Identical reasons in a row count towards the backoff; a new
        reason starts the count again."""
        with self._lock:
            if reason == self.last_refusal:
                self.consecutive_refusals += 1
            else:
                self.last_refusal, self.consecutive_refusals = reason, 1

    def note_success(self) -> None:
        with self._lock:
            self.last_refusal, self.consecutive_refusals = None, 0


# -- a TIME arrived ----------------------------------------------------------------

def handle_time(node_dest: bytes, payload: bytes, rns, trust_path: str = TRUST_PATH,
                now: Callable[[], float] = None, run: Callable = run_argv,
                log: Callable[[str], None] = None, ntp_synced: Optional[bool] = None,
                state_path: str = TIME_STATE_PATH, issued: Optional[IssuedNonces] = None
                ) -> Optional[Tuple[bytes, int, int]]:
    """A 0x05 landed on the node's health destination. Returns (nonce,
    before_s, status) for the TIME_ACK, or None when nothing should be
    acknowledged: no trust file, the medic's identity not recalled yet,
    another signer, a malformed packet, or an insane epoch. The order of
    the checks, each said in *log* (the reporter puts it in the journal
    at NOTICE):

      trust → signature → sanity → floor (status 4) → NTP (status 3) →
      slop (status 0) → helper (status 1 / 2).

    *ntp_synced* is timedatectl's word as read by the caller on a worker
    thread; True means the clock is NEVER moved by a TIME."""
    import time as _time
    _now = now or _time.time
    _log = log or (lambda m: None)
    trust = load_trust(trust_path)
    if trust is None:
        _log("TIME ignored: no trusted medic on file (%s)" % trust_path)
        return None
    ident = recall_trusted_medic(rns, trust)
    if ident is None:
        _log("TIME ignored: trusted medic %s not recalled yet (no announce heard "
             "from its reply destination %s)" % (trust["identity_hash"][:8],
                                                 trust["reply_dest"][:8]))
        return None
    got = verify_time(node_dest, payload, ident)
    if got is None:
        _log("TIME ignored: not signed by the trusted medic for this node")
        return None
    medic_time, nonce = got
    medic = trust["identity_hash"][:8]
    if not epoch_is_sane(medic_time):
        _log("TIME ignored: epoch %d outside 2026-01-01..2100-01-01" % medic_time)
        return None
    kind = "asked" if (issued is not None and issued.was_issued(nonce)) else "pushed"
    before = int(_now())
    floor = load_last_applied(state_path)
    if not passes_floor(medic_time, floor):
        _log("TIME (%s) from medic %s refused: epoch %d is not newer than the last "
             "applied (%d) - a replay or a stale medic" % (kind, medic, medic_time, floor))
        return (nonce, before, TIME_STATUS_REFUSED_STALE)
    if ntp_synced is True:
        _log("TIME (%s) from medic %s refused: this clock is NTP-synchronised "
             "(medic says %+d s) - not moved" % (kind, medic, medic_time - before))
        return (nonce, before, TIME_STATUS_REFUSED_NTP)
    if not should_apply(medic_time, before):
        _log("TIME (%s) from medic %s: clock already within %d s (delta %+d s) - not moved"
             % (kind, medic, CLOCK_SLOP_S, medic_time - before))
        return (nonce, before, TIME_STATUS_NOT_NEEDED)
    ok, out = apply_time(medic_time, run)
    if ok:
        remembered = save_last_applied(medic_time, state_path)
        _log("TIME (%s) from medic %s: clock set to %d (was %+d s off); helper says: %s%s"
             % (kind, medic, medic_time, before - medic_time, out,
                "" if remembered else " (floor NOT persisted: %s unwritable)" % state_path))
        return (nonce, before, TIME_STATUS_SET)
    _log("TIME (%s) from medic %s: clock NOT set - helper failed: %s"
         % (kind, medic, out or "no output"))
    return (nonce, before, TIME_STATUS_HELPER_FAILED)
