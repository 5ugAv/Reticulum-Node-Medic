"""The medic's side of time over the mesh, as one object the app delegates
to (docs/HEALTH_REPLY_UNICAST.md, "Time over the mesh", 2026-09-23; the
review the same day moved the logic out of ui/app.py so it could be tested
BEHAVIOURALLY against a fake RNS rather than pinned by source grep).

What it owns:
  * dispatch of what lands on the reply destination (TIME_REQ / TIME_ACK /
    an ack nobody remembers / a packet too short to be anything) — the
    health reply itself stays the app's;
  * the answer to a TIME_REQ: only for a known Pi kin node, one per node per
    TIME_REQ_COOLDOWN_S, at most MAX_INFLIGHT_SENDS sender threads at once;
  * the send: the medic signs ONLY a disciplined clock (monitor.medic_clock),
    after the node's identity is recalled and a path warmed; the ledger
    records the send only after .send() returned;
  * the ack: verified under the node's key, matched to a pending send or —
    after a UI restart — to the ledger's remembered nonce; recorded;
  * the unasked push after a verified health reply, Pi nodes only, gated on
    the ledger's max(sent_at, tried_at);
  * the node page's ledger lookup across every destination of a device.

Every outside effect is injected: *rns* is a callable returning the RNS
module, *reply_ident* returns the medic's reply identity (or None before
attach), *registry* returns the live registry, *disciplined* answers
(ok, reason) about the medic's own clock, *spawn* runs a sender (a daemon
thread by default; tests run it inline), *now*/*monotonic* are clocks.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, Optional

from monitor.health_poll import warm_path
from monitor.health_reply import (KIND_REPLY, KIND_SHORT, KIND_TIME_ACK,
                                  KIND_TIME_ACK_UNKNOWN, KIND_TIME_REQ,
                                  MIN_REPLY_LEN, TIME_STATUS_HELPER_FAILED,
                                  TIME_STATUS_NOT_NEEDED, TIME_STATUS_REFUSED_NTP,
                                  TIME_STATUS_REFUSED_STALE, TIME_STATUS_SET,
                                  build_time, classify_inbound, parse_time_req,
                                  time_ack_nonce, verify_time_ack)
from monitor.node_time import epoch_is_sane
from monitor.time_ledger import TIME_PUSH_EVERY_S, TimeLedger

#: One answer per node per minute: a node that re-asks in a tight loop (a
#: reporter bug, or a stranger replaying its request) costs one sender
#: thread and one 81-byte packet a minute, no more.
TIME_REQ_COOLDOWN_S = 60.0
#: Sender threads alive at once. Each warms a path for up to WARM_WAIT_S;
#: a flood of requests must not become a flood of threads.
MAX_INFLIGHT_SENDS = 4
WARM_WAIT_S = 15.0
#: The pending-nonce table: its own store, NOT PendingPolls' 300 s — an ack
#: crosses several LoRa hops after a path warm on the node's side, and a
#: node that reboots before answering may ack the same nonce hours later.
PENDING_TIME_TTL_S = 24 * 3600
PENDING_TIME_MAX = 256
#: Rate limit on the log lines a stranger could provoke.
LOG_EVERY_S = 10.0

STATUS_TEXT = {
    TIME_STATUS_NOT_NEEDED: "not moved — already within 30 s",
    TIME_STATUS_SET: "clock SET",
    TIME_STATUS_HELPER_FAILED: "NOT set — the node's clock helper failed",
    TIME_STATUS_REFUSED_NTP: "refused — the node keeps NTP time",
    TIME_STATUS_REFUSED_STALE: "refused — epoch not newer than the last it applied",
}


def is_pi_node(node_type) -> bool:
    """A node with an OS clock to keep. The registry's Pi type is
    "pi_propagation" (kin_roster.type_for_cert), older rows say "pi";
    an RTNode-2400 ("rtnode2400") has no clock to set."""
    return str(node_type or "").startswith("pi")


class PendingTimes:
    """The TIMEs still waiting for a TIME_ACK, keyed by nonce: bounded and
    with a long TTL (see PENDING_TIME_TTL_S). Oldest entry evicted first
    when full."""

    def __init__(self, ttl_s: float = PENDING_TIME_TTL_S, max_entries: int = PENDING_TIME_MAX,
                 now: Callable[[], float] = time.time):
        self._ttl, self._max, self._now = ttl_s, max_entries, now
        self._lock = threading.Lock()
        self._rows: Dict[bytes, dict] = {}

    def add(self, nonce: bytes, dest_hash: bytes) -> None:
        with self._lock:
            self._expire()
            while len(self._rows) >= self._max:
                oldest = min(self._rows, key=lambda k: self._rows[k]["sent_at"])
                del self._rows[oldest]
            self._rows[bytes(nonce)] = {"dest": bytes(dest_hash), "sent_at": self._now()}

    def claim(self, nonce: bytes, dest_hash: bytes) -> Optional[dict]:
        """The send this ack answers, removed — only if the nonce is pending
        AND the ack names the destination it was sent to."""
        with self._lock:
            self._expire()
            p = self._rows.get(bytes(nonce))
            if p is None or p["dest"] != bytes(dest_hash):
                return None
            del self._rows[bytes(nonce)]
            return p

    def is_pending(self, nonce: bytes) -> bool:
        with self._lock:
            self._expire()
            return bytes(nonce) in self._rows

    def pending(self) -> int:
        with self._lock:
            self._expire()
            return len(self._rows)

    def _expire(self) -> None:
        cutoff = self._now() - self._ttl
        for k in [k for k, p in self._rows.items() if p["sent_at"] < cutoff]:
            del self._rows[k]


def _thread_spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class TimeService:
    def __init__(self, ledger: TimeLedger, rns: Callable[[], object],
                 reply_ident: Callable[[], object], registry: Callable[[], object],
                 disciplined: Callable[[], tuple], now: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = None, spawn: Callable = _thread_spawn,
                 warm_wait_s: float = WARM_WAIT_S, urandom: Callable[[int], bytes] = os.urandom):
        self.ledger = ledger
        self.pending = PendingTimes(now=now)
        self._rns, self._reply_ident, self._registry = rns, reply_ident, registry
        self._disciplined = disciplined
        self._now, self._mono = now, monotonic
        self._log = log or (lambda m: None)
        self._spawn, self._warm_wait_s, self._urandom = spawn, warm_wait_s, urandom
        # RLock: the rate-limited log is called from inside the cooldown
        # and in-flight critical sections (a plain Lock deadlocked there
        # on the first behavioural test, 2026-09-23).
        self._lock = threading.RLock()
        self._inflight = 0
        self._last_req_at: Dict[str, float] = {}
        self._last_log_at: Dict[str, float] = {}

    # -- memory of sends ---------------------------------------------------------

    def nonce_known(self, nonce: bytes) -> bool:
        """Pending in memory, or the ledger's last send to some node (an ack
        that lands after the UI restarted still matches its send)."""
        return self.pending.is_pending(nonce) or \
            self.ledger.dest_for_nonce(bytes(nonce).hex()) is not None

    def classify(self, data: bytes) -> str:
        return classify_inbound(data, self.nonce_known)

    def inbound(self, data: bytes) -> bool:
        """True when the packet was one of ours to handle (a time packet, an
        orphan ack, or something too short to be a reply); False means "a
        health reply — verify it yourself"."""
        kind = self.classify(data)
        if kind == KIND_TIME_REQ:
            self.on_time_req(data)
            return True
        if kind == KIND_TIME_ACK:
            self.on_time_ack(data)
            return True
        if kind == KIND_TIME_ACK_UNKNOWN:
            n = time_ack_nonce(data)
            self._limited("ack_unknown", "TIME_ACK for a send this medic does not "
                          "remember (nonce %s) — dropped" % (n.hex() if n else "?"))
            return True
        if kind == KIND_SHORT:
            self._limited("short", "%d-byte packet on the reply destination is too "
                          "short to be a health reply (minimum %d) and is not a time "
                          "packet — dropped" % (len(bytes(data or b"")), MIN_REPLY_LEN))
            return True
        return kind != KIND_REPLY

    # -- a node asked ----------------------------------------------------------------

    def pi_kin_record(self, dest_hex: str):
        """The registry's record for *dest_hex* when it is one of the medic's
        own Pi nodes (kin, node_type starting "pi"); None otherwise. A stranger
        naming a hash gains nothing: the record must already be kin."""
        try:
            reg = self._registry()
            rec = reg.nodes.get((dest_hex or "").lower()) if reg is not None else None
            if rec is None or not is_pi_node(getattr(rec, "node_type", "")):
                return None
            if getattr(rec, "provenance", "") != "kin":
                return None
            return rec
        except Exception:                                          # noqa: BLE001
            return None

    def on_time_req(self, data: bytes) -> bool:
        """A 25-byte 0x06 on the inbound thread. Answered off-thread — and
        only for a known Pi kin node, once a minute per node, with a cap on
        sender threads. Returns True when a sender was started."""
        parsed = parse_time_req(data)
        if parsed is None:
            return False
        node_dest, nonce = parsed
        dest_hex = node_dest.hex()
        if self.pi_kin_record(dest_hex) is None:
            self._limited("req_unknown", "TIME_REQ from %s: not a known Pi kin node — "
                          "ignored" % dest_hex[:8])
            return False
        with self._lock:
            last = self._last_req_at.get(dest_hex)
            mono = self._mono()
            cooling = last is not None and mono - last < TIME_REQ_COOLDOWN_S
            if not cooling:
                self._last_req_at[dest_hex] = mono
        if cooling:
            self._limited("req_cooldown", "TIME_REQ from %s within %d s of the last "
                          "— ignored" % (dest_hex[:8], TIME_REQ_COOLDOWN_S))
            return False
        self._limited("req", "TIME_REQ from %s — answering" % dest_hex[:8])
        return self._start_send(node_dest, nonce, "asked")

    def _start_send(self, node_dest: bytes, nonce: Optional[bytes], why: str) -> bool:
        with self._lock:
            full = self._inflight >= MAX_INFLIGHT_SENDS
            if not full:
                self._inflight += 1
            n = self._inflight
        if full:
            self._limited("inflight", "TIME to %s (%s) dropped: %d sends already in "
                          "flight" % (node_dest.hex()[:8], why, n))
            return False
        try:
            self._spawn(self.send_time, node_dest, nonce, why)
        except Exception as e:                                     # noqa: BLE001
            with self._lock:
                self._inflight -= 1
            self._log("TIME to %s (%s) could not start: %s" % (node_dest.hex()[:8], why, e))
            return False
        return True

    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    # -- the send ---------------------------------------------------------------------

    def send_time(self, node_dest: bytes, nonce: Optional[bytes], why: str) -> bool:
        """One signed TIME to a node's rtnode.health destination, on a sender
        thread. Refuses when the medic's own clock is not disciplined (says
        why at NOTICE, records the refusal). Then the two-phase pattern of
        the ping: recall the node's identity, warm THIS stack's path (a send
        without one drops silently, 2026-08-21), sign `node_dest ‖ 0x05 ‖
        time ‖ nonce` with the reply identity. The ledger records the send
        only after .send() returned without raising."""
        dest_hex = node_dest.hex()
        try:
            ok, reason = self._disciplined()
            if not ok:
                self._log("refusing to sign time for %s (%s): %s" % (dest_hex[:8], why, reason))
                self.ledger.record_refused(dest_hex, reason)
                return False
            now_s = int(self._now())
            if not epoch_is_sane(now_s):
                self._log("refusing to sign time for %s (%s): this medic's own clock "
                          "reads %d, outside 2026..2100" % (dest_hex[:8], why, now_s))
                self.ledger.record_refused(dest_hex, "medic clock reads %d" % now_s)
                return False
            ident = self._reply_ident()
            if ident is None:
                self._log("TIME not sent to %s: reply identity not set up yet" % dest_hex[:8])
                return False
            RNS = self._rns()
            node_ident = RNS.Identity.recall(node_dest)
            if node_ident is None:
                self._log("TIME not sent to %s: node identity not recalled (no "
                          "announce heard from it)" % dest_hex[:8])
                return False
            if not warm_path(node_dest, RNS.Transport.has_path, RNS.Transport.request_path,
                             wait_s=self._warm_wait_s):
                self._log("TIME not sent to %s: no path from this stack (path requested; "
                          "the node's next ask will find one)" % dest_hex[:8])
                return False
            dest = RNS.Destination(node_ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                   "rtnode", "health")
            n = bytes(nonce) if nonce else self._urandom(8)
            now_s = int(self._now())                    # fresh after the warm wait
            pkt = build_time(node_dest, now_s, n, ident.sign)
            self.pending.add(n, node_dest)
            RNS.Packet(dest, pkt).send()
            self.ledger.record_sent(dest_hex, now_s, why=why, nonce_hex=n.hex())
            self._log("TIME sent to %s (%s): %d, nonce %s — waiting for its ack"
                      % (dest_hex[:8], why, now_s, n.hex()))
            return True
        except Exception as e:                                     # noqa: BLE001
            self._log("TIME to %s (%s) failed: %s" % (dest_hex[:8], why, e))
            return False
        finally:
            with self._lock:
                if self._inflight > 0:
                    self._inflight -= 1

    # -- the ack ------------------------------------------------------------------------

    def on_time_ack(self, data: bytes) -> Optional[dict]:
        """The node's word on what it did with a TIME: verified under the
        node's own recalled identity, bound to a pending send by nonce AND
        destination — or to the ledger's remembered nonce when the UI
        restarted in between — and recorded. Returns the ledger row."""
        try:
            RNS = self._rns()
            got = verify_time_ack(data, recall=RNS.Identity.recall)
        except Exception as e:                                     # noqa: BLE001
            self._limited("ack_error", "TIME_ACK could not be verified: %s" % e)
            return None
        if got is None:
            self._limited("ack_bad", "unverifiable TIME_ACK dropped")
            return None
        node_dest, nonce, before, status = got
        dest_hex = node_dest.hex()
        how = "pending send"
        if self.pending.claim(nonce, node_dest) is None:
            if self.ledger.dest_for_nonce(nonce.hex()) != dest_hex:
                self._log("TIME_ACK from %s names a nonce sent to another node — dropped"
                          % dest_hex[:8])
                return None
            how = "the ledger's last send (matched after a restart)"
        entry = self.ledger.record_ack(dest_hex, before, status)
        delta = entry.get("delta_s")
        self._log("TIME_ACK from %s: %s (its clock read %d; %s; matched %s)" % (
            dest_hex[:8], STATUS_TEXT.get(status, "status %d (unknown to this medic)" % status),
            before, "was %+d s off" % delta if delta is not None else "delta unknown", how))
        return entry

    # -- the unasked push ----------------------------------------------------------------

    def maybe_push_time(self, node_dest: bytes) -> bool:
        """After a verified health reply: push a TIME unasked if no send OR
        try went to this Pi node in TIME_PUSH_EVERY_S. tried_at is recorded
        HERE, on the inbound thread, before the sender starts — two replies
        seconds apart must not start two senders."""
        try:
            dest_hex = node_dest.hex()
            if self.pi_kin_record(dest_hex) is None:
                return False
            if not self.ledger.should_push(dest_hex, TIME_PUSH_EVERY_S):
                return False
            self.ledger.record_tried(dest_hex)
            return self._start_send(node_dest, None, "push")
        except Exception as e:                                     # noqa: BLE001
            self._log("unasked TIME push to %s failed: %s" % (node_dest.hex()[:8], e))
            return False

    # -- the node page ---------------------------------------------------------------------

    def clock_entry_for(self, key: str, now: float) -> Optional[dict]:
        """The ledger row for the DEVICE the record *key* belongs to: every
        destination of its group (registry.consolidated_records) is tried,
        the freshest row wins. The ledger is keyed by the node's health
        destination; the row tapped in VITALS may be led by another aspect."""
        hashes = [key] if key else []
        try:
            reg = self._registry()
            for cons, members in (reg.consolidated_records(now) if reg is not None else []):
                if any(getattr(m, "dst_hash", None) == key for m in members) \
                        or getattr(cons, "dst_hash", None) == key:
                    hashes = [getattr(m, "dst_hash", "") for m in members] + [key]
                    break
        except Exception:                                          # noqa: BLE001
            pass
        rows = []
        seen = set()
        for h in hashes:
            h = (h or "").lower()
            if not h or h in seen:
                continue
            seen.add(h)
            e = self.ledger.entry(h)
            if e:
                rows.append(e)
        if not rows:
            return None

        def _fresh(e):
            return max(float(e[k]) for k in ("acked_at", "sent_at", "tried_at", "refused_at")
                       if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool)) \
                if any(isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool)
                       for k in ("acked_at", "sent_at", "tried_at", "refused_at")) else 0.0
        return max(rows, key=_fresh)

    # -- logging ---------------------------------------------------------------------------

    def _limited(self, key: str, msg: str) -> None:
        """Rate-limited per key: these lines can be provoked by a stranger."""
        mono = self._mono()
        with self._lock:
            last = self._last_log_at.get(key)
            if last is not None and mono - last < LOG_EVERY_S:
                return
            self._last_log_at[key] = mono
        self._log(msg)
