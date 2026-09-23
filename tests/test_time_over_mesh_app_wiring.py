"""The medic's side of time over the mesh, BEHAVIOURALLY (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23; review the same day: no source greps).

monitor.time_service.TimeService is driven against a fake RNS, a fake
registry and an injected clock — the disciplined-clock refusal, the Pi-kin
gate, the per-node cooldown and the in-flight cap, the send that records
only after .send() returned, the ack matched to a pending send or to the
ledger after a restart, the orphan ack's own log line, the short packet's
own log line, the opportunistic push, and the device-wide ledger lookup.
Then ui/app.py's _on_health_reply is COMPILED out of the file and run
against stubs (the way tests/test_nav_bar_wiring.py compiles _NavBar) to
prove the app hands the packet to the service first and pushes after
ingest — and that the ledger and service are built in build(), not at
class body."""
import ast
import sys
import types

import pytest

import monitor.health_reply as hr
import monitor.time_service as ts
from monitor.health_beacon import encode
from monitor.time_ledger import TimeLedger
from tests.srcutil import func_source, src

RNS = pytest.importorskip("RNS")
NODE = b"\x11" * 16
OTHER = b"\x22" * 16
T0 = 1_790_000_000.0


def _beacon(uptime=7200):
    return encode(uptime_s=uptime, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
                  wifi_up=True, lora_up=True, tcp_backbone_up=True,
                  local_tcp_server_up=True, wdt_armed=True, psram=True,
                  fault=False, board_id=0x3F, fw=(0, 7, 0))


class _FakeRNS:
    def __init__(self, recall_map, has_path=True):
        self.sent, self.requested = [], []
        fake = self
        class Identity:
            @staticmethod
            def recall(h):
                return recall_map.get(bytes(h))
        class Transport:
            @staticmethod
            def has_path(h):
                return fake.has_path
            @staticmethod
            def request_path(h):
                fake.requested.append(bytes(h))
        class Destination:
            OUT, SINGLE = "out", "single"
            def __init__(self, ident, direction, typ, app, *aspects):
                self.ident, self.name = ident, ".".join((app,) + aspects)
        class Packet:
            def __init__(self, dest, data):
                self.dest, self.data = dest, data
            def send(self):
                if fake.send_raises:
                    raise OSError("interface down")
                fake.sent.append(self)
        self.has_path, self.send_raises = has_path, False
        self.Identity, self.Transport, self.Destination, self.Packet = \
            Identity, Transport, Destination, Packet


class _Rec:
    def __init__(self, dst_hash, node_type="pi_propagation", provenance="kin"):
        self.dst_hash, self.node_type, self.provenance = dst_hash, node_type, provenance


class _Registry:
    def __init__(self, records=(), groups=None):
        self.nodes = {r.dst_hash: r for r in records}
        self._groups = groups or [[r] for r in records]

    def consolidated_records(self, now):
        return [(members[0], members) for members in self._groups]


class _Harness:
    """A TimeService with everything injected; senders run INLINE unless
    *defer* is set, in which case they are queued for the test to run."""
    def __init__(self, tmp_path, records=None, disciplined=(True, "GPS set this clock 1 min ago"),
                 recall=None, has_path=True, defer=False, groups=None):
        self.medic = RNS.Identity()
        self.node = RNS.Identity()
        self.clock = [T0]
        self.mono = [1000.0]
        self.logs = []
        self.queued = []
        self.rns = _FakeRNS(recall if recall is not None else {NODE: self.node}, has_path)
        self.reply_ident = self.medic
        self.disc = [disciplined]
        self.registry = _Registry(records if records is not None else [_Rec(NODE.hex())],
                                  groups=groups)
        self.ledger = TimeLedger(str(tmp_path / "ledger.json"), now=lambda: self.clock[0])

        def spawn(fn, *a):
            if defer:
                self.queued.append((fn, a))
            else:
                fn(*a)
        self.svc = ts.TimeService(
            self.ledger, rns=lambda: self.rns, reply_ident=lambda: self.reply_ident,
            registry=lambda: self.registry, disciplined=lambda: self.disc[0],
            now=lambda: self.clock[0], monotonic=lambda: self.mono[0],
            log=self.logs.append, spawn=spawn, warm_wait_s=0.0,
            urandom=lambda n: b"\x5a" * n)

    def run_queued(self):
        while self.queued:
            fn, a = self.queued.pop(0)
            fn(*a)

    def req(self, nonce=b"\xa1" * 8, dest=NODE):
        return hr.build_time_req(dest, nonce)

    def ack(self, nonce, status=1, before=None, dest=NODE, signer=None):
        return hr.build_time_ack(dest, nonce, before if before is not None else int(T0) - 3600,
                                 status, (signer or self.node).sign)


# -- the request --------------------------------------------------------------------

def test_a_request_from_a_pi_kin_node_is_answered_with_a_signed_time(tmp_path):
    h = _Harness(tmp_path)
    assert h.svc.inbound(h.req()) is True
    assert len(h.rns.sent) == 1
    pkt = h.rns.sent[0]
    assert pkt.dest.name == "rtnode.health" and pkt.dest.ident is h.node
    assert hr.verify_time(NODE, pkt.data, h.medic) == (int(T0), b"\xa1" * 8)
    row = h.ledger.entry(NODE.hex())
    assert row["sent_at"] == T0 and row["why"] == "asked" and row["nonce"] == (b"\xa1" * 8).hex()
    assert h.svc.pending.is_pending(b"\xa1" * 8)
    assert any("TIME_REQ from 11111111 — answering" in m for m in h.logs)
    assert any("TIME sent to 11111111 (asked)" in m for m in h.logs)
    assert h.svc.inflight() == 0


def test_a_request_from_a_stranger_or_an_rtnode_is_ignored(tmp_path):
    h = _Harness(tmp_path, records=[_Rec(NODE.hex(), node_type="rtnode2400"),
                                    _Rec(OTHER.hex(), provenance="neighbour")])
    assert h.svc.inbound(h.req()) is True and h.rns.sent == []
    assert h.svc.inbound(h.req(dest=OTHER)) is True and h.rns.sent == []
    assert h.svc.inbound(h.req(dest=b"\x33" * 16)) is True and h.rns.sent == []
    assert any("not a known Pi kin node" in m for m in h.logs)
    assert h.ledger.entry(NODE.hex()) is None
    # the registry's OLD type "pi" still counts
    h2 = _Harness(tmp_path / "b", records=[_Rec(NODE.hex(), node_type="pi")])
    (tmp_path / "b").mkdir(exist_ok=True)
    h2.svc.inbound(h2.req())
    assert len(h2.rns.sent) == 1


def test_one_answer_per_node_per_minute_and_the_log_is_rate_limited(tmp_path):
    h = _Harness(tmp_path)
    assert ts.TIME_REQ_COOLDOWN_S == 60.0
    h.svc.inbound(h.req(nonce=b"\x01" * 8))
    h.mono[0] += 30
    h.svc.inbound(h.req(nonce=b"\x02" * 8))
    assert len(h.rns.sent) == 1
    assert sum("within 60 s" in m for m in h.logs) == 1
    h.mono[0] += 1                                   # still inside both windows
    h.svc.inbound(h.req(nonce=b"\x03" * 8))
    assert sum("within 60 s" in m for m in h.logs) == 1, "the cooldown line is rate-limited"
    h.mono[0] += 60
    h.svc.inbound(h.req(nonce=b"\x04" * 8))
    assert len(h.rns.sent) == 2
    assert sum("— answering" in m for m in h.logs) == 2


def test_at_most_four_senders_in_flight_the_rest_dropped_with_one_log_line(tmp_path):
    h = _Harness(tmp_path, defer=True)
    assert ts.MAX_INFLIGHT_SENDS == 4
    dests = [bytes([i]) * 16 for i in range(1, 8)]
    h.registry = _Registry([_Rec(d.hex()) for d in dests])
    for i, d in enumerate(dests):
        h.rns.Identity.recall = staticmethod(lambda x: h.node)
        h.svc.inbound(h.req(nonce=bytes([i]) * 8, dest=d))
    assert len(h.queued) == 4 and h.svc.inflight() == 4
    assert sum("already in flight" in m for m in h.logs) == 1
    h.run_queued()
    assert h.svc.inflight() == 0 and len(h.rns.sent) == 4


# -- the send -------------------------------------------------------------------------

def test_an_undisciplined_medic_refuses_to_sign_and_records_why(tmp_path):
    h = _Harness(tmp_path, disciplined=(False, "no GPS discipline on record and NTP is not synchronised"))
    h.svc.inbound(h.req())
    assert h.rns.sent == []
    assert any(m.startswith("refusing to sign time for 11111111 (asked): no GPS") for m in h.logs)
    row = h.ledger.entry(NODE.hex())
    assert row["refused_at"] == T0 and "no GPS" in row["refused_why"]
    assert row["sent_at"] is None and not h.svc.pending.is_pending(b"\xa1" * 8)
    # discipline comes back: the same node is answered
    h.disc[0] = (True, "NTP reports the clock synchronised")
    h.mono[0] += 61
    h.svc.inbound(h.req())
    assert len(h.rns.sent) == 1


def test_an_insane_medic_clock_is_refused_even_when_disciplined_says_yes(tmp_path):
    h = _Harness(tmp_path)
    h.clock[0] = 1_000_000_000.0                       # 2001
    h.svc.inbound(h.req())
    assert h.rns.sent == [] and any("outside 2026..2100" in m for m in h.logs)


def test_the_send_needs_the_reply_identity_a_recalled_node_and_a_path(tmp_path):
    h = _Harness(tmp_path)
    h.reply_ident = None
    h.svc.send_time(NODE, b"\xa1" * 8, "asked")
    assert h.rns.sent == [] and any("reply identity not set up" in m for m in h.logs)
    h.reply_ident = h.medic
    h2 = _Harness(tmp_path / "x", recall={})
    h2.svc.send_time(NODE, b"\xa1" * 8, "asked")
    assert h2.rns.sent == [] and any("not recalled" in m for m in h2.logs)
    h3 = _Harness(tmp_path / "y", has_path=False)
    h3.svc.send_time(NODE, b"\xa1" * 8, "asked")
    assert h3.rns.sent == [] and h3.rns.requested == [NODE]
    assert any("no path from this stack" in m for m in h3.logs)
    assert h3.ledger.entry(NODE.hex()) is None, "nothing sent, nothing recorded as sent"


def test_the_ledger_records_a_send_only_after_send_returned(tmp_path):
    h = _Harness(tmp_path)
    h.rns.send_raises = True
    h.svc.send_time(NODE, b"\xa1" * 8, "asked")
    assert h.ledger.entry(NODE.hex()) is None
    assert any("failed: interface down" in m for m in h.logs)
    assert h.svc.inflight() == 0


# -- the ack --------------------------------------------------------------------------

def test_an_ack_is_verified_matched_to_its_send_and_recorded_with_its_status(tmp_path):
    h = _Harness(tmp_path)
    h.svc.inbound(h.req())
    h.clock[0] += 5
    assert h.svc.inbound(h.ack(b"\xa1" * 8, status=hr.TIME_STATUS_SET)) is True
    row = h.ledger.entry(NODE.hex())
    assert row["acked_at"] == T0 + 5 and row["status"] == 1 and row["delta_s"] == 3600
    assert not h.svc.pending.is_pending(b"\xa1" * 8)
    assert any("TIME_ACK from 11111111: clock SET" in m and "was +3600 s off" in m for m in h.logs)
    # every status has words in the log
    for status, words in ((0, "already within"), (2, "helper failed"), (3, "keeps NTP"),
                          (4, "not newer")):
        h.mono[0] += 61
        h.svc.inbound(h.req(nonce=bytes([status + 10]) * 8))
        h.svc.inbound(h.ack(bytes([status + 10]) * 8, status=status))
        assert any(words in m for m in h.logs[-1:]), (status, h.logs[-1])
        assert h.ledger.entry(NODE.hex())["status"] == status


def test_an_ack_after_a_ui_restart_matches_the_ledgers_remembered_nonce(tmp_path):
    h = _Harness(tmp_path)
    h.svc.inbound(h.req())
    # a new process: the in-memory pending table is empty, the ledger is not
    h2 = _Harness(tmp_path)
    assert not h2.svc.pending.is_pending(b"\xa1" * 8)
    assert h2.svc.classify(h.ack(b"\xa1" * 8)) == hr.KIND_TIME_ACK
    h2.rns = h.rns                                  # the node's key is recalled
    assert h2.svc.inbound(h.ack(b"\xa1" * 8)) is True
    row = h2.ledger.entry(NODE.hex())
    assert row["status"] == 1 and row["nonce"] is None
    assert any("matched the ledger's last send" in m for m in h2.logs)
    # the same ack again is now an orphan: its own line, never "unverifiable health reply"
    h2.logs.clear()
    h2.mono[0] += 100
    assert h2.svc.inbound(h.ack(b"\xa1" * 8)) is True
    assert h2.logs == ["TIME_ACK for a send this medic does not remember (nonce a1a1a1a1a1a1a1a1) — dropped"]


def test_an_ack_naming_another_node_or_signed_by_a_stranger_is_dropped(tmp_path):
    h = _Harness(tmp_path)
    h.svc.inbound(h.req())
    stranger = RNS.Identity()
    h.svc.inbound(h.ack(b"\xa1" * 8, signer=stranger))
    assert h.ledger.entry(NODE.hex())["acked_at"] is None
    assert any("unverifiable TIME_ACK dropped" in m for m in h.logs)
    # right nonce, wrong node: the ack names OTHER, whose identity is recalled
    other = RNS.Identity()
    h.rns = _FakeRNS({NODE: h.node, OTHER: other})
    h.svc.inbound(h.ack(b"\xa1" * 8, dest=OTHER, signer=other))
    assert any("sent to another node" in m for m in h.logs)
    assert h.ledger.entry(OTHER.hex()) is None
    assert h.svc.pending.is_pending(b"\xa1" * 8), "the real node's send is still waiting"


def test_short_packets_get_their_own_honest_line(tmp_path):
    h = _Harness(tmp_path)
    assert h.svc.inbound(bytes(40)) is True
    assert h.logs == ["40-byte packet on the reply destination is too short to be a health "
                      "reply (minimum 102) and is not a time packet — dropped"]
    assert h.svc.inbound(hr.make_reply(NODE, b"\x01" * 8, _beacon(), h.node.sign)) is False, \
        "a real reply is the app's to verify"


def test_pending_times_are_bounded_and_keep_for_a_day():
    clock = [0.0]
    p = ts.PendingTimes(now=lambda: clock[0])
    assert ts.PENDING_TIME_TTL_S == 24 * 3600 and ts.PENDING_TIME_MAX == 256
    for i in range(300):
        p.add(i.to_bytes(8, "big"), NODE)
    assert p.pending() == 256 and not p.is_pending((0).to_bytes(8, "big"))
    assert p.is_pending((299).to_bytes(8, "big"))
    clock[0] = 24 * 3600 + 1
    assert p.pending() == 0
    p.add(b"\x01" * 8, NODE)
    assert p.claim(b"\x01" * 8, OTHER) is None and p.claim(b"\x01" * 8, NODE) is not None


# -- the opportunistic push -----------------------------------------------------------

def test_the_push_is_pi_kin_only_six_hourly_and_stamps_tried_before_the_sender(tmp_path):
    h = _Harness(tmp_path, defer=True)
    assert h.svc.maybe_push_time(NODE) is True
    row = h.ledger.entry(NODE.hex())
    assert row["tried_at"] == T0 and row["sent_at"] is None, "tried BEFORE recall/warm"
    assert h.svc.maybe_push_time(NODE) is False, "a second reply seconds later starts nothing"
    assert len(h.queued) == 1
    h.run_queued()
    assert len(h.rns.sent) == 1 and h.ledger.entry(NODE.hex())["why"] == "push"
    h.clock[0] += 6 * 3600 - 1
    assert h.svc.maybe_push_time(NODE) is False
    h.clock[0] += 1
    assert h.svc.maybe_push_time(NODE) is True
    # an RTNode or a stranger: never
    assert h.svc.maybe_push_time(OTHER) is False
    h.registry = _Registry([_Rec(OTHER.hex(), node_type="rtnode2400")])
    assert h.svc.maybe_push_time(OTHER) is False


def test_a_failed_push_is_not_retried_on_the_next_reply(tmp_path):
    h = _Harness(tmp_path, has_path=False)
    assert h.svc.maybe_push_time(NODE) is True
    assert h.rns.sent == [] and h.ledger.entry(NODE.hex())["sent_at"] is None
    assert h.svc.maybe_push_time(NODE) is False, "tried_at gates it"


# -- the node page's lookup -----------------------------------------------------------

def test_the_page_finds_the_row_under_any_destination_of_the_device(tmp_path):
    lead, health = _Rec("rtnode:elsewhere"), _Rec(NODE.hex())
    h = _Harness(tmp_path, records=[lead, health], groups=[[lead, health]])
    assert h.svc.clock_entry_for("rtnode:elsewhere", T0) is None
    h.ledger.record_sent(NODE.hex(), int(T0), why="asked", nonce_hex="aa" * 8)
    assert h.svc.clock_entry_for("rtnode:elsewhere", T0)["why"] == "asked"
    assert h.svc.clock_entry_for(NODE.hex(), T0)["why"] == "asked"
    assert h.svc.clock_entry_for("unknown", T0) is None
    # a registry that raises: the key itself is still tried
    h.registry = None
    assert h.svc.clock_entry_for(NODE.hex(), T0)["why"] == "asked"


# -- ui/app.py: compiled, not grepped ---------------------------------------------------

APP = "ui/app.py"
CLS = "ReticulumNodeMedicApp"


def _compile_method(name, namespace):
    body = func_source(APP, name, cls=CLS)
    tree = ast.parse(body)
    code = compile(tree, f"<{APP}:{name}>", "exec")
    exec(code, namespace)
    return namespace[name]


def _stub_rns(monkeypatch, node):
    mod = types.ModuleType("RNS")
    class Identity:
        @staticmethod
        def recall(h):
            return node if bytes(h) == NODE else None
    mod.Identity = Identity
    mod.LOG_NOTICE = 5
    mod.log = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "RNS", mod)
    return mod


class _SvcSpy:
    def __init__(self, consume):
        self.consume, self.inbound_with, self.pushed = consume, [], []

    def inbound(self, data):
        self.inbound_with.append(bytes(data))
        return self.consume

    def maybe_push_time(self, dest):
        self.pushed.append(bytes(dest))
        return True


class _Reg:
    def __init__(self):
        self.nodes, self.ingested, self.probes = {}, [], []

    def ingest(self, dest_hex, beacon, now, source=None):
        self.ingested.append((dest_hex, source))

    def record_probe(self, dest_hex, ok, now):
        self.probes.append(dest_hex)


def _app_with(monkeypatch, consume):
    node = RNS.Identity()
    _stub_rns(monkeypatch, node)
    from monitor.health_reply import PendingPolls, unicast_wait_s, uptime_is_fresh, verify_reply
    ns = {"verify_reply": verify_reply, "uptime_is_fresh": uptime_is_fresh,
          "unicast_wait_s": unicast_wait_s, "print": lambda *a, **k: None}
    on_reply = _compile_method("_on_health_reply", ns)
    app = types.SimpleNamespace()
    app._time_service = _SvcSpy(consume)
    app.monitor_service = types.SimpleNamespace(registry=_Reg())
    app._pending_polls = PendingPolls()
    app._reply_reject_log = lambda msg: app.rejects.append(msg)
    app.rejects = []
    return app, on_reply, node


def test_the_callback_hands_every_packet_to_the_service_first(monkeypatch):
    app, on_reply, node = _app_with(monkeypatch, consume=True)
    req = hr.build_time_req(NODE, b"\xa1" * 8)
    on_reply(app, req, None)
    assert app._time_service.inbound_with == [req]
    assert app.monitor_service.registry.ingested == [] and app.rejects == []
    assert app._time_service.pushed == []


def test_a_health_reply_is_verified_ingested_and_then_pushed_to(monkeypatch):
    app, on_reply, node = _app_with(monkeypatch, consume=False)
    reply = hr.make_reply(NODE, b"\x01" * 8, _beacon(), node.sign)
    app._pending_polls.add(b"\x01" * 8, NODE, 1)
    on_reply(app, reply, None)
    assert app._time_service.inbound_with == [reply]
    assert app.monitor_service.registry.ingested == [(NODE.hex(), "reply")]
    assert app._time_service.pushed == [NODE], "the push comes AFTER ingest"
    assert app.monitor_service.registry.probes == [NODE.hex()]
    assert not app._pending_polls.is_pending(b"\x01" * 8)
    # a stranger's reply: rejected, never pushed to
    stranger = RNS.Identity()
    on_reply(app, hr.make_reply(NODE, b"\x02" * 8, _beacon(), stranger.sign), None)
    assert app.rejects == ["unverifiable health reply dropped"]
    assert app._time_service.pushed == [NODE]


def test_the_callback_survives_a_missing_service(monkeypatch):
    app, on_reply, node = _app_with(monkeypatch, consume=False)
    app._time_service = None
    on_reply(app, hr.build_time_req(NODE, b"\xa1" * 8), None)
    assert app.rejects == ["unverifiable health reply dropped"]


def test_the_ledger_and_service_are_built_in_build_not_at_class_body():
    text = src(APP)
    tree = ast.parse(text)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == CLS)
    class_body_assigns = {}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    class_body_assigns[t.id] = node.value
    for name in ("_time_ledger", "_time_service"):
        v = class_body_assigns[name]
        assert isinstance(v, ast.Constant) and v.value is None, f"{name} built at import"
    build = func_source(APP, "build", cls=CLS)
    assert "self._time_ledger = TimeLedger()" in build
    assert "self._time_service = TimeService(" in build
    assert "disciplined=self._medic_clock_disciplined" in build
    assert "log=self._time_log" in build
    assert "_pending_times" not in text


def test_the_medics_discipline_reads_gps_and_ntp_now(monkeypatch):
    from monitor import gps_clock
    from monitor.medic_clock import disciplined
    ns = {"_clock_disciplined": disciplined}
    fn = _compile_method("_medic_clock_disciplined", ns)
    # Patch the REAL module's function: `import provisioning.tool_datetime
    # as td` resolves through the package attribute once another test has
    # imported it, so a sys.modules stub is bypassed in a full run.
    import provisioning.tool_datetime as td
    calls = []
    monkeypatch.setattr(td, "ntp_synchronized", lambda: calls.append(1) or False)
    monkeypatch.setattr(gps_clock, "last_disciplined_at", None)
    ok, why = fn(types.SimpleNamespace())
    assert not ok and calls == [1] and "NTP is not synchronised" in why
    import time
    monkeypatch.setattr(gps_clock, "last_disciplined_at", time.time() - 60)
    ok, why = fn(types.SimpleNamespace())
    assert ok and "GPS" in why
    log = func_source(APP, "_time_log", cls=CLS)
    assert "[time]" in log and "flush=True" in log and "LOG_NOTICE" in log
