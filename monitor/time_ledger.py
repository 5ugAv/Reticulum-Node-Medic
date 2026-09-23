"""The medic's record of the time it gave each node (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23).

One small JSON under ~/.reticulum-node-medic/: node health destination ->
{sent_at, acked_at, before, applied, delta_s, why}. The node page reads
ONE honest line from it (ui/clock_line.py) and the unasked push consults
it for the six-hour cadence. Pure: the clock and the path are injected.

A "set" is claimed only from an ACK with applied=1 — never from a send.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Optional, Tuple

LEDGER_PATH = "~/.reticulum-node-medic/time_ledger.json"
#: After a verified health reply, push a TIME unasked if none went to that
#: node in this long. The node ignores it when within 30 s (acks applied=0),
#: so the cost is one 81-byte packet per node per six hours (2026-09-23).
TIME_PUSH_EVERY_S = 6 * 3600


def _blank() -> dict:
    return {"sent_at": None, "sent_time": None, "acked_at": None,
            "before": None, "applied": None, "delta_s": None, "why": None}


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
        except (OSError, ValueError):
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

    def record_sent(self, dest_hex: str, medic_time_s: int, why: str = "asked") -> dict:
        """A TIME went out. Clears nothing about a previous ack: the page
        line rests on acked_at/applied, which only record_ack writes."""
        with self._lock:
            row = self._rows.setdefault((dest_hex or "").lower(), _blank())
            row["sent_at"] = self._now()
            row["sent_time"] = int(medic_time_s)
            row["why"] = why
            self._save()
            return dict(row)

    def record_ack(self, dest_hex: str, before_s: int, applied: bool) -> dict:
        """The node's word: what its clock read before, and whether it moved.
        delta_s = the time we sent minus the node's `before` — how far off it
        WAS; None when no send is on record (an ack out of the blue)."""
        with self._lock:
            row = self._rows.setdefault((dest_hex or "").lower(), _blank())
            row["acked_at"] = self._now()
            row["before"] = int(before_s)
            row["applied"] = bool(applied)
            row["delta_s"] = (None if row.get("sent_time") is None
                              else int(row["sent_time"]) - int(before_s))
            self._save()
            return dict(row)

    def should_push(self, dest_hex: str, every_s: float = TIME_PUSH_EVERY_S) -> bool:
        with self._lock:
            row = self._rows.get((dest_hex or "").lower())
            if not row or row.get("sent_at") is None:
                return True
            return self._now() - float(row["sent_at"]) >= every_s


def clock_state(entry: Optional[dict]) -> Tuple[str, Optional[float], Optional[float]]:
    """("set" | "checked" | "none", acked_at, delta_s) — "set" ONLY from an
    ack with applied=1, "checked" from an ack with applied=0, "none" for a
    send with no ack (the page never claims a set nobody confirmed)."""
    if not entry or entry.get("acked_at") is None:
        return ("none", None, None)
    if entry.get("applied") is True:
        return ("set", entry["acked_at"], entry.get("delta_s"))
    return ("checked", entry["acked_at"], entry.get("delta_s"))
