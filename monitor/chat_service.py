"""In-medic chat — the LXMF side (docs/CHAT.md).

Owns the medic's ONE messaging identity (``~/.reticulum-node-medic/lxmf_identity``
— the same file the operator-alert push has used since 2026-08-01, so alerts
and chat come from one address) and runs an ``LXMRouter`` against the shared
rnsd instance the UI already attaches to. Everything it learns goes into the
``MessageStore``; the screen never touches RNS.

How a message travels:

* DIRECT first — an encrypted link straight to the peer, if the mesh has a
  path. That is the byte-frugal path ([[bandwidth-economy-ethos]]).
* If nothing answers, PROPAGATED — handed to this medic's own lxmd (the post
  office next door, over the shared instance, no LoRa airtime) which holds it
  until the peer syncs. The store shows it as "waiting at propagation node",
  with this medic's name beside it: the node that is holding it.
* Either way the peer's IDENTITY must be known: LXMF encrypts to it. An
  address nobody on the mesh has announced cannot be written to, and the
  store says so in words rather than spinning.

``RNS``/``LXMF`` are imported at start(), not at module import, and can be
injected — the Mac has neither, and the tests drive a fake pair.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

from monitor import lxmf_chat as store_mod
from monitor.lxmf_chat import MessageStore
from monitor.operator_alert import normalize_address, valid_address

IDENTITY_PATH = os.path.expanduser("~/.reticulum-node-medic/lxmf_identity")
ROUTER_STORAGE = os.path.expanduser("~/.reticulum-node-medic/lxmf")
#: The propagation node next door: lxmd's identity, from which its propagation
#: destination hash follows.
LXMD_IDENTITY = os.path.expanduser("~/.lxmd/identity")

#: How long to wait for the mesh to find a path before falling back.
PATH_WAIT_S = 12.0
#: Ask our own propagation node for anything held for us this often. It is a
#: local request over the shared instance — nothing goes over LoRa for it.
SYNC_EVERY_S = 20 * 60
#: Re-announce our address now and then so a phone that missed the first
#: one still finds us. Sideband's own window (90–300 min, randomised), and
#: nothing more often: an announce IS LoRa airtime.
ANNOUNCE_MIN_S, ANNOUNCE_MAX_S = 90 * 60, 300 * 60


def _trunc(v, n: int = 80) -> str:
    s = repr(v)
    return s if len(s) <= n else s[:n] + "…"


def _is_local_road(name: str) -> bool:
    n = (name or "").lower()
    return "local" in n or "shared instance" in n


def _short_iface(name: str) -> str:
    """'RNodeInterface[RNode LoRa Interface]' -> 'RNode LoRa Interface'."""
    if "[" in name and name.endswith("]"):
        return name[name.index("[") + 1:-1]
    return name


class ChatService:
    def __init__(self, store: MessageStore, display_name: str = "Node Medic",
                 identity_path: str = IDENTITY_PATH,
                 storage_path: str = ROUTER_STORAGE,
                 lxmd_identity_path: str = LXMD_IDENTITY,
                 rns=None, lxmf=None, log: Optional[Callable[[str], None]] = None,
                 path_wait_s: float = PATH_WAIT_S, run: Optional[Callable[[str], str]] = None):
        self.store = store
        self.display_name = display_name
        self.identity_path = identity_path
        self.storage_path = storage_path
        self.lxmd_identity_path = lxmd_identity_path
        self._rns = rns
        self._lxmf = lxmf
        self._log = log or (lambda m: None)
        self._path_wait_s = path_wait_s
        self._run = run                 # shell runner for rnpath (tests inject)
        self._router = None
        self._identity = None
        self._dest = None
        self._propagation_hash: Optional[bytes] = None
        self._last_sync = 0.0
        #: when the propagation node was last asked — the screen prints it, so "no
        #: new messages" is a statement with a time on it (the thing people
        #: could not tell in Reticulum discussion #991)
        self.last_sync_at = 0.0
        self._next_announce = 0.0
        self.running = False
        self.last_error = ""

    # -- lifecycle -----------------------------------------------------------

    @property
    def address(self) -> str:
        """Our LXMF address (32 hex), or "" before the identity exists."""
        if self._dest is not None:
            return self._dest.hash.hex()
        return ""

    def start(self) -> bool:
        """Bring the router up on an already-attached RNS. Safe to call twice."""
        if self.running:
            return True
        try:
            RNS = self._rns or __import__("RNS")
            LXMF = self._lxmf or __import__("LXMF")
            self._rns, self._lxmf = RNS, LXMF
            if os.path.exists(self.identity_path):
                ident = RNS.Identity.from_file(self.identity_path)
            else:
                ident = RNS.Identity()
                os.makedirs(os.path.dirname(self.identity_path), exist_ok=True)
                ident.to_file(self.identity_path)
            self._identity = ident
            router = self._make_router(LXMF)
            self._dest = router.register_delivery_identity(
                ident, display_name=self.display_name)
            router.register_delivery_callback(self._on_delivery)
            self._router = router

            svc = self

            class _PeerHandler:
                aspect_filter = "lxmf.delivery"

                def received_announce(_h, destination_hash, announced_identity,
                                      app_data):
                    svc._on_peer_announce(destination_hash, app_data)

            RNS.Transport.register_announce_handler(_PeerHandler())

            if os.path.exists(self.lxmd_identity_path):
                lx = RNS.Identity.from_file(self.lxmd_identity_path)
                self._propagation_hash = RNS.Destination.hash(lx, "lxmf", "propagation")
                router.set_outbound_propagation_node(self._propagation_hash)
            # One announce so phones can find this address; LXMF's own
            # cadence takes it from here.
            router.announce(self._dest.hash)
            self._schedule_announce()
            # A message still "sending" from before the restart never got its
            # callback; say so rather than spin forever.
            stuck = self.store.fail_stuck_sending()
            if stuck:
                self._log("chat: %d message(s) left over from last session marked failed" % stuck)
            self.running = True
            self._log("chat up as %s (%s)" % (self.address, self.display_name))
            self.tick(force=True)
            return True
        except Exception as e:                                         # noqa: BLE001
            import traceback
            self.last_error = str(e)
            tb = traceback.extract_tb(e.__traceback__)
            where = ("%s:%d in %s" % (tb[-1].filename.split("/")[-1], tb[-1].lineno,
                                      tb[-1].name)) if tb else "?"
            self._log("chat failed to start: %r (raised at %s)" % (e, where))
            return False

    def _schedule_announce(self, now: float = None):
        import random
        now = time.time() if now is None else now
        self._next_announce = now + random.uniform(ANNOUNCE_MIN_S, ANNOUNCE_MAX_S)

    def _make_router(self, LXMF):
        """LXMRouter.__init__ installs SIGINT/SIGTERM handlers — Python allows
        that on the main thread only, and this starts on the mesh-listener
        thread (live, 2026-09-29 23:58: "signal only works in main thread").
        Kivy owns the process signals in any case; the router never gets
        them. atexit still runs its exit_handler."""
        import signal as _signal
        orig = _signal.signal
        _signal.signal = lambda *a, **k: None
        try:
            return LXMF.LXMRouter(storagepath=self.storage_path)
        finally:
            _signal.signal = orig

    def tick(self, force: bool = False, now: float = None) -> bool:
        """Periodic (the app calls it once a minute): re-announce when the
        window is up, and ask our propagation node for held messages. Returns True
        when a sync was requested this call."""
        if not self.running:
            return False
        now = time.time() if now is None else now
        if now >= self._next_announce:
            try:
                self._router.announce(self._dest.hash)
            except Exception as e:                                     # noqa: BLE001
                self._log("chat: announce failed: %r" % (e,))
            self._schedule_announce(now)
        if self._propagation_hash is None:
            return False
        if not force and now - self._last_sync < SYNC_EVERY_S:
            return False
        self._last_sync = now
        try:
            self._router.request_messages_from_propagation_node(self._identity)
            self.last_sync_at = now
            self._log("chat: asked the propagation node for held messages")
            return True
        except Exception as e:                                         # noqa: BLE001
            self._log("post-office sync failed: %r" % (e,))
            return False

    # -- inbound -------------------------------------------------------------

    def _on_delivery(self, message):
        try:
            peer = message.source_hash.hex()
            mid = message.hash.hex() if getattr(message, "hash", None) else None
            text = message.content_as_string()
            ts = float(getattr(message, "timestamp", None) or time.time())
            title = ""
            try:
                title = message.title_as_string() or ""
            except Exception:                                          # noqa: BLE001
                title = ""
            fields = {}
            try:
                fields = dict(getattr(message, "fields", None) or {})
            except Exception:                                          # noqa: BLE001
                fields = {}
            if not text.strip():
                # Columba's emoji REACTION is an LXMF message with no text
                # and the reaction in a field; it drew as an empty bubble
                # (operator, 2026-10-01). Say what the fields carried.
                text = store_mod.describe_fields(fields, title, text_of=self.store.text_of)
                self._log("chat: textless message from %s: title=%r fields=%s"
                          % (peer[:8], title, {k: _trunc(v) for k, v in fields.items()}))
            rec = self.store.add_incoming(peer, text, ts=ts, msg_id=mid)
            if rec is not None:
                self._log("chat: message from %s" % peer[:8])
        except Exception as e:                                         # noqa: BLE001
            self._log("chat: inbound dropped: %r" % (e,))

    def _on_peer_announce(self, destination_hash, app_data):
        try:
            name = ""
            try:
                name = self._lxmf.display_name_from_app_data(app_data) or ""
            except Exception:                                          # noqa: BLE001
                name = ""
            peer = destination_hash.hex()
            self.store.remember_peer(peer, name=name)
            # They are on the air now: anything that failed for want of them
            # goes again (MeshChat's auto-resend-on-announce).
            for rec in self.store.failed_to(peer):
                self.store.set_state(rec["id"], store_mod.SENDING)
                threading.Thread(target=self._deliver,
                                 args=(rec["id"], peer, rec["text"]),
                                 daemon=True).start()
        except Exception as e:                                         # noqa: BLE001
            self._log("chat: announce dropped: %r" % (e,))

    # -- what the screen can say about a peer ---------------------------------

    def peer_route(self, peer: str) -> dict:
        """How this medic would reach *peer* right now — the thing MeshChat is
        bolting on as "message path" toasts, and the first question anyone
        asks when a message sits at "sent": {hops: int|None, interface: str,
        known: bool}. hops None = no path; known = the identity is on hand."""
        out = {"hops": None, "interface": "", "known": False}
        if not self.running:
            return out
        RNS = self._rns
        try:
            dh = bytes.fromhex(normalize_address(peer))
        except ValueError:
            return out
        try:
            out["known"] = RNS.Identity.recall(dh) is not None
            if RNS.Transport.has_path(dh):
                out["hops"] = int(RNS.Transport.hops_to(dh))
                iface = RNS.Transport.next_hop_interface(dh)
                out["interface"] = str(getattr(iface, "name", iface) or "")
        except Exception:                                              # noqa: BLE001
            pass
        if out["hops"] is not None and _is_local_road(out["interface"]):
            # This process is a CLIENT of rnsd: its own next hop is the shared
            # instance, so the first live exchange read "1 hop via Local shared
            # instance" (2026-10-01). rnsd's path table names the real road.
            real = self._rnsd_road(dh)
            if real is not None:
                out["hops"], out["interface"] = real
        return out

    def _rnsd_road(self, dh: bytes):
        """(hops, interface) from rnsd's own path table, or None."""
        import subprocess
        from monitor.mesh import parse_rnpath
        try:
            if self._run is None:
                raw = subprocess.run(["bash", "-lc", "rnpath -t --json 2>/dev/null"],
                                     capture_output=True, text=True, timeout=10).stdout
            else:
                raw = self._run("rnpath -t --json")
        except Exception:                                              # noqa: BLE001
            return None
        want = dh.hex()
        for n in parse_rnpath(raw or "[]"):
            if n.dst_hash == want:
                return n.hops, _short_iface(n.interface)
        return None

    # -- outbound ------------------------------------------------------------

    def send(self, peer: str, text: str) -> Optional[dict]:
        """Queue *text* for *peer* (32-hex). Returns the store record (state
        "sending") or None when the address is malformed or chat is down. The
        mesh work happens on its own thread; the record's state follows it."""
        peer = normalize_address(peer)
        text = (text or "").strip()
        if not valid_address(peer) or not text or not self.running:
            return None
        rec = self.store.add_outgoing(peer, text)
        threading.Thread(target=self._deliver, args=(rec["id"], peer, text),
                         daemon=True).start()
        return rec

    def send_plain(self, peer: str, text: str) -> bool:
        """The operator-alert sender signature: True when queued."""
        return self.send(peer, text) is not None

    def _peer_identity(self, dh: bytes):
        """Ask the mesh for a path first (Sideband's order), THEN look the
        identity up: a known identity with no path still wants the path
        request, or every message to it goes to the propagation node even when
        the peer is one hop away and awake."""
        RNS = self._rns
        if not RNS.Transport.has_path(dh):
            RNS.Transport.request_path(dh)
            t0 = time.time()
            while (not RNS.Transport.has_path(dh)
                   and time.time() - t0 < self._path_wait_s):
                time.sleep(0.25)
        return RNS.Identity.recall(dh)

    def _deliver(self, msg_id: str, peer: str, text: str):
        RNS, LXMF = self._rns, self._lxmf
        try:
            dh = bytes.fromhex(peer)
            ident = self._peer_identity(dh)
            if ident is None:
                self.store.set_state(msg_id, store_mod.FAILED)
                return
            dest = RNS.Destination(ident, RNS.Destination.OUT,
                                   RNS.Destination.SINGLE, "lxmf", "delivery")
            self._dispatch(msg_id, dest, text, self._method_for(dh))
        except Exception as e:                                         # noqa: BLE001
            self._log("chat: send failed: %r" % (e,))
            self.store.set_state(msg_id, store_mod.FAILED)

    def _method_for(self, dh: bytes):
        """No path → the propagation node. A path but no link up, and a ratchet
        known → OPPORTUNISTIC: one packet, no link handshake — the cheapest
        thing that can carry a short message over LoRa (Sideband's rule; the
        router falls back to a link by itself if the message won't fit a
        packet). Otherwise a DIRECT link."""
        RNS, LXMF = self._rns, self._lxmf
        if not RNS.Transport.has_path(dh):
            return LXMF.LXMessage.PROPAGATED
        try:
            link_up = self._router.delivery_link_available(dh)
            ratchet = RNS.Identity.current_ratchet_id(dh)
        except Exception:                                              # noqa: BLE001
            return LXMF.LXMessage.DIRECT
        if not link_up and ratchet is not None:
            return LXMF.LXMessage.OPPORTUNISTIC
        return LXMF.LXMessage.DIRECT

    def _dispatch(self, msg_id: str, dest, text: str, method):
        LXMF = self._lxmf
        if method == LXMF.LXMessage.PROPAGATED and self._propagation_hash is None:
            self.store.set_state(msg_id, store_mod.FAILED)
            return
        lxm = LXMF.LXMessage(dest, self._dest, text, self.display_name,
                             desired_method=method)
        propagated = method == LXMF.LXMessage.PROPAGATED

        def _delivered(message):
            if propagated:
                self.store.set_state(msg_id, store_mod.POSTED, via=self.display_name)
            else:
                self.store.set_state(msg_id, store_mod.DELIVERED)

        def _failed(message):
            if not propagated and self._propagation_hash is not None:
                # Nothing answered on a link — leave it with the propagation
                # node. OFF this thread: LXMF fires the failure callback while
                # holding its own lock, and handing the retry straight back to
                # router.handle_outbound from inside it deadlocked outbound
                # chat for the rest of the session (readiness ledger #183).
                threading.Thread(
                    target=self._dispatch,
                    args=(msg_id, dest, text, LXMF.LXMessage.PROPAGATED),
                    daemon=True).start()
            else:
                self.store.set_state(msg_id, store_mod.FAILED)

        lxm.register_delivery_callback(_delivered)
        lxm.register_failed_callback(_failed)
        self._router.handle_outbound(lxm)
        self.store.set_state(msg_id, store_mod.SENDING)
