"""Time over the mesh (docs/HEALTH_REPLY_UNICAST.md, "Time over the mesh",
2026-09-23, revised after review): a solar Pi node that dies overnight
boots with no clock — no RTC, no internet, no NTP. The medic feeds it the
time over LoRa, several hops away, signed by the medic's health-reply
identity. Wire contract (status byte), the dispatcher's four answers, the
node-side policy (trust, floor, issued nonces, NTP rule, backoff), the
reporter's handler / asker / ack retry against a fake RNS, the medic's
ledger and the node-page line — pure, with real Ed25519 keys where the
existing health_reply tests use them.
"""
import json
import os
import types

import pytest

import monitor.health_reply as hr
import monitor.node_time as nt
import monitor.time_ledger as tl
from monitor.health_beacon import PAYLOAD_LEN, encode
from monitor.pi_health_reporter import (make_command_handler, make_time_asker,
                                        retry_pending_ack, run_time_asker)

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
    the remembered-nonce check is the belt to that brace."""
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


def test_time_ack_is_98_bytes_with_a_signed_status_byte():
    """The status byte (review, 2026-09-23): 0 not needed, 1 set, 2 helper
    failed, 3 refused NTP, 4 refused stale — signed raw; length stays 98.
    An old node's applied bool still builds (True -> 1)."""
    node, stranger = RNS.Identity(), RNS.Identity()
    assert hr.TIME_STATUSES == (0, 1, 2, 3, 4)
    ack = hr.build_time_ack(NODE_DEST, N1, T_GOOD - 7200, hr.TIME_STATUS_SET, node.sign)
    assert len(ack) == hr.TIME_ACK_LEN == 98 and ack[0] == 0x07
    recall = lambda d: node if d == NODE_DEST else None
    assert hr.verify_time_ack(ack, recall) == (NODE_DEST, N1, T_GOOD - 7200, 1)
    assert hr.verify_time_ack(ack, lambda d: stranger) is None
    assert hr.verify_time_ack(ack, lambda d: None) is None
    for i in (0, 1, 17, 25, 33, 97):
        t = bytearray(ack); t[i] ^= 0x01
        assert hr.verify_time_ack(bytes(t), recall) is None
    assert hr.parse_time_ack(ack[:-1]) is None
    for status in hr.TIME_STATUSES:
        a = hr.build_time_ack(NODE_DEST, N1, T_GOOD, status, node.sign)
        assert hr.verify_time_ack(a, recall)[3] == status
        assert a[33] == status
    # the old bool callers still mean 1 / 0
    assert hr.build_time_ack(NODE_DEST, N1, T_GOOD, True, node.sign)[33] == 1
    assert hr.build_time_ack(NODE_DEST, N1, T_GOOD, False, node.sign)[33] == 0
    # a status this medic does not know is still the node's signed word
    a = hr.build_time_ack(NODE_DEST, N1, T_GOOD, 9, node.sign)
    assert hr.verify_time_ack(a, recall)[3] == 9
    with pytest.raises(ValueError):
        hr.build_time_ack(NODE_DEST, N1, T_GOOD, 256, node.sign)
    assert hr.time_ack_nonce(ack) == N1 and hr.time_ack_nonce(ack[:-1]) is None


def test_inbound_dispatch_has_four_answers_decided_before_verification():
    """The medic's reply destination hears: a 25-byte 0x06 (a request — no
    reply is that short); a 98-byte 0x07 whose nonce is a send the medic
    remembers (an ack); a 98-byte 0x07 with an unknown nonce (its OWN
    answer, so the log never calls it an unverifiable health reply);
    anything shorter than MIN_REPLY_LEN (too short to be a reply, said as
    such); and the health reply."""
    node = RNS.Identity()
    req = hr.build_time_req(NODE_DEST, N1)
    ack = hr.build_time_ack(NODE_DEST, N1, T_GOOD, 1, node.sign)
    reply = hr.make_reply(NODE_DEST, N1, _beacon(), node.sign)
    known = lambda nonce: nonce == N1
    assert hr.classify_inbound(req, known) == hr.KIND_TIME_REQ == "time_req"
    assert hr.classify_inbound(req, lambda n: False) == "time_req"
    assert hr.classify_inbound(ack, known) == hr.KIND_TIME_ACK == "time_ack"
    assert hr.classify_inbound(ack, lambda n: False) == hr.KIND_TIME_ACK_UNKNOWN
    assert hr.classify_inbound(ack, lambda n: 1 / 0) == hr.KIND_TIME_ACK_UNKNOWN
    assert hr.classify_inbound(reply, known) == hr.KIND_REPLY == "reply"
    assert len(reply) >= hr.MIN_REPLY_LEN
    assert hr.classify_inbound(b"", known) == hr.KIND_SHORT
    assert hr.classify_inbound(bytes(101), known) == hr.KIND_SHORT
    assert hr.classify_inbound(bytes(102), known) == hr.KIND_REPLY
    # a 98-byte packet whose first byte is not 0x07 is never an ack: too short
    assert hr.classify_inbound(b"\x06" + ack[1:], known) == hr.KIND_SHORT


# -- the node's policy ---------------------------------------------------------

def test_epoch_sanity_window():
    assert nt.epoch_is_sane(nt.EPOCH_MIN) and nt.epoch_is_sane(T_GOOD)
    assert not nt.epoch_is_sane(nt.EPOCH_MIN - 1)
    assert not nt.epoch_is_sane(nt.EPOCH_MAX) and not nt.epoch_is_sane(0)
    assert nt.EPOCH_MIN == 1767225600 and nt.EPOCH_MAX == 4102444800   # 2026-01-01, 2100-01-01
    assert len(str(nt.EPOCH_MIN)) == 10 and len(str(nt.EPOCH_MAX - 1)) == 10


def test_the_clock_moves_only_past_the_lora_slop_which_is_an_estimate():
    assert nt.CLOCK_SLOP_S == 30
    assert nt.should_apply(T_GOOD, T_GOOD + 30) is False
    assert nt.should_apply(T_GOOD, T_GOOD - 30) is False
    assert nt.should_apply(T_GOOD, T_GOOD + 31) is True
    assert nt.should_apply(T_GOOD, T_GOOD - 86400 * 30) is True
    # the "1-3 s per hop" figure is an estimate, and the source says so
    from tests.srcutil import src
    body = src("monitor/node_time.py")
    assert "ESTIMATE" in body and "not a measurement" in body


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


def test_the_floor_is_persisted_atomically_and_refuses_older_or_equal(tmp_path):
    p = str(tmp_path / "time_state.json")
    assert nt.load_last_applied(p) is None
    assert nt.passes_floor(T_GOOD, None) is True
    assert nt.save_last_applied(T_GOOD, p) is True
    assert nt.load_last_applied(p) == T_GOOD
    assert not os.path.exists(p + ".tmp")
    assert nt.passes_floor(T_GOOD, T_GOOD) is False
    assert nt.passes_floor(T_GOOD - 1, T_GOOD) is False
    assert nt.passes_floor(T_GOOD + 1, T_GOOD) is True
    (tmp_path / "time_state.json").write_text('{"last_applied_epoch": "nope"}')
    assert nt.load_last_applied(p) is None
    (tmp_path / "time_state.json").write_text("{ broken")
    assert nt.load_last_applied(p) is None
    # an unwritable directory is False, never an exception
    assert nt.save_last_applied(T_GOOD, str(tmp_path / "time_state.json" / "x")) is False


def test_issued_nonces_expire_are_bounded_and_spend_on_match():
    clock = [1000.0]
    issued = nt.IssuedNonces(ttl_s=nt.ISSUED_NONCE_TTL_S, max_entries=3, now=lambda: clock[0])
    assert nt.ISSUED_NONCE_TTL_S == 15 * 60
    issued.issue(N1)
    assert issued.was_issued(N1) is True
    assert issued.was_issued(N1) is False, "spent on the first match"
    issued.issue(N1)
    clock[0] += nt.ISSUED_NONCE_TTL_S + 1
    assert issued.was_issued(N1) is False, "expired"
    for i in range(5):
        issued.issue(bytes([i]) * 8)
    assert issued.was_issued(bytes([0]) * 8) is False, "oldest evicted at the cap"
    assert issued.was_issued(bytes([4]) * 8) is True


def test_asking_waits_out_the_grace_repeats_re_asks_daily_and_backs_off():
    assert nt.ASK_GRACE_S == 90 and nt.ASK_EVERY_S == 600
    assert nt.RE_ASK_S == 24 * 3600 and nt.ASK_BACKOFF_AFTER == 3 and nt.ASK_BACKOFF_S == 3600
    t0 = 1000.0
    ask = lambda **kw: nt.should_ask(**dict(dict(started_at=t0, ntp_synced=False), **kw))
    assert ask(now=t0 + 89, last_ask_at=None, last_set_at=None) is False
    assert ask(now=t0 + 90, last_ask_at=None, last_set_at=None) is True
    assert ask(now=t0 + 400, last_ask_at=t0 + 90, last_set_at=None) is False
    assert ask(now=t0 + 690, last_ask_at=t0 + 90, last_set_at=None) is True
    # NTP synced: never; unreadable (None): keep asking
    assert ask(now=t0 + 690, last_ask_at=t0 + 90, last_set_at=None, ntp_synced=True) is False
    assert ask(now=t0 + 690, last_ask_at=t0 + 90, last_set_at=None, ntp_synced=None) is True
    # NO LATCH: once set, the node re-asks after RE_ASK_S while NTP is unsynced
    assert ask(now=t0 + 690, last_ask_at=t0 + 90, last_set_at=t0 + 100) is False
    assert ask(now=t0 + 100 + nt.RE_ASK_S - 1, last_ask_at=t0 + 100, last_set_at=t0 + 100) is False
    assert ask(now=t0 + 100 + nt.RE_ASK_S, last_ask_at=t0 + 100, last_set_at=t0 + 100) is True
    # backoff: three identical refusals in a row -> hourly
    assert nt.ask_interval_s(None, 2) == nt.ASK_EVERY_S
    assert nt.ask_interval_s(None, 3) == nt.ASK_BACKOFF_S
    assert ask(now=t0 + 690, last_ask_at=t0 + 90, last_set_at=None, consecutive_refusals=3) is False
    assert ask(now=t0 + 90 + 3600, last_ask_at=t0 + 90, last_set_at=None,
               consecutive_refusals=3) is True


def test_time_state_counts_identical_refusals_and_resets_on_success():
    st = nt.TimeState()
    assert st.last_set_at is None and st.last_ask_at is None and st.pending_ack is None
    st.note_refusal("no_path"); st.note_refusal("no_path")
    assert st.consecutive_refusals == 2
    st.note_refusal("not_recalled")
    assert st.consecutive_refusals == 1, "a different reason starts the count again"
    st.note_refusal("not_recalled"); st.note_refusal("not_recalled")
    assert st.consecutive_refusals == 3
    st.note_success()
    assert st.consecutive_refusals == 0 and st.last_refusal is None


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
    uses). *send_raises* makes every send fail (the ack-retry tests)."""
    def __init__(self, recall_map, has_path=True, send_raises=False):
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
        self.has_path, self.send_raises = has_path, send_raises
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


def _ht(tmp_path, medic, pkt, **kw):
    rd = bytes.fromhex(_anchor(medic)["reply_dest"])
    kw.setdefault("rns", _FakeRNS({rd: medic}))
    kw.setdefault("trust_path", _trusted(tmp_path, medic))
    kw.setdefault("state_path", str(tmp_path / "time_state.json"))
    return nt.handle_time(NODE_DEST, pkt, **kw)


def test_handle_time_applies_a_trusted_far_off_time_acks_set_and_persists_the_floor(tmp_path):
    medic = RNS.Identity()
    logs, runs = [], []
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = _ht(tmp_path, medic, pkt, now=lambda: T_GOOD - 5 * 3600,
              run=lambda argv: (runs.append(argv), (0, "ok"))[1], log=logs.append)
    assert got == (N1, T_GOOD - 5 * 3600, hr.TIME_STATUS_SET)
    assert runs == [nt.settime_argv(T_GOOD)]
    assert any("set" in m and "pushed" in m for m in logs), "no issued nonce: a push"
    assert nt.load_last_applied(str(tmp_path / "time_state.json")) == T_GOOD


def test_handle_time_says_asked_when_the_nonce_is_one_this_node_issued(tmp_path):
    medic = RNS.Identity()
    logs = []
    issued = nt.IssuedNonces()
    issued.issue(N1)
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = _ht(tmp_path, medic, pkt, now=lambda: T_GOOD - 3600,
              run=lambda argv: (0, ""), log=logs.append, issued=issued)
    assert got[2] == hr.TIME_STATUS_SET
    assert any("(asked)" in m for m in logs)
    # the same nonce again reads as pushed (spent) — and is refused by the floor
    logs.clear()
    got = _ht(tmp_path, medic, pkt, now=lambda: T_GOOD - 3600,
              run=lambda argv: (0, ""), log=logs.append, issued=issued)
    assert got[2] == hr.TIME_STATUS_REFUSED_STALE and any("(pushed)" in m for m in logs)


def test_handle_time_refuses_a_replayed_or_older_epoch_with_status_4(tmp_path):
    """The floor: a TIME whose epoch is not newer than the last one applied
    is refused — status 4, no helper call — across a restart (the file)."""
    medic = RNS.Identity()
    runs, logs = [], []
    run = lambda argv: (runs.append(argv), (0, ""))[1]
    first = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    assert _ht(tmp_path, medic, first, now=lambda: T_GOOD - 3600, run=run, log=logs.append)[2] == 1
    assert runs == [nt.settime_argv(T_GOOD)]
    # the same packet, replayed after the clock was wound back by hand
    got = _ht(tmp_path, medic, first, now=lambda: T_GOOD - 7200, run=run, log=logs.append)
    assert got == (N1, T_GOOD - 7200, hr.TIME_STATUS_REFUSED_STALE)
    older = hr.build_time(NODE_DEST, T_GOOD - 1, b"\xb2" * 8, medic.sign)
    assert _ht(tmp_path, medic, older, now=lambda: T_GOOD - 7200, run=run, log=logs.append)[2] == 4
    assert runs == [nt.settime_argv(T_GOOD)], "the clock never moved on a stale TIME"
    assert any("not newer" in m for m in logs)
    newer = hr.build_time(NODE_DEST, T_GOOD + 1, b"\xb3" * 8, medic.sign)
    assert _ht(tmp_path, medic, newer, now=lambda: T_GOOD - 7200, run=run, log=logs.append)[2] == 1
    assert nt.load_last_applied(str(tmp_path / "time_state.json")) == T_GOOD + 1


def test_handle_time_never_moves_a_clock_ntp_says_is_synchronised(tmp_path):
    medic = RNS.Identity()
    runs, logs = [], []
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = _ht(tmp_path, medic, pkt, now=lambda: T_GOOD - 5 * 3600,
              run=lambda argv: (runs.append(argv), (0, ""))[1], log=logs.append,
              ntp_synced=True)
    assert got == (N1, T_GOOD - 5 * 3600, hr.TIME_STATUS_REFUSED_NTP) and runs == []
    assert any("NTP-synchronised" in m for m in logs)
    # unreadable NTP state is NOT synchronised: the clock moves
    got = _ht(tmp_path, medic, pkt, now=lambda: T_GOOD - 5 * 3600,
              run=lambda argv: (runs.append(argv), (0, ""))[1], ntp_synced=None)
    assert got[2] == hr.TIME_STATUS_SET and runs == [nt.settime_argv(T_GOOD)]


def test_handle_time_within_slop_acks_status_0_without_touching_the_clock(tmp_path):
    medic = RNS.Identity()
    logs, runs = [], []
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = _ht(tmp_path, medic, pkt, now=lambda: T_GOOD + 12,
              run=lambda argv: (runs.append(argv), (0, ""))[1], log=logs.append)
    assert got == (N1, T_GOOD + 12, hr.TIME_STATUS_NOT_NEEDED) and runs == []
    assert any("within 30 s" in m for m in logs)
    assert nt.load_last_applied(str(tmp_path / "time_state.json")) is None, "floor only on a set"


def test_handle_time_refuses_unsigned_other_signer_untrusted_and_insane(tmp_path):
    medic, stranger = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rd = bytes.fromhex(a["reply_dest"])
    good = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    runs = []
    run = lambda argv: (runs.append(argv), (0, ""))[1]
    kw = dict(now=lambda: T_GOOD - 3600, run=run, state_path=str(tmp_path / "s.json"))
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


def test_a_failed_helper_acks_status_2_and_says_why(tmp_path):
    medic = RNS.Identity()
    logs = []
    pkt = hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign)
    got = _ht(tmp_path, medic, pkt, now=lambda: T_GOOD - 3600,
              run=lambda argv: (1, "sudo: a password is required"), log=logs.append)
    assert got == (N1, T_GOOD - 3600, hr.TIME_STATUS_HELPER_FAILED)
    assert any("password" in m for m in logs)
    assert nt.load_last_applied(str(tmp_path / "time_state.json")) is None


# -- the reporter: 0x05 in, TIME_ACK out; TIME_REQ asked ------------------------

def _handler(rns, node, tmp_path, medic, **kw):
    dest = types.SimpleNamespace(hash=NODE_DEST)
    kw.setdefault("ntp", lambda: False)
    kw.setdefault("state_path", str(tmp_path / "time_state.json"))
    return make_command_handler(rns, node, dest, announce=lambda: None,
                                current_beacon=_beacon,
                                spawn=lambda fn, *a: fn(*a), warm_wait_s=0.0,
                                trust_path=_trusted(tmp_path, medic), **kw)


def test_0x05_from_the_trusted_medic_is_applied_and_acked_by_unicast(tmp_path):
    medic, node = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic})
    runs, state, mono = [], nt.TimeState(), [500.0]
    h = _handler(rns, node, tmp_path, medic, now=lambda: T_GOOD - 7200,
                 run=lambda argv: (runs.append(argv), (0, ""))[1], time_state=state,
                 monotonic=lambda: mono[0])
    h(hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign), None)
    assert runs == [nt.settime_argv(T_GOOD)]
    assert len(rns.sent) == 1
    pkt = rns.sent[0]
    assert pkt.dest.name == "nodemedic.health.reply" and pkt.dest.ident is medic
    got = hr.verify_time_ack(pkt.data, recall=lambda d: node if d == NODE_DEST else None)
    assert got == (NODE_DEST, N1, T_GOOD - 7200, hr.TIME_STATUS_SET)
    assert state.last_set_at == 500.0, "the set is stamped MONOTONIC for the re-ask"
    assert state.pending_ack is None


def test_0x05_reads_ntp_fresh_on_the_worker_thread_and_refuses_when_synced(tmp_path):
    medic, node = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic})
    runs, reads, state = [], [], nt.TimeState()
    h = _handler(rns, node, tmp_path, medic, now=lambda: T_GOOD - 7200,
                 run=lambda argv: (runs.append(argv), (0, ""))[1], time_state=state,
                 ntp=lambda: (reads.append(1), True)[1])
    h(hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign), None)
    assert reads == [1] and runs == []
    assert hr.verify_time_ack(rns.sent[0].data, recall=lambda d: node)[3] == hr.TIME_STATUS_REFUSED_NTP
    assert state.ntp_synced is True and state.last_set_at is None


def test_0x05_from_a_stranger_moves_nothing_and_acks_nothing(tmp_path):
    medic, node, stranger = RNS.Identity(), RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic})
    runs = []
    h = _handler(rns, node, tmp_path, medic, now=lambda: T_GOOD - 7200,
                 run=lambda argv: (runs.append(argv), (0, ""))[1])
    h(hr.build_time(NODE_DEST, T_GOOD, N1, stranger.sign), None)
    assert runs == [] and rns.sent == []


def test_an_ack_that_cannot_be_sent_is_retried_once_after_the_next_announce(tmp_path):
    medic, node = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rns = _FakeRNS({bytes.fromhex(a["reply_dest"]): medic}, send_raises=True)
    state, logs = nt.TimeState(), []
    h = _handler(rns, node, tmp_path, medic, now=lambda: T_GOOD - 7200,
                 run=lambda argv: (0, ""), time_state=state, log=lambda m, lvl=None: logs.append(m))
    h(hr.build_time(NODE_DEST, T_GOOD, N1, medic.sign), None)
    assert rns.sent == [] and state.pending_ack == (N1, T_GOOD - 7200, hr.TIME_STATUS_SET)
    assert any("retry" in m for m in logs)
    # the road comes back: the announce-time retry sends it, once
    rns.send_raises = False
    dest = types.SimpleNamespace(hash=NODE_DEST)
    assert retry_pending_ack(rns, node, dest, state, trust_path=_trusted(tmp_path, medic),
                             warm_wait_s=0.0) is True
    assert len(rns.sent) == 1 and state.pending_ack is None
    assert hr.verify_time_ack(rns.sent[0].data, recall=lambda d: node)[3] == 1
    assert retry_pending_ack(rns, node, dest, state) is False, "nothing left to retry"
    # a retry that fails again is NOT kept: one retry
    rns.send_raises = True
    state.pending_ack = (N1, 1, 0)
    assert retry_pending_ack(rns, node, dest, state, trust_path=_trusted(tmp_path, medic),
                             warm_wait_s=0.0) is False
    assert state.pending_ack is None


def test_the_node_asks_for_time_only_with_trust_a_key_and_a_road(tmp_path):
    medic, node = RNS.Identity(), RNS.Identity()
    a = _anchor(medic)
    rd = bytes.fromhex(a["reply_dest"])
    dest = types.SimpleNamespace(hash=NODE_DEST)
    logs, state = [], nt.TimeState()
    rns = _FakeRNS({rd: medic})
    ask = make_time_asker(rns, dest, trust_path=_trusted(tmp_path, medic),
                          log=logs.append, warm_wait_s=0.0, time_state=state)
    assert ask() is True
    assert len(rns.sent) == 1 and rns.sent[0].dest.name == "nodemedic.health.reply"
    nd, nonce = hr.parse_time_req(rns.sent[0].data)
    assert nd == NODE_DEST and state.issued.was_issued(nonce), "the nonce is remembered"
    assert any("TIME_REQ sent" in m for m in logs) and state.consecutive_refusals == 0
    # identity not recalled -> nothing sent, said why, counted
    logs.clear()
    rns2 = _FakeRNS({})
    assert make_time_asker(rns2, dest, trust_path=_trusted(tmp_path, medic),
                           log=logs.append, warm_wait_s=0.0, time_state=state)() is False
    assert rns2.sent == [] and any("not recalled" in m for m in logs)
    assert state.consecutive_refusals == 1 and state.last_refusal == "not_recalled"
    # no road -> path requested, nothing sent
    rns3 = _FakeRNS({rd: medic}, has_path=False)
    assert make_time_asker(rns3, dest, trust_path=_trusted(tmp_path, medic),
                           log=logs.append, warm_wait_s=0.0, time_state=state)() is False
    assert rns3.sent == [] and rns3.requested == [rd]
    assert state.consecutive_refusals == 1, "a different refusal restarts the count"
    # no trust file -> nothing, said why
    logs.clear()
    assert make_time_asker(rns, dest, trust_path=str(tmp_path / "none.json"),
                           log=logs.append, warm_wait_s=0.0)() is False
    assert any("no trusted medic" in m for m in logs)


def test_the_asker_loop_re_reads_ntp_each_interval_and_uses_monotonic_time():
    """A node that lost its internet at sunset must start asking again; a
    node whose wall clock is set backwards must not fall silent."""
    state, asks, ntp_reads, logs = nt.TimeState(), [], [], []
    mono = [0.0]
    ntp_answers = [True, True, False]           # synced, synced, then lost

    def ntp():
        ntp_reads.append(mono[0])
        return ntp_answers.pop(0) if ntp_answers else False

    def sleep(_s):
        mono[0] += nt.ASK_EVERY_S               # each tick is one interval
    ticks = [0]
    run_time_asker(lambda: asks.append(mono[0]) or True, state,
                   log=lambda m, lvl=None: logs.append(m), ntp=ntp,
                   monotonic=lambda: mono[0], sleep=sleep,
                   stop=lambda: (ticks.__setitem__(0, ticks[0] + 1), ticks[0] > 4)[1])
    assert len(ntp_reads) >= 3, "NTP is re-read every interval, not once"
    assert asks, "once NTP says no, the node asks"
    assert asks[0] >= ntp_reads[2]
    assert any("NTP-synchronised" in m for m in logs) and any("asking the medic" in m for m in logs)


def test_the_reporter_wires_the_asker_thread_monotonic_heartbeat_and_notice():
    from tests.srcutil import func_source
    body = func_source("monitor/pi_health_reporter.py", "serve")
    assert "make_time_asker(" in body and "run_time_asker" in body
    assert "ntp_synchronized(" not in body, "NTP is read on the asker's thread, never the heartbeat"
    assert "time.monotonic()" in body and "next_at = time.time()" not in body
    assert "retry_pending_ack" in body
    assert "trust_path=" in body
    # journald drops VERBOSE (2026-09-22): the time lines must be NOTICE
    assert "RNS.LOG_NOTICE" in body


# -- the medic's ledger --------------------------------------------------------

def test_ledger_records_tries_sends_and_acks_and_persists(tmp_path):
    clock = [1000.0]
    p = str(tmp_path / "time_ledger.json")
    led = tl.TimeLedger(p, now=lambda: clock[0])
    d = "aa" * 16
    assert led.entry(d) is None
    led.record_tried(d)
    assert led.entry(d)["tried_at"] == 1000.0 and led.entry(d)["sent_at"] is None
    clock[0] = 1002.0
    led.record_sent(d, T_GOOD, why="asked", nonce_hex=N1.hex())
    e = led.entry(d)
    assert e["sent_at"] == 1002.0 and e["acked_at"] is None and e["why"] == "asked"
    assert e["nonce"] == N1.hex() and e["sends_since_ack"] == 1
    assert led.dest_for_nonce(N1.hex()) == d and led.dest_for_nonce("ff" * 8) is None
    clock[0] = 1004.0
    e = led.record_ack(d, before_s=T_GOOD - 5 * 3600, status=hr.TIME_STATUS_SET)
    assert e["acked_at"] == 1004.0 and e["status"] == 1 and e["applied"] is True
    assert e["delta_s"] == 5 * 3600, "how far off the node WAS, from the time we sent"
    assert e["sends_since_ack"] == 0 and e["nonce"] is None, "the nonce is spent by its ack"
    again = tl.TimeLedger(p, now=lambda: clock[0])
    assert again.entry(d) == e
    assert os.path.isfile(p) and not os.path.exists(p + ".tmp")
    led.record_refused(d, "no GPS discipline")
    assert led.entry(d)["refused_why"] == "no GPS discipline" and led.entry(d)["refused_at"] == 1004.0


def test_an_ack_for_a_node_never_sent_to_is_kept_but_marked(tmp_path):
    led = tl.TimeLedger(str(tmp_path / "l.json"), now=lambda: 5.0)
    e = led.record_ack("bb" * 16, before_s=T_GOOD, status=0)
    assert e["sent_at"] is None and e["delta_s"] is None and e["acked_at"] == 5.0


def test_push_cadence_is_six_hours_per_node_counting_tries(tmp_path):
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
    led.record_tried(d)                               # a try that never became a send
    clock[0] = 12 * 3600 - 1
    assert led.should_push(d) is False, "gated on max(sent_at, tried_at)"
    clock[0] = 12 * 3600
    assert led.should_push(d) is True
    assert led.should_push("dd" * 16) is True


def test_a_corrupt_ledger_file_is_an_empty_ledger_not_a_crash(tmp_path):
    p = tmp_path / "l.json"
    p.write_text("{ not json")
    assert tl.TimeLedger(str(p), now=lambda: 0.0).entry("aa" * 16) is None
    p.write_text('{"aa": {"sent_at": "yesterday", "acked_at": [1]}}')
    led = tl.TimeLedger(str(p), now=lambda: 0.0)
    assert led.should_push("aa") is True
    assert tl.clock_state(led.entry("aa"))[0] == "none"


def test_clock_state_reads_every_status_and_never_claims_an_unacked_set():
    assert tl.clock_state(None)[0] == "none"
    assert tl.clock_state({"sent_at": 10.0, "acked_at": None})[0] == "none"
    acked = lambda status, **kw: dict({"sent_at": 10.0, "acked_at": 14.0, "status": status,
                                       "delta_s": 3.0}, **kw)
    assert tl.clock_state(acked(0))[:3] == ("checked", 14.0, 3.0)
    assert tl.clock_state(acked(1))[:3] == ("set", 14.0, 3.0)
    assert tl.clock_state(acked(2))[0] == "failed"
    assert tl.clock_state(acked(3))[0] == "ntp"
    assert tl.clock_state(acked(4))[0] == "older"
    # old rows before the status byte
    assert tl.clock_state({"sent_at": 10.0, "acked_at": 14.0, "applied": True})[0] == "set"
    assert tl.clock_state({"sent_at": 10.0, "acked_at": 14.0, "applied": False})[0] == "checked"
    # decay: ack older than 24 h AND a later send/try unanswered
    old = acked(1, sent_at=20.0, sends_since_ack=2)
    assert tl.clock_state(old, now=14.0 + 24 * 3600 - 1)[0] == "set"
    st, at, _d, extra = tl.clock_state(old, now=14.0 + 24 * 3600 + 1)
    assert st == "stale" and at == 14.0 and extra == {"sends": 2, "tried": False}
    assert tl.clock_state(acked(1), now=14.0 + 48 * 3600)[0] == "set", "no later send: still set"
    st, _a, _d, extra = tl.clock_state(acked(1, tried_at=30.0, sends_since_ack=0),
                                       now=14.0 + 48 * 3600)
    assert st == "stale" and extra["tried"] is True
    # malformed rows never raise
    for bad in ({"acked_at": "x"}, {"acked_at": 1.0, "status": "set"}, {"acked_at": True},
                {"acked_at": 1.0, "sent_at": [], "delta_s": "?"}, "not a dict"):
        tl.clock_state(bad, now=1e9)


# -- the node page's one honest line --------------------------------------------

def test_a_stale_refusal_is_its_own_state_never_already_right():
    """Status 4 (REFUSED_STALE) means the node refused a time not newer than
    the last it applied — a replay, or the medic's clock behind. Reading it
    as "checked — already right" claimed a check nobody made (2026-09-23)."""
    from monitor.time_ledger import clock_state
    from ui.clock_line import clock_line
    row = {"sent_at": 10.0, "acked_at": 14.0, "status": 4, "delta_s": -3600.0}
    assert clock_state(row, now=100.0)[0] == "older"
    line = clock_line(row, now=lambda: 100.0)
    assert "older time" in line and "already right" not in line


def test_clock_line_wording_uses_ages_never_hhmm():
    from ui.clock_line import clock_line
    now = lambda: 100_000.0
    row = lambda **kw: dict({"sent_at": 99_000.0, "acked_at": 99_100.0, "delta_s": 4.0}, **kw)
    assert clock_line(None, now=now) == "Clock: not yet confirmed by Node Medic"
    assert clock_line({"sent_at": 1.0, "acked_at": None}, now=now) == \
        "Clock: not yet confirmed by Node Medic"
    assert clock_line(row(status=0), now=now) == "Clock: checked by Node Medic 15m ago — already right"
    assert clock_line(row(status=1, delta_s=5 * 3600.0), now=now) == \
        "Clock: set by Node Medic over the mesh 15m ago (was 5.0h off)"
    assert clock_line(row(status=1, delta_s=-900.0), now=now).endswith("(was 15m off)")
    assert clock_line(row(status=1, delta_s=None), now=now) == \
        "Clock: set by Node Medic over the mesh 15m ago", "unknown delta: no invented '1m'"
    assert clock_line(row(status=2), now=now) == \
        "Clock: Node Medic sent the time 15m ago — the node could not set it"
    assert clock_line(row(status=3), now=now) == "Clock: the node keeps NTP time — left alone"
    later = lambda: 99_100.0 + 30 * 3600
    assert clock_line(row(status=1, sent_at=99_500.0, sends_since_ack=3), now=later) == \
        "Clock: last confirmed 1d 6h ago; 3 sends since unanswered"
    assert clock_line(row(status=1, tried_at=99_500.0, sends_since_ack=0), now=later) == \
        "Clock: last confirmed 1d 6h ago; a later try did not reach the node"
    for bad in ({"acked_at": "x"}, {"acked_at": 1.0, "status": "set", "delta_s": "?"}, "row"):
        assert clock_line(bad, now=now).startswith("Clock:")
    assert clock_line(row(status=1), now=lambda: 1 / 0).startswith("Clock:")
    from tests.srcutil import src
    assert "%H:%M" not in src("ui/clock_line.py")


def test_a_real_propagation_certificate_is_a_pi_node_and_an_rtnode_is_not(tmp_path):
    """The page's condition is startswith("pi"), and it is tested through the
    real path a record takes: kin_roster.type_for_cert on a certificate's
    role -> register_device -> registry.set_kin_roster -> the record's
    node_type. A grep for `node_type == "pi"` could never have found that
    the registry's Pi type is "pi_propagation" (review, 2026-09-23)."""
    from monitor import kin_roster
    from monitor.registry import NodeRegistry
    from monitor.time_service import is_pi_node
    roster = str(tmp_path / "kin.json")
    pi_cert = {"role": "LXMF propagation node", "identity_hash": "aa" * 16}
    rt_cert = {"role": "Transport node", "identity_hash": "bb" * 16}
    assert kin_roster.type_for_cert(pi_cert) == "pi_propagation"
    for cert, h in ((pi_cert, "aa" * 16), (rt_cert, "bb" * 16)):
        kin_roster.register_device([h], "n-" + h[:2],
                                   node_type=kin_roster.type_for_cert(cert) or "rtnode2400",
                                   path=roster)
    reg = NodeRegistry()
    reg.set_kin_roster(kin_roster.load_roster(roster))
    pi, rt = reg.nodes["aa" * 16], reg.nodes["bb" * 16]
    assert pi.node_type == "pi_propagation" and rt.node_type == "rtnode2400"
    assert is_pi_node(pi.node_type) is True and is_pi_node(rt.node_type) is False
    assert is_pi_node("pi") is True and is_pi_node(None) is False
    assert pi.node_type != "pi", "the literal the page used to compare against"


def test_the_node_page_places_the_line_and_the_update_button_behind_is_pi_node():
    from tests.srcutil import func_source, src
    body = func_source("ui/screens/node_detail_screen.py", "__init__", cls="NodeDetailScreen")
    assert 'node_type == "pi"' not in body
    i = body.index("clock_line(")
    assert "is_pi_node(record.node_type)" in body[max(0, i - 900):i]
    assert "clock_entry" in body
    assert "from monitor.time_service import is_pi_node" in src("ui/screens/node_detail_screen.py")
    app = src("ui/app.py")
    assert "clock_entry=" in app and "clock_entry_for(" in app
