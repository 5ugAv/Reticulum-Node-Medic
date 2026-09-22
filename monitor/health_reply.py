"""The unicast health reply — the pure half (design:
docs/HEALTH_REPLY_UNICAST.md, 2026-09-21, revised after review).

"Ping node now" used to ask a node to ANNOUNCE its beacon, which only the
medic's own radio could hear in time. Now the request says "speak to me,
about this": it carries the medic's reply destination and a nonce, and
the node answers with a packet to that destination — naming itself,
echoing the nonce, signed with its identity — routed back through the
mesh like any message. This module holds the wire contract, the node-side
predicate, the wait arithmetic and the pending-poll table; nothing here
touches RNS or the screen, so all of it is tested with plain bytes.

Wire:
  request = 0x04 | reply_dest[16] | nonce[8]
  reply   = node_dest[16] | nonce[8] | beacon[N] | sig[64]
            sig = Ed25519 over (node_dest | nonce | beacon)
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from monitor.health_poll import OPCODE_FULL_HEALTH

#: "Send full health to the destination that follows." Unknown to older
#: firmware, which drops it BEFORE its rate limiter — so a 0x01 sent after
#: it is still answered there.
OPCODE_HEALTH_TO = 0x04
DEST_HASH_LEN = 16
NONCE_LEN = 8
SIG_LEN = 64
REQUEST_LEN = 1 + DEST_HASH_LEN + NONCE_LEN
#: The medic's reply destination: app name and aspects. Both sides must
#: build the same name — "nodemedic.health.reply".
REPLY_APP = "nodemedic"
REPLY_ASPECTS = ("health", "reply")
#: The medic re-announces its reply destination this often so nodes several
#: hops away, and nodes that booted since, can recall its identity.
REPLY_ANNOUNCE_EVERY_S = 600.0
#: ...but never this close to a poll: the medic's own announce is the
#: likeliest thing to fill a relay's announce cap just when a fallback
#: announce reply needs it.
REPLY_ANNOUNCE_QUIET_AFTER_POLL_S = 60.0

#: Wait windows. A unicast reply is a routed packet — seconds per hop. The
#: floor is the node's 30 s request rate limiter plus margin: a 0x01 sent
#: sooner after a 0x04 is swallowed by new firmware (review finding 2).
UNICAST_BASE_S = 15.0
UNICAST_PER_HOP_S = 5.0
NODE_RATE_LIMIT_S = 30.0
UNICAST_FLOOR_S = NODE_RATE_LIMIT_S + 2.0
#: An announce reply must be REBROADCAST by each relay, and rebroadcast
#: announces are held by a per-interface cap: after one goes out the next
#: waits about airtime / announce_cap. ~212 B at 1.8 kbps ≈ 0.94 s, over
#: Reticulum's 2 % cap ≈ 47 s per relay (review finding 7).
ANNOUNCE_BASE_S = 15.0
ANNOUNCE_BYTES = 212
ANNOUNCE_CAP = 0.02
DEFAULT_BITRATE_BPS = 1800.0
#: How long a poll stays claimable by a late reply.
PENDING_TTL_S = 300.0


# -- the medic's reply identity ------------------------------------------------

#: A dedicated, persistent identity for the reply destination — not the rnsd
#: transport identity (rnsd owns that) and not lxmd's.
REPLY_IDENTITY_PATH = "~/.reticulum-node-medic/health_reply_identity"
#: The shell ping's own identity — ONE, persistent. A throwaway identity
#: per run announced a new destination each time, and the medic listed
#: each as "Neighbour … heard on the mesh" (four ghosts, 2026-09-22).
#: This file is in the medic's own-identity list, so it never lists.
PING_TOOL_IDENTITY_PATH = "~/.reticulum-node-medic/health_ping_identity"


def load_or_create_identity(rns, path: str = REPLY_IDENTITY_PATH):
    """The reply identity, created once. Written atomically with mode 0600
    (review note: the reporter's own helper had neither)."""
    p = os.path.expanduser(path)
    if os.path.isfile(p):
        ident = rns.Identity.from_file(p)
        if ident is not None:
            return ident
    ident = rns.Identity()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(ident.get_private_key())
    os.replace(tmp, p)
    return ident


# -- request -----------------------------------------------------------------

def build_request_to(reply_dest_hash: bytes, nonce: Optional[bytes] = None) -> bytes:
    """0x04 + the 16-byte destination the node should reply to + 8-byte nonce."""
    if not isinstance(reply_dest_hash, (bytes, bytearray)) \
            or len(reply_dest_hash) != DEST_HASH_LEN:
        raise ValueError("reply destination hash must be 16 bytes")
    n = bytes(nonce) if nonce is not None else os.urandom(NONCE_LEN)
    if len(n) != NONCE_LEN:
        raise ValueError("nonce must be 8 bytes")
    return bytes([OPCODE_HEALTH_TO]) + bytes(reply_dest_hash) + n


def build_fallback_request() -> bytes:
    """The old request — announce your beacon — for firmware that does not
    know 0x04 (it dropped it unseen by its rate limiter)."""
    return bytes([OPCODE_FULL_HEALTH])


def parse_request(data: bytes) -> Tuple[int, Optional[bytes], Optional[bytes]]:
    """(opcode, reply_dest, nonce). A node-side helper shared by the Pi
    reporter; a malformed 0x04 reads as opcode -1: ignored."""
    b = bytes(data or b"")
    if not b:
        return (-1, None, None)
    op = b[0]
    if op == OPCODE_HEALTH_TO:
        if len(b) != REQUEST_LEN:
            return (-1, None, None)
        return (op, b[1:1 + DEST_HASH_LEN], b[1 + DEST_HASH_LEN:])
    return (op, None, None)


# -- reply ---------------------------------------------------------------------

def signed_bytes(node_dest: bytes, nonce: bytes, beacon: bytes) -> bytes:
    return bytes(node_dest) + bytes(nonce) + bytes(beacon)


def make_reply(node_dest: bytes, nonce: bytes, beacon: bytes, sign) -> bytes:
    """dest + nonce + beacon + signature, *sign* being ``identity.sign``."""
    if len(node_dest) != DEST_HASH_LEN or len(nonce) != NONCE_LEN:
        raise ValueError("bad destination or nonce length")
    sig = bytes(sign(signed_bytes(node_dest, nonce, beacon)))
    if len(sig) != SIG_LEN:
        raise ValueError("signature must be 64 bytes")
    return signed_bytes(node_dest, nonce, beacon) + sig


def split_reply(payload: bytes) -> Optional[Tuple[bytes, bytes, bytes, bytes]]:
    """(node_dest, nonce, beacon, signature) or None if it cannot be a reply."""
    b = bytes(payload or b"")
    if len(b) <= DEST_HASH_LEN + NONCE_LEN + SIG_LEN:
        return None
    dest = b[:DEST_HASH_LEN]
    nonce = b[DEST_HASH_LEN:DEST_HASH_LEN + NONCE_LEN]
    beacon = b[DEST_HASH_LEN + NONCE_LEN:-SIG_LEN]
    return (dest, nonce, beacon, b[-SIG_LEN:])


def verify_reply(payload: bytes, recall: Callable[[bytes], object]
                 ) -> Optional[Tuple[bytes, bytes, bytes]]:
    """(node_dest, nonce, beacon) when the signature verifies under the
    identity *recall(node_dest)* returns (an object with ``validate(sig,
    msg)``); None otherwise. The hash names the key; the signature is the
    authority. An unverifiable reply is nobody's word: never a green card."""
    parts = split_reply(payload)
    if parts is None:
        return None
    dest, nonce, beacon, sig = parts
    try:
        ident = recall(dest)
        ok = ident is not None and bool(
            ident.validate(sig, signed_bytes(dest, nonce, beacon)))
    except Exception:                                              # noqa: BLE001
        ok = False
    return (dest, nonce, beacon) if ok else None


def uptime_is_fresh(new_uptime_s: Optional[int], last_uptime_s: Optional[int],
                    rebooted: bool = False, reboot_grace_s: int = 300) -> bool:
    """A verified reply whose uptime runs BACKWARDS is a replay or a stale
    queue, unless the node rebooted (reset reason changed, or uptime is
    small). Replay of a captured ciphertext is already refused by the
    transport's packet-hash list; this owns freshness at the protocol."""
    if new_uptime_s is None or last_uptime_s is None:
        return True
    if new_uptime_s >= last_uptime_s:
        return True
    return rebooted or new_uptime_s <= reboot_grace_s


# -- the node's decision -------------------------------------------------------

def node_reply_mode(recalled: bool, has_path: bool) -> str:
    """'unicast' only when the node can encrypt to the medic AND has a
    road back; otherwise 'announce' — today's reply, which the medic hears
    if it can — and the node should request a path so the NEXT poll is
    unicast (review findings 2 and 4)."""
    return "unicast" if (recalled and has_path) else "announce"


# -- waits ---------------------------------------------------------------------

def unicast_wait_s(hops: Optional[int]) -> float:
    """W1: seconds per hop for a routed packet, floored at the node's rate
    limiter plus margin so the 0x01 that follows is not swallowed."""
    return max(UNICAST_BASE_S + UNICAST_PER_HOP_S * max(0, (hops or 1) - 1),
               UNICAST_FLOOR_S)


def relay_cap_hold_s(bitrate_bps: float = DEFAULT_BITRATE_BPS,
                     announce_bytes: int = ANNOUNCE_BYTES,
                     announce_cap: float = ANNOUNCE_CAP) -> float:
    """One relay's announce-cap hold: airtime of one announce / the cap."""
    airtime = (announce_bytes * 8.0) / max(bitrate_bps, 1.0)
    return airtime / max(announce_cap, 1e-6)


def announce_wait_s(hops: Optional[int], bitrate_bps: float = DEFAULT_BITRATE_BPS) -> float:
    """W2: base plus one cap hold per relay between here and the node."""
    return ANNOUNCE_BASE_S + max(0, (hops or 1) - 1) * relay_cap_hold_s(bitrate_bps)


# -- pending polls: late replies still count -----------------------------------

class PendingPolls:
    """The polls still waiting for a word, keyed by nonce, with a TTL. The
    packet callback claims one when a verified reply's nonce matches; a
    reply with no pending poll is still fresh health, just not an answer
    to a question anyone is waiting on."""

    def __init__(self, ttl_s: float = PENDING_TTL_S, now=time.time):
        self._ttl = ttl_s
        self._now = now
        self._lock = threading.Lock()
        self._polls: Dict[bytes, dict] = {}

    def add(self, nonce: bytes, dest_hash: bytes, hops: Optional[int],
            report=None) -> None:
        with self._lock:
            self._expire()
            self._polls[bytes(nonce)] = {"dest": bytes(dest_hash), "hops": hops,
                                         "sent_at": self._now(), "report": report}

    def claim(self, nonce: bytes, dest_hash: bytes) -> Optional[dict]:
        """The poll this reply answers, removed from the table — only if the
        nonce is pending AND the reply names the destination it was sent to
        (a nonce alone could be lifted from a captured request)."""
        with self._lock:
            self._expire()
            p = self._polls.get(bytes(nonce))
            if p is None or p["dest"] != bytes(dest_hash):
                return None
            del self._polls[bytes(nonce)]
            p["answered_after_s"] = self._now() - p["sent_at"]
            return p

    def is_pending(self, nonce: bytes) -> bool:
        """Still waiting? The poll thread's oracle: False once a verified
        reply has claimed the nonce (or the TTL passed)."""
        with self._lock:
            self._expire()
            return bytes(nonce) in self._polls

    def pending(self) -> int:
        with self._lock:
            self._expire()
            return len(self._polls)

    def _expire(self) -> None:
        cutoff = self._now() - self._ttl
        for k in [k for k, p in self._polls.items() if p["sent_at"] < cutoff]:
            del self._polls[k]
