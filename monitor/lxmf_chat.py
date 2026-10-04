"""In-medic chat — the message store (pure, no Kivy, no RNS).

The medic has always been the mesh's propagation node; from 2026-09-29 it is also a
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
POSTED = "posted"          # no path — waiting at a propagation node for the peer
FAILED = "failed"          # nothing on the mesh answered to that address
STATES = (SENDING, SENT, DELIVERED, POSTED, FAILED)

#: What the screen prints beside each state. Plain words — the person at the
#: panel is not an LXMF developer.
STATE_WORDS = {
    SENDING: "sending",
    SENT: "sent",
    DELIVERED: "delivered",
    POSTED: "held here until they're back online",
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


#: LXMF field numbers (LXMF/LXMF.py). Known so a textless message can be
#: named in words; anything else is shown by number.
FIELD_NAMES = {0x01: "embedded messages", 0x02: "telemetry", 0x03: "telemetry stream",
               0x04: "icon", 0x05: "file", 0x06: "image", 0x07: "audio",
               0x08: "thread", 0x09: "commands", 0x0A: "results", 0x0B: "group",
               0x0C: "ticket", 0x0D: "event", 0x0E: "RNR refs", 0x0F: "renderer"}


def readable(text: str) -> str:
    """Text the medic's fonts can draw. Roboto and DejaVu carry no emoji;
    Kivy draws a missing glyph as NOTHING, so a 👍 arrived as a blank. Each
    emoji becomes its Unicode name in brackets — "[thumbs up sign]" — which
    is honest, offline, and needs no new font."""
    import unicodedata
    out = []
    for ch in text or "":
        cp = ord(ch)
        if cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0x1F1E6 <= cp <= 0x1F1FF:
            name = unicodedata.name(ch, "").lower()
            out.append("[%s]" % name if name else "[?]")
        elif cp in (0xFE0F, 0x200D):
            continue                           # variation selector / joiner
        else:
            out.append(ch)
    return "".join(out)


#: LXMF/LXMF.py (1.0.1): the reply and reaction fields and their dict keys.
FIELD_REPLY_TO, FIELD_REPLY_QUOTE, FIELD_REACTION = 0x30, 0x31, 0x40
REACTION_TO, REACTION_CONTENT = 0x00, 0x01


def _utf8(v) -> Optional[str]:
    if isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def describe_fields(fields: dict, title: str = "", text_of=None) -> str:
    """Words for a message that has no text: a reaction, an attachment.

    A Columba thumbs-up is FIELD_REACTION {REACTION_TO: hash, REACTION_CONTENT:
    b"\xf0\x9f\x91\x8d"} and nothing else; it drew as an empty bubble
    (2026-10-01). *text_of* (msg_id hex → text, or None) lets the reaction
    name the message it was on."""
    if title and title.strip():
        return readable(title.strip())
    if not fields:
        return "(an empty message)"
    parts = []
    for k, v in fields.items():
        if k == FIELD_REACTION and isinstance(v, dict):
            emoji = readable(_utf8(v.get(REACTION_CONTENT)) or "?")
            target = v.get(REACTION_TO)
            quoted = None
            if text_of is not None and isinstance(target, (bytes, bytearray)):
                quoted = text_of(bytes(target).hex())
            if quoted:
                parts.append("%s to \u201c%s\u201d" % (emoji, preview(quoted, 40)))
            else:
                parts.append("%s to an earlier message" % emoji)
        elif k in (FIELD_REPLY_TO, FIELD_REPLY_QUOTE):
            continue                          # context for a text, not content
        elif k in FIELD_NAMES:
            parts.append(FIELD_NAMES[k])
        else:
            u = _utf8(v)
            parts.append(readable(u) if (u and len(u) <= 64) else "field %s" % k)
    return "(" + ", ".join(parts) + ")" if parts else "(an empty message)"


def escape_markup(text) -> str:
    """Kivy markup-safe text — the same three substitutions Kivy's own
    ``escape_markup`` makes. A peer name or message containing "[size=x]"
    reached a markup Label raw and Kivy raised while drawing the CHAT list,
    on every open, forever (readiness sweep, 2026-10-03)."""
    return (str(text or "").replace("&", "&amp;").replace("[", "&bl;")
            .replace("]", "&br;"))


def preview(text: str, limit: int = 44) -> str:
    """One line of the last message for a conversation row: newlines folded,
    cut on a word with an ellipsis — the row is a fixed-height key and a
    wrapped preview was cut mid-word on the glass ("proves the badge a",
    2026-09-30)."""
    t = " ".join(readable(text or "").split())
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
        if self._loaded and self._mtime() == self._disk_mtime:
            return                     # a DELETED file (mtime 0) is a change too
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

    def text_of(self, msg_id: str) -> Optional[str]:
        """The text of a stored message by its id (LXMF hash hex), or None."""
        with self._lock:
            self._ensure_loaded()
            rec = self._find(msg_id)
            return rec.get("text") if rec else None

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

    def set_state(self, msg_id: str, state: str, via: Optional[str] = None) -> bool:
        """Move a message to *state*. *via* names the node now holding it (a
        POSTED message), kept on the record so the screen can say which one."""
        if state not in STATES:
            raise ValueError(state)
        with self._lock:
            self._ensure_loaded()
            rec = self._find(msg_id)
            if rec is None:
                return False
            changed = rec.get("state") != state
            if via is not None and rec.get("via") != via:
                rec["via"] = via
                changed = True
            if not changed:
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

    def delete_conversation(self, peer: str) -> int:
        """Remove every message with *peer* (press-and-hold → Delete on the
        list, operator 2026-10-01). The peer itself stays known: deleting a
        chat is not forgetting who is on the mesh. Returns how many went."""
        with self._lock:
            self._ensure_loaded()
            before = len(self._messages)
            self._messages = [m for m in self._messages if m["peer"] != peer]
            n = before - len(self._messages)
            if n:
                self._save_messages()
            return n

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

    def contacts(self, limit: int = 40, exclude=()) -> List[dict]:
        """Addresses this medic already knows, most useful first: the peer of
        every conversation (newest first), then every other peer heard
        announcing. ``{hash, name}`` rows; *name* falls back to the short
        hash. Settings ▸ Notifications offers these so the operator's own
        phone can be picked instead of typed (operator, 2026-10-04)."""
        skip = {str(x).lower() for x in exclude if x}
        out, seen = [], set()
        for c in self.conversations():
            if c.peer in seen or c.peer.lower() in skip:
                continue
            seen.add(c.peer)
            out.append({"hash": c.peer, "name": c.name})
        for p in self.peers():
            h = p["hash"]
            if h in seen or h.lower() in skip:
                continue
            seen.add(h)
            out.append({"hash": h, "name": p.get("name") or short_hash(h)})
        return out[:limit]
