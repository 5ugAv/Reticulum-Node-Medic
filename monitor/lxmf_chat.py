"""In-medic chat — the message store (pure, no Kivy, no RNS).

The medic has always been the mesh's post office; from 2026-09-29 it is also a
messenger of its own (operator: "a section that is chat, like functional
chat — typing messages and receiving messages"). Sideband was weighed and
turned down: a second full Kivy app with its own window, identity and pip
tree, on a five-inch panel one Kivy app already fills. LXMF itself is already
on the medic (lxmd runs beside the UI), so CHAT is built on it directly.

This module is the on-disk truth the screen reads and the service writes:

* ``messages.json`` — every message, in or out, with its delivery state;
* ``peers.json``    — every LXMF peer heard announcing, with its display name.

Both live under ``~/.reticulum-node-medic/chat`` and are written atomically
(a field power cut mid-write must never eat the conversation). The service
calls in from RNS threads and the screen reads from the main thread, so every
mutation takes the lock and bumps ``version`` — the screen polls that integer
instead of re-reading files.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from monitor.atomic_json import write_json

CHAT_DIR = os.path.expanduser("~/.reticulum-node-medic/chat")

#: Delivery states of an OUTGOING message, in the order a message walks them.
SENDING = "sending"        # handed to the router, on its way
SENT = "sent"              # left this medic (direct link accepted it)
DELIVERED = "delivered"    # the peer's LXMF confirmed receipt
POSTED = "posted"          # no path — held at the post office for the peer
FAILED = "failed"          # nothing on the mesh answered to that address
STATES = (SENDING, SENT, DELIVERED, POSTED, FAILED)

#: What the screen prints beside each state. Plain words — the person at the
#: panel is not an LXMF developer.
STATE_WORDS = {
    SENDING: "sending",
    SENT: "sent",
    DELIVERED: "delivered",
    POSTED: "held at the post office",
    FAILED: "not delivered — nobody on the mesh answered to that address. "
            "It goes again the moment they are heard.",
}

IN = "in"
OUT = "out"


@dataclass
class Conversation:
    peer: str            # 32-hex LXMF delivery hash
    name: str            # display name if announced, else a short hash
    last_text: str
    last_ts: float
    unread: int


def preview(text: str, limit: int = 44) -> str:
    """One line of the last message for a conversation row: newlines folded,
    cut on a word with an ellipsis — the row is a fixed-height key and a
    wrapped preview was cut mid-word on the glass ("proves the badge a",
    2026-09-30)."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0] or t[:limit]
    return cut + "…"


def short_hash(peer: str) -> str:
    return (peer or "")[:8]


def _load(path: str, default):
    try:
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class MessageStore:
    def __init__(self, path: str = CHAT_DIR):
        self.path = path
        self._msg_file = os.path.join(path, "messages.json")
        self._peer_file = os.path.join(path, "peers.json")
        self._lock = threading.RLock()
        self._messages: List[dict] = []
        self._peers: Dict[str, dict] = {}
        self._loaded = False
        #: mtime of messages.json as last read or written by THIS instance —
        #: a newer one on disk means another process wrote (a CLI, a
        #: walkthrough plant, a future importer) and we re-read. Without it
        #: the running app sat on an empty cache while a message planted
        #: from a shell never showed (2026-09-30).
        self._disk_mtime = 0.0
        #: bumped on every change; the screen compares, never re-reads
        self.version = 0

    # -- persistence ---------------------------------------------------------

    def _mtime(self) -> float:
        try:
            return os.stat(self._msg_file).st_mtime
        except OSError:
            return 0.0

    def _ensure_loaded(self):
        if self._loaded and self._mtime() <= self._disk_mtime:
            return
        if self._loaded:
            self.version += 1          # someone else wrote; the screen must look
        self._disk_mtime = self._mtime()
        raw = _load(self._msg_file, [])
        self._messages = [m for m in raw if isinstance(m, dict) and m.get("id")]
        raw_p = _load(self._peer_file, {})
        self._peers = {k: v for k, v in raw_p.items()
                       if isinstance(v, dict)} if isinstance(raw_p, dict) else {}
        self._loaded = True

    def _save_messages(self):
        os.makedirs(self.path, exist_ok=True)
        write_json(self._msg_file, self._messages, mode=0o600)
        self._disk_mtime = self._mtime()
        self.version += 1

    def _save_peers(self):
        os.makedirs(self.path, exist_ok=True)
        write_json(self._peer_file, self._peers, mode=0o600)
        self.version += 1

    def poll(self) -> int:
        """Look at the disk, then say the version. The screen and the badge
        compare versions to decide whether to redraw — but a file another
        process wrote is only noticed INSIDE a read, so a version compared
        before any read never moved (the front-page badge, 2026-09-30)."""
        with self._lock:
            self._ensure_loaded()
            return self.version

    # -- messages ------------------------------------------------------------

    def _find(self, msg_id: str) -> Optional[dict]:
        for m in self._messages:
            if m["id"] == msg_id:
                return m
        return None

    def add_incoming(self, peer: str, text: str, ts: Optional[float] = None,
                     msg_id: Optional[str] = None) -> Optional[dict]:
        """Record a message that arrived. Returns the record, or None when the
        same LXMF message (by its hash) was already stored — a propagation-node
        sync can re-deliver what a direct link already brought."""
        with self._lock:
            self._ensure_loaded()
            mid = msg_id or uuid.uuid4().hex
            if self._find(mid) is not None:
                return None
            rec = {"id": mid, "peer": peer, "dir": IN, "text": text,
                   "ts": float(ts if ts is not None else time.time()),
                   "state": DELIVERED, "read": False}
            self._messages.append(rec)
            self._touch_peer(peer, seen=rec["ts"])
            self._save_messages()
            return rec

    def add_outgoing(self, peer: str, text: str, ts: Optional[float] = None,
                     msg_id: Optional[str] = None) -> dict:
        with self._lock:
            self._ensure_loaded()
            rec = {"id": msg_id or uuid.uuid4().hex, "peer": peer, "dir": OUT,
                   "text": text, "ts": float(ts if ts is not None else time.time()),
                   "state": SENDING, "read": True}
            self._messages.append(rec)
            self._touch_peer(peer)
            self._save_messages()
            return rec

    def set_state(self, msg_id: str, state: str) -> bool:
        if state not in STATES:
            raise ValueError(state)
        with self._lock:
            self._ensure_loaded()
            rec = self._find(msg_id)
            if rec is None or rec.get("state") == state:
                return False
            rec["state"] = state
            self._save_messages()
            return True

    def fail_stuck_sending(self) -> int:
        """At start: whatever was still 'sending' when the last session ended
        never got its callback (MeshChat does the same sweep). Returns the count."""
        with self._lock:
            self._ensure_loaded()
            n = 0
            for m in self._messages:
                if m.get("dir") == OUT and m.get("state") == SENDING:
                    m["state"] = FAILED
                    n += 1
            if n:
                self._save_messages()
            return n

    def failed_to(self, peer: str) -> List[dict]:
        """Our messages to *peer* that never got through, oldest first."""
        with self._lock:
            self._ensure_loaded()
            return sorted((dict(m) for m in self._messages
                           if m["peer"] == peer and m.get("dir") == OUT
                           and m.get("state") == FAILED), key=lambda m: m["ts"])

    def thread(self, peer: str) -> List[dict]:
        with self._lock:
            self._ensure_loaded()
            return sorted((dict(m) for m in self._messages if m["peer"] == peer),
                          key=lambda m: m["ts"])

    def mark_read(self, peer: str) -> int:
        with self._lock:
            self._ensure_loaded()
            n = 0
            for m in self._messages:
                if m["peer"] == peer and not m.get("read"):
                    m["read"] = True
                    n += 1
            if n:
                self._save_messages()
            return n

    def unread_total(self) -> int:
        with self._lock:
            self._ensure_loaded()
            return sum(1 for m in self._messages if not m.get("read"))

    def conversations(self) -> List[Conversation]:
        with self._lock:
            self._ensure_loaded()
            by_peer: Dict[str, dict] = {}
            unread: Dict[str, int] = {}
            for m in self._messages:
                p = m["peer"]
                if p not in by_peer or m["ts"] >= by_peer[p]["ts"]:
                    by_peer[p] = m
                if not m.get("read"):
                    unread[p] = unread.get(p, 0) + 1
            convs = [Conversation(peer=p, name=self.peer_name(p),
                                  last_text=m["text"], last_ts=m["ts"],
                                  unread=unread.get(p, 0))
                     for p, m in by_peer.items()]
            return sorted(convs, key=lambda c: c.last_ts, reverse=True)

    # -- peers ---------------------------------------------------------------

    def _touch_peer(self, peer: str, seen: Optional[float] = None):
        p = self._peers.setdefault(peer, {"name": "", "seen": 0.0})
        if seen is not None and seen > p.get("seen", 0.0):
            p["seen"] = float(seen)

    def remember_peer(self, peer: str, name: str = "",
                      seen: Optional[float] = None) -> None:
        """An LXMF peer announced itself (or wrote to us)."""
        with self._lock:
            self._ensure_loaded()
            p = self._peers.setdefault(peer, {"name": "", "seen": 0.0})
            if name:
                p["name"] = str(name)[:64]
            p["seen"] = float(seen if seen is not None else time.time())
            self._save_peers()

    def peer_name(self, peer: str) -> str:
        with self._lock:
            self._ensure_loaded()
            n = (self._peers.get(peer) or {}).get("name") or ""
            return n or short_hash(peer)

    def peers(self) -> List[dict]:
        """Every peer heard, newest first: {hash, name, seen}."""
        with self._lock:
            self._ensure_loaded()
            rows = [{"hash": h, "name": v.get("name", ""), "seen": v.get("seen", 0.0)}
                    for h, v in self._peers.items()]
            return sorted(rows, key=lambda r: r["seen"], reverse=True)
