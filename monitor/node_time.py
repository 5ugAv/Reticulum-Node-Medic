"""Time over the mesh — the NODE's side (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23).

A Pi propagation node on a solar bank dies overnight and boots again in the
sun with no correct time: no RTC, no internet in the field, no NTP. A
wrong clock corrupts its own logs and LXMF message expiry and misleads
VITALS. So the node ASKS its medic for the time over LoRa (TIME_REQ), the
medic answers with a SIGNED time (TIME), and the node sets its clock
through one tiny root helper and says what it did (TIME_ACK).

This module is the policy, pure: the trust anchor, the sanity window, the
30 s slop, the helper invocation, the ask schedule. It runs ON THE NODE
(shipped in the reporter package, workflows.build._HEALTH_MODULES) and is
imported by monitor.pi_health_reporter, which owns the RNS calls. Every
outside effect — the clock, timedatectl, the packets — is injected, so all
of it is tested with plain values and fake RNS. Nothing in here may accept
a time from anyone but the trusted medic.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Optional, Tuple

from monitor.health_reply import verify_time

#: Written at birth (workflows.build.install_time_trust) and by the reporter
#: push: which medic this node believes about the time. Keyed by the medic's
#: health-reply identity hash; reply_dest is how the node recalls that
#: identity from an announce it heard.
TRUST_PATH = "~/.rnm-health/trusted_medic.json"
#: The ONLY road to the clock. The reporter runs as the build user; this
#: helper (installed 0755 root, sudoers NOPASSWD for that one path) checks
#: its argument and runs `date -s` — nothing else.
SETTIME_HELPER = "/usr/local/sbin/nm-settime"
#: A single unicast packet crosses N LoRa hops at ~1.8 kbps and lands
#: seconds late (roughly 1-3 s per hop). Moving the clock for less than
#: this would chase the latency, not the truth; logs, LXMF expiry and
#: VITALS are all fine at ±30 s (2026-09-23).
CLOCK_SLOP_S = 30
#: Sanity window for a time anyone offers: 2026-01-01 .. 2100-01-01 UTC.
#: Before the first is a clock that has not been set; after the second is
#: nonsense. Both are exactly 10 digits, which the helper also insists on.
EPOCH_MIN = 1767225600
EPOCH_MAX = 4102444800
#: On reporter start, wait this long before asking — NTP may still fix
#: the clock on a node that does have internet (2026-09-23).
ASK_GRACE_S = 90
#: ...then ask this often while timedatectl says NTP is not synchronised.
ASK_EVERY_S = 600

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
    NOT "synchronised" — the asker keeps asking)."""
    try:
        code, out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    except Exception:                                              # noqa: BLE001
        return None
    if code != 0:
        return None
    return parse_ntp_synchronized(out)


def should_ask(now: float, started_at: float, last_ask_at: Optional[float],
               clock_set: bool, ntp_synced: Optional[bool]) -> bool:
    """Ask after the grace, then every ASK_EVERY_S, until the clock was set
    over the mesh or NTP reports synchronised. An unreadable NTP state
    does not count as synchronised."""
    if clock_set or ntp_synced is True:
        return False
    if now - started_at < ASK_GRACE_S:
        return False
    if last_ask_at is None:
        return True
    return now - last_ask_at >= ASK_EVERY_S


class TimeState:
    """What the reporter remembers between the asker and the 0x05 handler:
    whether the clock was set by the medic (stop asking) and when it last
    asked."""
    def __init__(self):
        self.clock_set = False
        self.last_ask_at: Optional[float] = None


# -- a TIME arrived ----------------------------------------------------------------

def handle_time(node_dest: bytes, payload: bytes, rns, trust_path: str = TRUST_PATH,
                now: Callable[[], float] = None, run: Callable = run_argv,
                log: Callable[[str], None] = None
                ) -> Optional[Tuple[bytes, int, bool]]:
    """A 0x05 landed on the node's health destination. Returns (nonce,
    before_s, applied) for the TIME_ACK, or None when nothing should be
    acknowledged: no trust file, the medic's identity not recalled yet,
    another signer, a malformed packet, or an insane epoch. Every outcome
    is said in *log* (the reporter puts it in the journal at NOTICE)."""
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
    if not epoch_is_sane(medic_time):
        _log("TIME ignored: epoch %d outside 2026-01-01..2100-01-01" % medic_time)
        return None
    before = int(_now())
    if not should_apply(medic_time, before):
        _log("TIME from medic %s: clock already within %d s (delta %+d s) - not moved"
             % (trust["identity_hash"][:8], CLOCK_SLOP_S, medic_time - before))
        return (nonce, before, False)
    ok, out = apply_time(medic_time, run)
    if ok:
        _log("TIME from medic %s: clock set to %d (was %+d s off); helper says: %s"
             % (trust["identity_hash"][:8], medic_time, before - medic_time, out))
    else:
        _log("TIME from medic %s: clock NOT set - helper failed: %s"
             % (trust["identity_hash"][:8], out or "no output"))
    return (nonce, before, bool(ok))
