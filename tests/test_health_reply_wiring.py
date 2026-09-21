"""The unicast health reply — node side and registry side
(docs/HEALTH_REPLY_UNICAST.md, 2026-09-21)."""
import os
import stat
import types

import pytest

import monitor.health_reply as hr
from monitor.health_beacon import decode, encode
from monitor.mesh import MeshNode
from monitor.pi_health_reporter import make_command_handler
from monitor.registry import NodeRegistry

RNS = pytest.importorskip("RNS")


def _beacon(uptime=7200):
    return encode(uptime_s=uptime, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
                  wifi_up=True, lora_up=True, tcp_backbone_up=True,
                  local_tcp_server_up=True, wdt_armed=True, psram=True,
                  fault=False, board_id=0x3F, fw=(0, 7, 0))


# -- the medic's reply identity ---------------------------------------------

def test_reply_identity_is_created_once_with_mode_0600(tmp_path):
    p = str(tmp_path / "health_reply_identity")
    a = hr.load_or_create_identity(RNS, p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert not os.path.exists(p + ".tmp")
    assert hr.load_or_create_identity(RNS, p).hash == a.hash


# -- the registry ------------------------------------------------------------

def test_the_medics_own_destination_is_never_a_neighbour():
    reg = NodeRegistry()
    reg.set_own_destinations({"AA" * 16})
    assert reg.ingest_mesh(MeshNode(dst_hash="aa" * 16, hops=0,
                                    interface="LocalInterface[rns/default]",
                                    heard=100.0), now=100.0) is None
    assert reg.ingest_mesh(MeshNode(dst_hash="bb" * 16, hops=1,
                                    interface="RNodeInterface[x]", heard=100.0),
                           now=100.0) is not None
    assert [r.dst_hash for r in reg.all(now=100.0)] == ["bb" * 16]


def test_a_unicast_reply_ingests_as_the_nodes_own_word_with_its_source():
    reg = NodeRegistry()
    rec = reg.ingest("cc" * 16, decode(_beacon()), now=500.0, source="reply")
    assert rec.last_heard_announce_at == 500.0
    assert rec.seen.source == "reply"
    rec.late_reply_note = "answered 48 s later via relay"
    assert rec.late_reply_note


# -- the Pi node's handler ----------------------------------------------------

class _FakeRNS:
    """Just enough of RNS for the handler: recall, has_path/request_path,
    an OUT destination that remembers what it was built from, and a Packet
    that records what was sent."""
    def __init__(self, medic_identity, known=True, has_path=True):
        self.sent = []
        self.requested = []
        fake = self
        class Identity:
            @staticmethod
            def recall(h):
                return medic_identity if known else None
        class Transport:
            @staticmethod
            def has_path(h):
                return has_path
            @staticmethod
            def request_path(h):
                fake.requested.append(h)
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


def _handler(rns, node_identity, announces):
    dest = types.SimpleNamespace(hash=b"\x11" * 16)
    return make_command_handler(rns, node_identity, dest,
                                announce=lambda: announces.append(1),
                                current_beacon=_beacon,
                                spawn=lambda fn, *a: fn(*a), warm_wait_s=0.0)


def test_0x04_answers_by_unicast_the_medic_can_verify():
    medic, node = RNS.Identity(), RNS.Identity()
    rns = _FakeRNS(medic)
    announces = []
    on_command = _handler(rns, node, announces)
    on_command(hr.build_request_to(b"\x99" * 16, b"\xa1" * 8), None)
    assert len(rns.sent) == 1 and announces == []
    pkt = rns.sent[0]
    assert pkt.dest.name == "nodemedic.health.reply" and pkt.dest.ident is medic
    got = hr.verify_reply(pkt.data, recall=lambda d: node if d == b"\x11" * 16 else None)
    assert got is not None
    dest, nonce, beacon = got
    assert dest == b"\x11" * 16 and nonce == b"\xa1" * 8
    assert decode(beacon).uptime_s == 7200


def test_0x04_falls_back_to_an_announce_without_a_key_or_a_road():
    medic, node = RNS.Identity(), RNS.Identity()
    for known, has_path in ((False, True), (True, False)):
        rns = _FakeRNS(medic, known=known, has_path=has_path)
        announces = []
        _handler(rns, node, announces)(hr.build_request_to(b"\x99" * 16, b"\xa1" * 8), None)
        assert rns.sent == [] and announces == [1]
        if not has_path:
            assert rns.requested, "asked for a road so the next poll is unicast"


def test_0x01_announces_and_junk_is_ignored():
    medic, node = RNS.Identity(), RNS.Identity()
    rns = _FakeRNS(medic)
    announces = []
    h = _handler(rns, node, announces)
    h(bytes([0x01]), None)
    h(b"\x04\x00", None)            # malformed 0x04
    h(b"\x7f", None)                 # unknown opcode
    h(b"", None)
    assert announces == [1] and rns.sent == []
