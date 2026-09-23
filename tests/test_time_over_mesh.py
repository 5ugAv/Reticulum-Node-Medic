"""Time over the mesh (docs/HEALTH_REPLY_UNICAST.md, "Time over the mesh",
2026-09-23): a solar Pi node that dies overnight boots with no clock — no
RTC, no internet, no NTP. The medic feeds it the time over LoRa, several
hops away, signed by the medic's health-reply identity. Wire contract,
node-side policy, the medic's ledger and the node-page line — pure, with
real Ed25519 keys where the existing health_reply tests use them.
"""
import json
import os
import types

import pytest

import monitor.health_reply as hr
import monitor.node_time as nt
import monitor.time_ledger as tl
from monitor.health_beacon import PAYLOAD_LEN, encode
from monitor.pi_health_reporter import make_command_handler, make_time_asker

RNS = pytest.importorskip("RNS")
NODE_DEST = b"\x11" * 16
MEDIC_REPLY = b"\x99" * 16
N1 = b"\xa1" * 8
T_GOOD = 1_790_000_000            # 2026-09-21T...Z, inside the sane window


def _beacon(uptime=7200):
    return encode(uptime_s=uptime, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
                  wifi_up=True, lora_up=True, tcp_backbone_up=True,
                  local_tcp_server_up=True, wdt_armed=True, psram=True,
                  fault=False, board_id=0x3F, fw=(0, 7, 0))


# -- wire ----------------------------------------------------------------------

def test_time_req_is_exactly_25_bytes_and_a_reply_can_never_be():
    req = hr.build_time_req(NODE_DEST, N1)
    assert req == bytes([0x06]) + NODE_DEST + N1 and len(req) == hr.TIME_REQ_LEN == 25
    assert hr.parse_time_req(req) == (NODE_DEST, N1)
    # a health reply is dest[16] | nonce[8] | beacon | sig[64]: > 88 bytes always
    assert hr.split_reply(req) is None
    assert hr.split_reply(b"\x06" + bytes(87)) is None
    assert hr.parse_time_req(bytes([0x04]) + NODE_DEST + N1) is None     # a 0x04 request
    assert hr.parse_time_req(req + b"\x00") is None


def test_the_shortest_valid_reply_is_longer_than_a_time_ack():
    """A decodable beacon is at least PAYLOAD_LEN (14) bytes, so no reply
    the medic could ever verify AND decode is shorter than 102 bytes — the
    98-byte TIME_ACK sits below that floor. Length alone tells them apart;
    the pending-nonce check is the belt to that brace."""
    assert hr.MIN_REPLY_LEN == 16 + 8 + PAYLOAD_LEN + 64 == 102
    assert hr.TIME_ACK_LEN == 98 < hr.MIN_REPLY_LEN


def test_time_is_signed_by_the_medic_over_the_nodes_own_destination():
    medic, stranger = RNS.Identity(), RNS.Identity()
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    assert len(pkt) == hr.TIME_LEN == 81 and pkt[0] == 0x05
    assert hr.verify_time(NODE_DEST, pkt, medic) == (T_GOOD, N1)
    assert hr.verify_time(NODE_DEST, pkt, stranger) is None
    assert hr.verify_time(b"\x22" * 16, pkt, medic) is None       # meant for another node
    for i in (0, 1, 8, 9, 16, 17, 80):
        t = bytearray(pkt); t[i] ^= 0x01
        assert hr.verify_time(NODE_DEST, bytes(t), medic) is None
    assert hr.verify_time(NODE_DEST, pkt[:-1], medic) is None
    assert hr.verify_time(NODE_DEST, pkt, None) is None


def test_time_ack_is_98_bytes_signed_by_the_node():
    node, stranger = RNS.Identity(), RNS.Identity()
    ack = hr.build_time_ack(NODE_DEST, N1, T_GOOD - 7200, True, node.sign)
    assert len(ack) == hr.TIME_ACK_LEN == 98 and ack[0] == 0x07
    recall = lambda d: node if d == NODE_DEST else None
    assert hr.verify_time_ack(ack, recall) == (NODE_DEST, N1, T_GOOD - 7200, True)
    assert hr.verify_time_ack(ack, lambda d: stranger) is None
    assert hr.verify_time_ack(ack, lambda d: None) is None
    for i in (0, 1, 17, 25, 33, 97):
        t = bytearray(ack); t[i] ^= 0x01
        assert hr.verify_time_ack(bytes(t), recall) is None
    assert hr.parse_time_ack(ack[:-1]) is None
    no = hr.build_time_ack(NODE_DEST, N1, T_GOOD, False, node.sign)
    assert hr.verify_time_ack(no, recall)[3] is False


def test_inbound_dispatch_order_request_then_pending_ack_then_reply():
    """The medic's reply destination hears three things. A 25-byte 0x06 is
    a request (no reply is that short). A 98-byte 0x07 is an ack ONLY when
    its nonce is a TIME the medic is waiting on; otherwise it goes down the
    reply path (where verification refuses it) — the ordering pinned here."""
    node = RNS.Identity()
    req = hr.build_time_req(NODE_DEST, N1)
    ack = hr.build_time_ack(NODE_DEST, N1, T_GOOD, True, node.sign)
    reply = hr.make_reply(NODE_DEST, N1, _beacon(), node.sign)
    pending = lambda nonce: nonce == N1
    assert hr.classify_inbound(req, pending) == "time_req"
    assert hr.classify_inbound(req, lambda n: False) == "time_req"
    assert hr.classify_inbound(ack, pending) == "time_ack"
    assert hr.classify_inbound(ack, lambda n: False) == "reply"        # unknown nonce
    assert hr.classify_inbound(reply, pending) == "reply"
    assert hr.classify_inbound(b"", pending) == "reply"
    # a 98-byte packet whose first byte is not 0x07 is never an ack
    assert hr.classify_inbound(b"\x06" + ack[1:], pending) == "reply"


# -- the node's policy ---------------------------------------------------------

def test_epoch_sanity_window():
    assert nt.epoch_is_sane(nt.EPOCH_MIN) and nt.epoch_is_sane(T_GOOD)
    assert not nt.epoch_is_sane(nt.EPOCH_MIN - 1)
    assert not nt.epoch_is_sane(nt.EPOCH_MAX) and not nt.epoch_is_sane(0)
    assert nt.EPOCH_MIN == 1767225600 and nt.EPOCH_MAX == 4102444800   # 2026-01-01, 2100-01-01
    assert len(str(nt.EPOCH_MIN)) == 10 and len(str(nt.EPOCH_MAX - 1)) == 10


def test_the_clock_moves_only_past_the_lora_slop():
    assert nt.CLOCK_SLOP_S == 30
    assert nt.should_apply(T_GOOD, T_GOOD + 30) is False
    assert nt.should_apply(T_GOOD, T_GOOD - 30) is False
    assert nt.should_apply(T_GOOD, T_GOOD + 31) is True
    assert nt.should_apply(T_GOOD, T_GOOD - 86400 * 30) is True


def test_the_clock_is_set_only_through_the_root_helper():
    assert nt.settime_argv(T_GOOD) == ["sudo", "-n", "/usr/local/sbin/nm-settime", str(T_GOOD)]
    calls = []
    ok, out = nt.apply_time(T_GOOD, run=lambda argv: (calls.append(argv), (0, "Mon 21 Sep 2026 UTC"))[1])
    assert ok and calls == [nt.settime_argv(T_GOOD)] and "2026" in out
    ok, out = nt.apply_time(T_GOOD, run=lambda argv: (1, "sudo: a password is required"))
    assert not ok and "password" in out
    ok, out = nt.apply_time(T_GOOD, run=lambda argv: (_ for _ in ()).throw(OSError("no sudo")))
    assert not ok and "no sudo" in out


def test_ntp_state_is_read_from_timedatectl_and_never_guessed():
    assert nt.parse_ntp_synchronized("yes\n") is True
    assert nt.parse_ntp_synchronized("no\n") is False
    assert nt.parse_ntp_synchronized("") is None
    assert nt.parse_ntp_synchronized("garbage") is None
    assert nt.ntp_synchronized(run=lambda argv: (0, "no\n")) is False
    assert nt.ntp_synchronized(run=lambda argv: (1, "")) is None
    assert nt.ntp_synchronized(run=lambda argv: (_ for _ in ()).throw(OSError())) is None


def test_asking_waits_out_the_grace_then_repeats_until_set_or_synced():
    assert nt.ASK_GRACE_S == 90 and nt.ASK_EVERY_S == 600
    t0 = 1000.0
    assert nt.should_ask(now=t0 + 89, started_at=t0, last_ask_at=None,
                         clock_set=False, ntp_synced=False) is False
    assert nt.should_ask(now=t0 + 90, started_at=t0, last_ask_at=None,
                         clock_set=False, ntp_synced=False) is True
    assert nt.should_ask(now=t0 + 400, started_at=t0, last_ask_at=t0 + 90,
                         clock_set=False, ntp_synced=False) is False
    assert nt.should_ask(now=t0 + 690, started_at=t0, last_ask_at=t0 + 90,
                         clock_set=False, ntp_synced=False) is True
    assert nt.should_ask(now=t0 + 690, started_at=t0, last_ask_at=t0 + 90,
                         clock_set=True, ntp_synced=False) is False
    assert nt.should_ask(now=t0 + 690, started_at=t0, last_ask_at=t0 + 90,
                         clock_set=False, ntp_synced=True) is False
    # NTP state unreadable (None) is not "synced": keep asking
    assert nt.should_ask(now=t0 + 690, started_at=t0, last_ask_at=t0 + 90,
                         clock_set=False, ntp_synced=None) is True


# -- the trust anchor ----------------------------------------------------------

def _anchor(medic):
    reply_dest = RNS.Destination.hash(medic, hr.REPLY_APP, *hr.REPLY_ASPECTS)
    return nt.trust_anchor(medic.hash.hex(), reply_dest.hex(), "nodemedic", "2026-09-23")


def test_trust_file_is_canonical_json_and_validated_on_load(tmp_path):
    medic = RNS.Identity()
    a = _anchor(medic)
    text = nt.trust_anchor_json(a)
    assert text.endswith("\n") and json.loads(text) == a
    assert list(json.loads(text)) == sorted(a)               # sort_keys: read-back compares bytes
    p = tmp_path / "trusted_medic.json"
    p.write_text(text)
    assert nt.load_trust(str(p)) == a
    assert nt.load_trust(str(tmp_path / "missing.json")) is None
    p.write_text('{"identity_hash": "zz", "reply_dest": "zz"}')
    assert nt.load_trust(str(p)) is None                      # not hashes: no trust
    with pytest.raises(ValueError):
        nt.trust_anchor("abc", a["reply_dest"], "n", "2026-09-23")


class _FakeRNS:
    """Enough of RNS for the node: recall by destination hash, has_path /
    request_path, an OUT destination that remembers its identity, and a
    Packet that records what was sent (the shape test_health_reply_wiring
    uses)."""
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
                return has_path
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
                fake.sent.append(self)
        self.Identity, self.Transport = Identity, Transport
        self.Destination, self.Packet = Destination, Packet


def _trusted(tmp_path, medic):
    p = tmp_path / "trusted_medic.json"
    p.write_text(nt.trust_anchor_json(_anchor(medic)))
    return str(p)


def test_recall_requires_the_anchored_identity_behind_the_reply_destination(tmp_path):
    medic, other = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rd = bytes.fromhex(a["reply_dest"])
    assert nt.recall_trusted_medic(_FakeRNS({rd: medic}), a) is medic
    assert nt.recall_trusted_medic(_FakeRNS({rd: other}), a) is None   # same dest, other key
    assert nt.recall_trusted_medic(_FakeRNS({}), a) is None            # no announce heard yet


def test_handle_time_applies_a_trusted_far_off_time_and_acks_it(tmp_path):
    medic = RNS.Identity()
    a = _anchor(medic)
    rd = bytes.fromhex(a["reply_dest"])
    rns = _FakeRNS({rd: medic})
    logs, runs = [], []
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = nt.handle_time(NODE_DEST, pkt, rns, trust_path=_trusted(tmp_path, medic),
                         now=lambda: T_GOOD - 5 * 3600,
                         run=lambda argv: (runs.append(argv), (0, "ok"))[1],
                         log=logs.append)
    assert got == (N1, T_GOOD - 5 * 3600, True)
    assert runs == [nt.settime_argv(T_GOOD)]
    assert any("set" in m for m in logs)


def test_handle_time_within_slop_acks_applied_0_without_touching_the_clock(tmp_path):
    medic = RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic})
    logs, runs = [], []
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = nt.handle_time(NODE_DEST, pkt, rns, trust_path=_trusted(tmp_path, medic),
                         now=lambda: T_GOOD + 12,
                         run=lambda argv: (runs.append(argv), (0, ""))[1], log=logs.append)
    assert got == (N1, T_GOOD + 12, False) and runs == []
    assert any("within 30 s" in m for m in logs)


def test_handle_time_refuses_unsigned_other_signer_untrusted_and_insane(tmp_path):
    medic, stranger = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rd = bytes.fromhex(a["reply_dest"])
    good = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    runs = []
    run = lambda argv: (runs.append(argv), (0, ""))[1]
    kw = dict(now=lambda: T_GOOD - 3600, run=run)
    trust = _trusted(tmp_path, medic)
    # no trust file
    logs = []
    assert nt.handle_time(NODE_DEST, good, _FakeRNS({rd: medic}),
                          trust_path=str(tmp_path / "none.json"), log=logs.append, **kw) is None
    assert any("no trusted medic" in m for m in logs)
    # identity not recalled yet
    logs = []
    assert nt.handle_time(NODE_DEST, good, _FakeRNS({}), trust_path=trust,
                          log=logs.append, **kw) is None
    assert any("not recalled" in m for m in logs)
    # another signer
    bad = hr.build_time(NODE_DEST, T_GOOD, N1, stranger.sign)
    assert nt.handle_time(NODE_DEST, bad, _FakeRNS({rd: medic}), trust_path=trust,
                          log=lambda m: None, **kw) is None
    # unsigned / malformed
    assert nt.handle_time(NODE_DEST, good[:-64], _FakeRNS({rd: medic}), trust_path=trust,
                          log=lambda m: None, **kw) is None
    # signed but insane epochs
    for t in (nt.EPOCH_MIN - 1, nt.EPOCH_MAX, 0):
        pkt = hr.build_time(NODE_DEST, t, N1, medic.sign)
        assert nt.handle_time(NODE_DEST, pkt, _FakeRNS({rd: medic}), trust_path=trust,
                              log=lambda m: None, **kw) is None
    assert runs == [], "the clock never moved"


def test_a_failed_helper_acks_applied_0_and_says_why(tmp_path):
    medic = RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic})
    logs = []
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = nt.handle_time(NODE_DEST, pkt, rns, trust_path=_trusted(tmp_path, medic),
                         now=lambda: T_GOOD - 3600,
                         run=lambda argv: (1, "sudo: a password is required"), log=logs.append)
    assert got == (N1, T_GOOD - 3600, False)
    assert any("password" in m for m in logs)


# -- the reporter: 0x05 in, TIME_ACK out; TIME_REQ asked ------------------------

def _handler(rns, node, tmp_path, medic, **kw):
    dest = types.SimpleNamespace(hash=NODE_DEST)
    return make_command_handler(rns, node, dest, announce=lambda: None,
                                current_beacon=_beacon,
                                spawn=lambda fn, *a: fn(*a), warm_wait_s=0.0,
                                trust_path=_trusted(tmp_path, medic), **kw)


def test_0x05_from_the_trusted_medic_is_applied_and_acked_by_unicast(tmp_path):
    medic, node = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic})
    runs, state = [], nt.TimeState()
    h = _handler(rns, node, tmp_path, medic, now=lambda: T_GOOD - 7200,
                 run=lambda argv: (runs.append(argv), (0, ""))[1], time_state=state)
    h(hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign), None)
    assert runs == [nt.settime_argv(T_GOOD)]
    assert len(rns.sent) == 1
    pkt = rns.sent[0]
    assert pkt.dest.name == "nodemedic.health.reply" and pkt.dest.ident is medic
    got = hr.verify_time_ack(pkt.data, recall=lambda d: node if d == NODE_DEST else None)
    assert got == (NODE_DEST, N1, T_GOOD - 7200, True)
    assert state.clock_set is True, "stop asking once set"


def test_0x05_from_a_stranger_moves_nothing_and_acks_nothing(tmp_path):
    medic, node, stranger = RNS.Identity(), RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic})
    runs = []
    h = _handler(rns, node, tmp_path, medic, now=lambda: T_GOOD - 7200,
                 run=lambda argv: (runs.append(argv), (0, ""))[1])
    h(hr.build_time(NODE_DEST, T_GOOD, N1, stranger.sign), None)
    assert runs == [] and rns.sent == []


def test_the_node_asks_for_time_only_with_trust_a_key_and_a_road(tmp_path):
    medic, node = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rd = bytes.fromhex(a["reply_dest"])
    dest = types.SimpleNamespace(hash=NODE_DEST)
    logs = []
    rns = _FakeRNS({rd: medic})
    ask = make_time_asker(rns, dest, trust_path=_trusted(tmp_path, medic),
                          log=logs.append, warm_wait_s=0.0)
    assert ask() is True
    assert len(rns.sent) == 1 and rns.sent[0].dest.name == "nodemedic.health.reply"
    assert hr.parse_time_req(rns.sent[0].data)[0] == NODE_DEST
    assert any("TIME_REQ sent" in m for m in logs)
    # identity not recalled -> nothing sent, said why
    logs.clear()
    rns2 = _FakeRNS({})
    assert make_time_asker(rns2, dest, trust_path=_trusted(tmp_path, medic),
                           log=logs.append, warm_wait_s=0.0)() is False
    assert rns2.sent == [] and any("not recalled" in m for m in logs)
    # no road -> path requested, nothing sent
    rns3 = _FakeRNS({rd: medic}, has_path=False)
    assert make_time_asker(rns3, dest, trust_path=_trusted(tmp_path, medic),
                           log=logs.append, warm_wait_s=0.0)() is False
    assert rns3.sent == [] and rns3.requested == [rd]
    # no trust file -> nothing, said why
    logs.clear()
    assert make_time_asker(rns, dest, trust_path=str(tmp_path / "none.json"),
                           log=logs.append, warm_wait_s=0.0)() is False
    assert any("no trusted medic" in m for m in logs)


def test_the_reporter_wires_the_asker_into_serve_at_notice():
    from tests.srcutil import func_source
    body = func_source("monitor/pi_health_reporter.py", "serve")
    assert "make_time_asker(" in body and "should_ask(" in body
    assert "ntp_synchronized(" in body
    assert "trust_path=" in body
    # journald drops VERBOSE (2026-09-22): the time lines must be NOTICE
    assert "RNS.LOG_NOTICE" in body


# -- the medic's ledger --------------------------------------------------------

def test_ledger_records_sends_and_acks_and_persists(tmp_path):
    clock = [1000.0]
    p = str(tmp_path / "time_ledger.json")
    led = tl.TimeLedger(p, now=lambda: clock[0])
    d = "aa" * 16
    assert led.entry(d) is None
    led.record_sent(d, T_GOOD, why="asked")
    e = led.entry(d)
    assert e["sent_at"] == 1000.0 and e["acked_at"] is None and e["why"] == "asked"
    clock[0] = 1004.0
    e = led.record_ack(d, before_s=T_GOOD - 5 * 3600, applied=True)
    assert e["acked_at"] == 1004.0 and e["applied"] is True
    assert e["delta_s"] == 5 * 3600, "how far off the node WAS, from the time we sent"
    again = tl.TimeLedger(p, now=lambda: clock[0])
    assert again.entry(d) == e
    assert os.path.isfile(p) and not os.path.exists(p + ".tmp")


def test_an_ack_for_a_node_never_sent_to_is_kept_but_marked(tmp_path):
    led = tl.TimeLedger(str(tmp_path / "l.json"), now=lambda: 5.0)
    e = led.record_ack("bb" * 16, before_s=T_GOOD, applied=False)
    assert e["sent_at"] is None and e["delta_s"] is None and e["acked_at"] == 5.0


def test_push_cadence_is_six_hours_per_node(tmp_path):
    assert tl.TIME_PUSH_EVERY_S == 6 * 3600
    clock = [0.0]
    led = tl.TimeLedger(str(tmp_path / "l.json"), now=lambda: clock[0])
    d = "cc" * 16
    assert led.should_push(d) is True                 # never sent
    led.record_sent(d, T_GOOD, why="push")
    clock[0] = 6 * 3600 - 1
    assert led.should_push(d) is False
    clock[0] = 6 * 3600
    assert led.should_push(d) is True
    assert led.should_push("dd" * 16) is True


def test_a_corrupt_ledger_file_is_an_empty_ledger_not_a_crash(tmp_path):
    p = tmp_path / "l.json"
    p.write_text("{ not json")
    assert tl.TimeLedger(str(p), now=lambda: 0.0).entry("aa" * 16) is None


def test_clock_state_never_claims_a_set_that_was_not_acked():
    assert tl.clock_state(None) == ("none", None, None)
    sent_only = {"sent_at": 10.0, "acked_at": None, "applied": None, "delta_s": None}
    assert tl.clock_state(sent_only) == ("none", None, None)
    acked_no = {"sent_at": 10.0, "acked_at": 14.0, "applied": False, "delta_s": 3.0}
    assert tl.clock_state(acked_no) == ("checked", 14.0, 3.0)
    acked_yes = {"sent_at": 10.0, "acked_at": 14.0, "applied": True, "delta_s": 18000.0}
    assert tl.clock_state(acked_yes) == ("set", 14.0, 18000.0)


# -- the node page's one honest line --------------------------------------------

def test_clock_line_wording():
    from ui.clock_line import clock_line
    hhmm = lambda t: "07:42"
    assert clock_line(None, strftime=hhmm) == "Clock: not yet given by Node Medic"
    assert clock_line({"sent_at": 1.0, "acked_at": None, "applied": None, "delta_s": None},
                      strftime=hhmm) == "Clock: not yet given by Node Medic"
    assert clock_line({"sent_at": 1.0, "acked_at": 2.0, "applied": False, "delta_s": 4.0},
                      strftime=hhmm) == "Clock: checked by Node Medic at 07:42 — already right"
    line = clock_line({"sent_at": 1.0, "acked_at": 2.0, "applied": True, "delta_s": 5 * 3600.0},
                      strftime=hhmm)
    assert line == "Clock: set by Node Medic over the mesh at 07:42 (was 5.0h off)"
    line = clock_line({"sent_at": 1.0, "acked_at": 2.0, "applied": True, "delta_s": -900.0},
                      strftime=hhmm)
    assert "(was 15m off)" in line


def test_the_node_page_shows_the_line_for_pi_nodes_only():
    from tests.srcutil import func_source
    body = func_source("ui/screens/node_detail_screen.py", "__init__", cls="NodeDetailScreen")
    i = body.index("clock_line(")
    assert 'node_type == "pi"' in body[max(0, i - 600):i]
    assert "clock_entry" in body
    app = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui/app.py")).read()
    assert "clock_entry=" in app and "_time_ledger.entry(" in app
