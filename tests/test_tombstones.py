"""Tombstone enforcement — a deleted identity STAYS deleted for 7 days.

2026-08-25, live: the operator forgot three ghost neighbours (a re-imaged
node's dead identities) and all three returned within the hour wearing their
old SEEN — the path-table rediscover and replayed announces re-created them,
because only the MAP consulted forgotten.json. These tests pin the fix: every
ingest path suppresses a buried hash, installation purges rows already
wearing one, and the tombstone file round-trips with expiry.
"""

import json

from monitor import tombstones
from monitor.registry import NodeRegistry
from monitor.health_beacon import HealthBeacon


NOW = 1_000_000.0
H = "aa" * 16


def _beacon(uptime=100):
    return HealthBeacon(uptime_s=uptime, free_heap_kb=100, wifi_rssi_dbm=-50,
                        reset_reason=0, wifi_up=True, lora_up=True,
                        tcp_backbone_up=False, local_tcp_server_up=False,
                        wdt_armed=True, psram=False, fault=False, board_id=1,
                        format_version=1, airtime_lock=False,
                        firmware_version="1.0.0")


class _MeshNode:
    def __init__(self, h):
        self.dst_hash = h
        self.hops = 2
        self.interface = "RNode LoRa"
        self.heard = NOW - 3600


def _buried_registry():
    reg = NodeRegistry()
    reg.set_tombstones({H: NOW - 60})
    return reg


def test_buried_hash_is_suppressed_on_every_ingest_path():
    reg = _buried_registry()
    assert reg.ingest(H, _beacon(), NOW) is None
    assert reg.ingest_announce(bytes.fromhex(H), b"x", NOW) is None
    assert reg.ingest_mesh(_MeshNode(H), NOW) is None
    assert reg.ingest_relay(H, "RNode LoRa", NOW, heard=NOW - 10) is None
    assert H not in reg.nodes            # nothing re-created the row


def test_expired_tombstone_lets_the_node_return():
    reg = NodeRegistry()
    reg.set_tombstones({H: NOW - tombstones.LIFETIME_S - 1})
    rec = reg.ingest(H, _beacon(), NOW)
    assert rec is not None and H in reg.nodes


def test_install_purges_rows_already_wearing_a_buried_hash():
    reg = NodeRegistry()
    reg.ingest(H, _beacon(), NOW)        # ghost row already resurrected
    other = "bb" * 16
    reg.ingest(other, _beacon(), NOW)
    reg.set_tombstones({H: NOW})
    assert H not in reg.nodes            # purged
    assert other in reg.nodes            # exact-hash only


def test_unburied_hashes_are_untouched():
    reg = _buried_registry()
    other = "bb" * 16
    assert reg.ingest(other, _beacon(), NOW) is not None


def test_bury_and_load_round_trip_with_expiry(tmp_path):
    p = str(tmp_path / "forgotten.json")
    tombstones.bury([H], path=p, now=NOW)
    assert H in tombstones.load(p, now=NOW + 60)
    assert H not in tombstones.load(p, now=NOW + tombstones.LIFETIME_S + 1)


def test_load_survives_a_corrupt_file(tmp_path):
    p = str(tmp_path / "forgotten.json")
    open(p, "w").write("not json{")
    assert tombstones.load(p) == {}
    open(p, "w").write(json.dumps({"h": "notafloat"}))
    assert tombstones.load(p) == {}
