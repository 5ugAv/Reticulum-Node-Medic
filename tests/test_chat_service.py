"""The chat service against a fake RNS/LXMF pair — every road a message can
take (docs/CHAT.md), with no radio and no Kivy."""
import os
import time

import pytest

from monitor import lxmf_chat as lc
from monitor.chat_service import ChatService
from monitor.lxmf_chat import MessageStore

PEER = "c" * 32
PEER_BYTES = bytes.fromhex(PEER)


class _Hash:
    def __init__(self, b): self.hash = b


class FakeIdentity:
    saved = {}

    def __init__(self, tag="me"):
        self.tag = tag
        self.hash = (tag * 16).encode()[:16]

    @classmethod
    def from_file(cls, path): return cls(os.path.basename(path)[:2])

    def to_file(self, path):
        with open(path, "w") as f: f.write(self.tag)

    known = {}

    @classmethod
    def recall(cls, dh): return cls.known.get(dh)


class FakeTransport:
    paths = set()
    requested = []
    handlers = []

    @classmethod
    def has_path(cls, dh): return dh in cls.paths
    @classmethod
    def request_path(cls, dh): cls.requested.append(dh)
    @classmethod
    def register_announce_handler(cls, h): cls.handlers.append(h)


class FakeDestination:
    OUT, SINGLE = "out", "single"

    def __init__(self, ident, *_a):
        self.hash = ident.hash

    @staticmethod
    def hash(ident, app, aspect):
        # The identity's LXMF delivery address — what the service compares the
        # TYPED hash against (readiness ledger #186). A fake identity may carry
        # its own; otherwise it is the key it is known under.
        if getattr(ident, "delivery", None):
            return ident.delivery
        for k, v in FakeIdentity.known.items():
            if v is ident:
                return k
        return (app + aspect).encode()[:16]


class FakeRNS:
    Identity = FakeIdentity
    Transport = FakeTransport
    Destination = FakeDestination


class FakeLXM:
    DIRECT, PROPAGATED, OPPORTUNISTIC = "direct", "propagated", "opportunistic"
    instances = []

    def __init__(self, dest, source, content, title, desired_method=None):
        self.dest, self.source, self.content = dest, source, content
        self.desired_method = desired_method
        self.instances.append(self)

    def register_delivery_callback(self, cb): self.on_delivered = cb
    def register_failed_callback(self, cb): self.on_failed = cb


class FakeRouter:
    def __init__(self, storagepath=None):
        self.outbound, self.syncs, self.announced = [], 0, []
        self.prop_node = None

    def register_delivery_identity(self, ident, display_name=None):
        self.display_name = display_name
        return _Hash(b"\x4f" * 16)

    def register_delivery_callback(self, cb): self.delivery_cb = cb
    def set_outbound_propagation_node(self, h): self.prop_node = h
    def announce(self, h): self.announced.append(h)
    def request_messages_from_propagation_node(self, ident, max_messages=0): self.syncs += 1
    def handle_outbound(self, lxm): self.outbound.append(lxm)


class FakeLXMF:
    LXMRouter = FakeRouter
    LXMessage = FakeLXM

    @staticmethod
    def display_name_from_app_data(app_data): return app_data.decode() if app_data else None


class InMsg:
    def __init__(self, text, ts=123.0):
        self.source_hash = PEER_BYTES
        self.hash = b"\x01" * 32
        self._t, self.timestamp = text, ts

    def content_as_string(self): return self._t


@pytest.fixture
def svc(tmp_path):
    FakeTransport.paths.clear(); FakeTransport.requested.clear(); FakeTransport.handlers.clear()
    FakeIdentity.known.clear(); FakeLXM.instances.clear()
    lxmd = tmp_path / "lxmd_identity"; lxmd.write_text("lx")
    s = ChatService(MessageStore(str(tmp_path / "chat")), display_name="Bench medic",
                    propagation_probe=lambda: True,   # Home mode unless a test says otherwise
                    identity_path=str(tmp_path / "lxmf_identity"),
                    storage_path=str(tmp_path / "router"),
                    lxmd_identity_path=str(lxmd), rns=FakeRNS, lxmf=FakeLXMF,
                    path_wait_s=0.05)
    assert s.start()
    return s


def test_start_makes_one_identity_registers_and_announces_once(svc, tmp_path):
    assert os.path.exists(tmp_path / "lxmf_identity")       # created, kept
    assert svc.address == "4f" * 16
    assert svc._router.display_name == "Bench medic"
    assert svc._router.announced == [b"\x4f" * 16]
    assert svc._router.prop_node == b"lxmfpropagation"[:16]
    assert svc._router.syncs == 1                            # asked the propagation node at start
    assert any(h.aspect_filter == "lxmf.delivery" for h in FakeTransport.handlers)
    assert svc.start() is True and svc._router.announced == [b"\x4f" * 16]


def test_sync_is_rate_limited(svc):
    assert svc.tick() is False
    assert svc.tick(force=True) is True and svc._router.syncs == 2


def test_incoming_lands_in_the_store_unread(svc):
    svc._router.delivery_cb(InMsg("g'day"))
    t = svc.store.thread(PEER)
    assert t[0]["text"] == "g'day" and t[0]["read"] is False and t[0]["ts"] == 123.0
    svc._router.delivery_cb(InMsg("g'day"))                  # same LXMF hash, once
    assert len(svc.store.thread(PEER)) == 1


def test_announce_names_the_peer(svc):
    h = FakeTransport.handlers[-1]
    h.received_announce(PEER_BYTES, None, b"Marnie")
    assert svc.store.peer_name(PEER) == "Marnie"


def _wait(cond, t=2.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < t:
        time.sleep(0.02)
    return cond()


def test_direct_when_the_mesh_has_a_path(svc):
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    rec = svc.send(PEER, "  hello  ")
    assert rec["state"] == lc.SENDING and rec["text"] == "hello"
    assert _wait(lambda: svc._router.outbound)
    lxm = svc._router.outbound[0]
    assert lxm.desired_method == "direct" and lxm.content == "hello"
    lxm.on_delivered(lxm)
    assert svc.store.thread(PEER)[0]["state"] == lc.DELIVERED


def test_no_path_but_known_identity_goes_to_the_post_office(svc):
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    rec = svc.send(PEER, "hold this")
    assert _wait(lambda: svc._router.outbound)
    assert FakeTransport.requested == [PEER_BYTES]            # it did ask the mesh first
    lxm = svc._router.outbound[0]
    assert lxm.desired_method == "propagated"
    lxm.on_delivered(lxm)
    assert svc.store.thread(PEER)[0]["state"] == lc.POSTED
    # the node holding it is this medic's own lxmd — the record names it
    assert svc.store.thread(PEER)[0]["via"] == "Bench medic"


def test_a_failed_direct_send_is_retried_through_the_post_office(svc):
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    svc.send(PEER, "try twice")
    assert _wait(lambda: svc._router.outbound)
    first = svc._router.outbound[0]
    first.on_failed(first)
    # the retry is handed off the callback thread, so it lands a beat later —
    # and under a loaded full-suite run that beat has been seen to pass 2 s
    assert _wait(lambda: len(svc._router.outbound) == 2, t=6.0)
    assert svc._router.outbound[1].desired_method == "propagated"
    svc._router.outbound[1].on_delivered(None)
    assert svc.store.thread(PEER)[0]["state"] == lc.POSTED
    assert svc.store.thread(PEER)[0]["via"] == "Bench medic"


def test_a_direct_delivery_names_no_holding_node(svc):
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    svc.send(PEER, "straight there")
    assert _wait(lambda: svc._router.outbound)
    svc._router.outbound[0].on_delivered(None)
    rec = svc.store.thread(PEER)[0]
    assert rec["state"] == lc.DELIVERED and "via" not in rec


def test_an_address_nobody_announced_fails_in_words(svc):
    rec = svc.send(PEER, "into the void")
    assert _wait(lambda: svc.store.thread(PEER)[0]["state"] == lc.FAILED)
    assert svc._router.outbound == []
    assert "nobody on the mesh" in lc.STATE_WORDS[lc.FAILED]


def test_bad_input_is_refused_before_the_store(svc):
    assert svc.send("not-an-address", "x") is None
    assert svc.send(PEER, "   ") is None
    assert svc.store.conversations() == []
    assert svc.send_plain("<" + PEER + ">", "alert") is True     # decorated form accepted


def test_send_before_start_is_refused(tmp_path):
    s = ChatService(MessageStore(str(tmp_path / "c")), rns=FakeRNS, lxmf=FakeLXMF)
    assert s.send(PEER, "x") is None and s.address == ""


def test_start_failure_is_reported_not_raised(tmp_path):
    class Broken(FakeLXMF):
        class LXMRouter:
            def __init__(self, **_): raise RuntimeError("no rnsd")
    s = ChatService(MessageStore(str(tmp_path / "c")), identity_path=str(tmp_path / "id"),
                    rns=FakeRNS, lxmf=Broken)
    assert s.start() is False and "no rnsd" in s.last_error and not s.running


# -- what the other messengers taught (Sideband, MeshChat, NomadNet) ----------

def test_opportunistic_when_a_ratchet_is_known_and_no_link_is_up(svc):
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    FakeIdentity.current_ratchet_id = staticmethod(lambda dh: b"r")
    FakeRouter.delivery_link_available = lambda self, dh: False
    try:
        svc.send(PEER, "short")
        assert _wait(lambda: svc._router.outbound)
        assert svc._router.outbound[0].desired_method == "opportunistic"
    finally:
        del FakeIdentity.current_ratchet_id
        del FakeRouter.delivery_link_available


def test_direct_when_the_link_is_already_up(svc):
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    FakeIdentity.current_ratchet_id = staticmethod(lambda dh: b"r")
    FakeRouter.delivery_link_available = lambda self, dh: True
    try:
        svc.send(PEER, "on the link")
        assert _wait(lambda: svc._router.outbound)
        assert svc._router.outbound[0].desired_method == "direct"
    finally:
        del FakeIdentity.current_ratchet_id
        del FakeRouter.delivery_link_available


def test_a_failed_message_goes_again_when_the_peer_announces(svc):
    svc.send(PEER, "you there?")
    assert _wait(lambda: svc.store.thread(PEER)[0]["state"] == lc.FAILED)
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    FakeTransport.handlers[-1].received_announce(PEER_BYTES, None, b"Marnie")
    assert _wait(lambda: svc._router.outbound)
    assert svc._router.outbound[0].content == "you there?"
    assert svc.store.thread(PEER)[0]["state"] == lc.SENDING


def test_leftover_sending_from_last_session_is_marked_failed(tmp_path):
    store = MessageStore(str(tmp_path / "chat"))
    store.add_outgoing(PEER, "orphan")
    s = ChatService(store, identity_path=str(tmp_path / "id"),
                    storage_path=str(tmp_path / "r"),
                    lxmd_identity_path=str(tmp_path / "none"), rns=FakeRNS, lxmf=FakeLXMF)
    assert s.start()
    assert store.thread(PEER)[0]["state"] == lc.FAILED
    assert s.tick() is False                       # no propagation node configured


def test_reannounce_inside_sidebands_window(svc):
    from monitor.chat_service import ANNOUNCE_MIN_S, ANNOUNCE_MAX_S
    t0 = time.time()
    assert ANNOUNCE_MIN_S <= svc._next_announce - t0 <= ANNOUNCE_MAX_S + 1
    svc.tick(now=t0 + ANNOUNCE_MIN_S - 1)
    assert len(svc._router.announced) == 1
    svc.tick(now=t0 + ANNOUNCE_MAX_S + 1)
    assert len(svc._router.announced) == 2
    assert svc._next_announce > t0 + ANNOUNCE_MAX_S + 1


# -- what the forums asked for: where does it go, and when was that checked --

def test_peer_route_says_hops_interface_and_whether_known(svc):
    class _If:
        name = "RNode LoRa Interface"
    FakeTransport.hops_to = classmethod(lambda cls, dh: 2)
    FakeTransport.next_hop_interface = classmethod(lambda cls, dh: _If())
    try:
        assert svc.peer_route(PEER) == {"hops": None, "interface": "", "known": False,
                                        "is_lxmf": None}
        FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
        assert svc.peer_route(PEER)["known"] is True and svc.peer_route(PEER)["hops"] is None
        FakeTransport.paths.add(PEER_BYTES)
        assert svc.peer_route("<" + PEER + ">") == {"hops": 2, "interface": "RNode LoRa Interface",
                                                    "known": True, "is_lxmf": True}
        assert svc.peer_route("junk")["hops"] is None
    finally:
        del FakeTransport.hops_to, FakeTransport.next_hop_interface


def test_last_post_office_check_is_timestamped(svc):
    assert svc.last_sync_at > 0                          # asked at start
    t = svc.last_sync_at
    svc.tick(force=True)
    assert svc.last_sync_at >= t


def test_router_is_built_on_a_worker_thread_despite_its_signal_handlers(tmp_path):
    """LXMRouter.__init__ calls signal.signal(); off the main thread Python
    raises. The service starts on the mesh-listener thread — live failure
    2026-09-29 23:58."""
    import signal, threading

    class SignalRouter(FakeRouter):
        def __init__(self, storagepath=None):
            signal.signal(signal.SIGTERM, lambda *_: None)   # what LXMF does
            super().__init__(storagepath)

    class L(FakeLXMF):
        LXMRouter = SignalRouter

    s = ChatService(MessageStore(str(tmp_path / "c")), identity_path=str(tmp_path / "id"),
                    storage_path=str(tmp_path / "r"), lxmd_identity_path=str(tmp_path / "none"),
                    rns=FakeRNS, lxmf=L)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ok", s.start()))
    t.start(); t.join(5)
    assert out["ok"] is True and s.running, s.last_error
    assert signal.getsignal(signal.SIGTERM) is not None     # module left intact


def test_peer_route_names_rnsds_road_not_the_shared_instance(svc):
    """The UI is a client of rnsd: Transport says the next hop is the local
    shared instance. The first live exchange showed exactly that (2026-10-01).
    rnsd's path table names the real interface."""
    import json
    class _If:
        name = "LocalInterface[rns/default]"
    FakeTransport.hops_to = classmethod(lambda cls, dh: 1)
    FakeTransport.next_hop_interface = classmethod(lambda cls, dh: _If())
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    table = json.dumps([{"hash": PEER, "hops": 1, "via": PEER, "timestamp": 1.0,
                         "expires": 2.0, "interface": "RNodeInterface[RNode LoRa Interface]"}])
    svc._run = lambda cmd: table
    try:
        assert svc.peer_route(PEER) == {"hops": 1, "interface": "RNode LoRa Interface",
                                        "known": True, "is_lxmf": True}
        # within the cache window the road is served without asking rnpath again
        svc._run = lambda cmd: (_ for _ in ()).throw(AssertionError("rnpath re-run"))
        assert svc.peer_route(PEER)["interface"] == "RNode LoRa Interface"
        svc._road_cache.clear()
        svc._run = lambda cmd: "not json"                 # table unreadable: keep what we had
        assert svc.peer_route(PEER)["interface"] == "LocalInterface[rns/default]"
    finally:
        del FakeTransport.hops_to, FakeTransport.next_hop_interface


def test_the_fallback_is_never_sent_from_inside_the_routers_locked_callback(svc):
    """LXMF fires the failed-callback while holding its own lock. A router
    that takes that lock in handle_outbound deadlocks if the retry is sent
    from inside the callback — outbound chat froze for the rest of the
    session (readiness ledger #183). The retry must come from another
    thread, after the callback has returned."""
    import threading as _th
    lock = _th.RLock()
    router = svc._router
    plain_outbound = router.handle_outbound

    def locked_outbound(lxm):
        # a re-entrant call from the callback thread would pass an RLock, so
        # a plain Lock is what models LXMF here — acquire with a timeout and
        # record a deadlock instead of hanging the suite
        got = plain_lock.acquire(timeout=2.0)
        try:
            plain_outbound(lxm)
        finally:
            if got:
                plain_lock.release()
        if not got:
            deadlocks.append(lxm)

    plain_lock = _th.Lock()
    deadlocks = []
    router.handle_outbound = locked_outbound
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    svc.send(PEER, "hold the lock")
    assert _wait(lambda: router.outbound)
    first = router.outbound[0]
    # LXMF's thread: holds the lock, fires the callback, releases afterwards
    with plain_lock:
        first.on_failed(first)
        callback_returned = True
    assert callback_returned
    assert _wait(lambda: len(router.outbound) == 2), "the retry never arrived"
    assert deadlocks == [], "the retry tried to send from inside the locked callback"
    assert router.outbound[1].desired_method == "propagated"


# -- #186: a node's hash is not a messaging address ---------------------------

NODE = "d" * 32
NODE_BYTES = bytes.fromhex(NODE)


def test_a_nodes_hash_is_not_a_messaging_address_and_is_never_sent(svc):
    """recall() answers for ANY destination an identity announced — a node's
    beacon aspect, say. A message typed to that hash used to be built for the
    identity's LXMF destination (a different hash), dispatched, and reported
    'held' for ever (readiness ledger #186). Now it gets its own state word,
    is never dispatched, and an announce from the node does not resend it."""
    node_ident = FakeIdentity("nd")
    node_ident.delivery = bytes.fromhex("e" * 32)      # its real LXMF address
    FakeIdentity.known[NODE_BYTES] = node_ident
    FakeTransport.paths.add(NODE_BYTES)
    assert svc.send(NODE, "hello?") is not None
    assert _wait(lambda: svc.store.thread(NODE)[0]["state"] == lc.NOT_AN_ADDRESS)
    assert svc._router.outbound == []                 # never dispatched
    assert lc.STATE_WORDS[lc.NOT_AN_ADDRESS].startswith("not sent")
    svc._on_peer_announce(NODE_BYTES, b"")
    assert svc.store.failed_to(NODE) == []            # not a FAILED message
    assert svc._router.outbound == []
    assert svc.peer_route(NODE)["is_lxmf"] is False


def test_a_real_messaging_address_still_goes(svc):
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.add(PEER_BYTES)
    assert svc.send(PEER, "hi") is not None
    assert _wait(lambda: svc._router.outbound)
    assert svc.peer_route(PEER)["is_lxmf"] is True
