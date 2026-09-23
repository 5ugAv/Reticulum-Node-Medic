"""The medic's record of the time it gave each node (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23; revised after review the same day).

One small JSON under ~/.reticulum-node-medic/: node health destination ->
{sent_at, sent_time, nonce, tried_at, acked_at, before, status, applied,
delta_s, why, refused_at, refused_why, sends_since_ack}. The node page
reads ONE honest line from it (ui/clock_line.py) and the unasked push
consults it for the six-hour cadence. Pure: the clock and the path are
injected.

A "set" is claimed only from an ACK with status SET — never from a send.
The row keeps the NONCE of the last send so an ack that lands after the
UI restarted (the in-memory pending table is gone) still matches its send.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Optional, Tuple

from monitor.health_reply import TIME_STATUS_SET

LEDGER_PATH = "~/.reticulum-node-medic/time_ledger.json"
#: After a verified health reply, push a TIME unasked if none went to that
#: node in this long. The node ignores it when within 30 s (acks status 0),
#: so the cost is one 81-byte packet per node per six hours (2026-09-23).
#: Opportunistic: a unicast reply only arrives when the operator pinged
#: the node, so this is a bonus on top of the node's own 24 h re-ask.
TIME_PUSH_EVERY_S = 6 * 3600
#: An ack older than this, with a later send unanswered, no longer
#: vouches for the node's clock on the page (the node may have rebooted).
ACK_STALE_AFTER_S = 24 * 3600


def _blank() -> dict:
    return {"sent_at": None, "sent_time": None, "nonce": None, "tried_at": None,
            "acked_at": None, "before": None, "status": None, "applied": None,
            "delta_s": None, "why": None, "refused_at": None, "refused_why": None,
            "sends_since_ack": 0}


class TimeLedger:
    def __init__(self, path: Optional[str] = None, now: Callable[[], float] = time.time):
        self._path = os.path.expanduser(path or LEDGER_PATH)
        self._now = now
        self._lock = threading.Lock()
        self._rows: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._rows = {k: dict(_blank(), **v) for k, v in data.items()
                              if isinstance(v, dict)}
        except (OSError, ValueError, TypeError):
            # A corrupt ledger is an empty ledger: the only claim it backs
            # is a "set" line on the node page, and no line is the honest
            # fallback (2026-09-23).
            self._rows = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._rows, fh, sort_keys=True, indent=2)
                fh.write("\n")
            os.replace(tmp, self._path)
        except OSError:
            pass

    def entry(self, dest_hex: str) -> Optional[dict]:
        with self._lock:
            row = self._rows.get((dest_hex or "").lower())
            return dict(row) if row else None

    def record_tried(self, dest_hex: str) -> dict:
        """A send is about to be ATTEMPTED (before recall and the path warm).
        Gates should_push so a node whose sends keep failing is not retried
        on every reply (review, 2026-09-23)."""
        with self._lock:
            row = self._rows.setdefault((dest_hex or "").lower(), _blank())
            row["tried_at"] = self._now()
            self._save()
            return dict(row)

    def record_refused(self, dest_hex: str, why: str) -> dict:
        """The medic would not sign: its own clock is not disciplined."""
        with self._lock:
            row = self._rows.setdefault((dest_hex or "").lower(), _blank())
            row["refused_at"] = self._now()
            row["refused_why"] = str(why or "")
            self._save()
            return dict(row)

    def record_sent(self, dest_hex: str, medic_time_s: int, why: str = "asked",
                    nonce_hex: Optional[str] = None) -> dict:
        """A TIME went out — call AFTER .send() returned without raising.
        Clears nothing about a previous ack: the page line rests on
        acked_at/status, which only record_ack writes."""
        with self._lock:
            row = self._rows.setdefault((dest_hex or "").lower(), _blank())
            row["sent_at"] = self._now()
            row["sent_time"] = int(medic_time_s)
            row["why"] = why
            row["nonce"] = (nonce_hex or "").lower() or None
            row["sends_since_ack"] = int(row.get("sends_since_ack") or 0) + 1
            self._save()
            return dict(row)

    def record_ack(self, dest_hex: str, before_s: int, status: int) -> dict:
        """The node's word: what its clock read before, and the status byte.
        delta_s = the time we sent minus the node's `before` — how far off it
        WAS; None when no send is on record (an ack out of the blue)."""
        with self._lock:
            row = self._rows.setdefault((dest_hex or "").lower(), _blank())
            row["acked_at"] = self._now()
            row["before"] = int(before_s)
            row["status"] = int(status)
            row["applied"] = int(status) == TIME_STATUS_SET
            row["delta_s"] = (None if row.get("sent_time") is None
                              else int(row["sent_time"]) - int(before_s))
            row["sends_since_ack"] = 0
            # The nonce is spent: a second ack carrying it (a replay the
            # transport's packet-hash list did not catch, or a node's
            # retry of an ack the medic already has) must read as "a send
            # this medic does not remember", not refresh acked_at.
            row["nonce"] = None
            self._save()
            return dict(row)

    def dest_for_nonce(self, nonce_hex: str) -> Optional[str]:
        """The destination whose LAST send carried this nonce, or None —
        how an ack is matched after a UI restart emptied the pending table."""
        n = (nonce_hex or "").lower()
        if not n:
            return None
        with self._lock:
            for dest, row in self._rows.items():
                if row.get("nonce") == n:
                    return dest
        return None

    def should_push(self, dest_hex: str, every_s: float = TIME_PUSH_EVERY_S) -> bool:
        """No send AND no try to this node in *every_s*: a try that failed
        (no path, undisciplined clock) counts, or a mute node would be
        retried on every reply it sends."""
        with self._lock:
            row = self._rows.get((dest_hex or "").lower())
            if not row:
                return True
            stamps = [float(row[k]) for k in ("sent_at", "tried_at")
                      if _is_number(row.get(k))]
            if not stamps:
                return True
            return self._now() - max(stamps) >= every_s


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num(v) -> Optional[float]:
    """A float, or None for anything a malformed row could hold."""
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def clock_state(entry: Optional[dict], now: Optional[float] = None,
                stale_after_s: float = ACK_STALE_AFTER_S) -> Tuple[str, Optional[float], Optional[float], dict]:
    """(state, acked_at, delta_s, extra) — what the ledger can BACK:

      "set"      an ack with status SET;
      "checked"  an ack with status NOT_NEEDED (already right);
      "failed"   an ack with status HELPER_FAILED (the node could not set it);
      "ntp"      an ack with status REFUSED_NTP (the node keeps NTP time);
      "stale"    the last ack is older than *stale_after_s* AND a later
                 send or try went unanswered — extra["sends"] says how many
                 sends since, extra["tried"] whether a try that never became
                 a send is the later one;
      "none"     no ack at all (a send with no ack claims nothing).

      "older"    an ack with status REFUSED_STALE: the node refused a time
                 not newer than the last it applied — a replay, or Node
                 Medic's own clock behind the node's. NOT "already right":
                 nothing about the node's clock was checked (2026-09-23).

    Old rows (before the status byte) carry only `applied`; it is read as
    SET / NOT_NEEDED. Never raises on a malformed row."""
    if not isinstance(entry, dict):
        return ("none", None, None, {})
    acked_at = _num(entry.get("acked_at"))
    if acked_at is None:
        return ("none", None, None, {})
    delta = _num(entry.get("delta_s"))
    status = entry.get("status")
    if status is None or isinstance(status, bool):
        status = 1 if entry.get("applied") is True else 0
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0
    later = [t for t in (_num(entry.get("sent_at")), _num(entry.get("tried_at")))
             if t is not None and t > acked_at]
    if now is not None and later and _num(now) is not None \
            and float(now) - acked_at > float(stale_after_s):
        sends = entry.get("sends_since_ack")
        sends = int(sends) if _is_number(sends) else 0
        return ("stale", acked_at, delta, {"sends": sends, "tried": sends == 0})
    if status == 1:
        return ("set", acked_at, delta, {})
    if status == 2:
        return ("failed", acked_at, delta, {})
    if status == 3:
        return ("ntp", acked_at, delta, {})
    if status == 4:
        return ("older", acked_at, delta, {})
    return ("checked", acked_at, delta, {})
