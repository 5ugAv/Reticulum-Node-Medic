"""Settings' revoke actually demotes nodes on VITALS/SCAN.

The trust store (monitor.trust) is per-unit and non-transitive; the kin roster
knows which UNIT birthed each of the medic's own nodes (its ``builder``). Wiring
the two means: revoking a builder unit flips its birthed nodes from kin to
neighbour in the registry. These tests pin that end-to-end wiring — the
non-transitivity itself lives in (and is tested by) test_trust.py.
"""

import pytest

from monitor import trust
from monitor.registry import NodeRegistry
from monitor.mesh import MeshNode

NODE = "02675080aa11223344556677889900aa"
SELF_UNIT = "aaaaselfunit"
FRIEND_UNIT = "bbbbfriendunit"


@pytest.fixture
def trust_path(tmp_path, monkeypatch):
    """A throwaway trust store; NEVER touch the operator's real ~/.reticulum-...
    NodeRecord.provenance reads ``trust.CONFIG`` at call time, so redirect it."""
    p = str(tmp_path / "trust.json")
    monkeypatch.setattr(trust, "CONFIG", p)
    return p


def test_kin_roster_node_built_by_self_is_kin(trust_path):
    trust.set_self(SELF_UNIT, "This Medic", path=trust_path)
    reg = NodeRegistry()
    reg.set_kin_roster({NODE: {"name": "FAITH", "type": "rtnode2400",
                               "builder": SELF_UNIT}})
    rec = reg.get(NODE)
    assert rec.builder_hash == SELF_UNIT
    assert rec.provenance == "kin"           # self-built => kin


def test_revoking_the_builder_demotes_its_nodes_to_neighbour(trust_path):
    # THE key spec: a node birthed by a trusted (friend) unit reads kin...
    trust.set_self(SELF_UNIT, "Origin", path=trust_path)
    trust.record_child_clone(FRIEND_UNIT, "Friend", parent_hash=SELF_UNIT,
                             path=trust_path)
    reg = NodeRegistry()
    reg.set_kin_roster({NODE: {"name": "FRIENDS NODE", "type": "rtnode2400",
                               "builder": FRIEND_UNIT}})
    assert reg.get(NODE).provenance == "kin"

    # ...and drops to neighbour the moment that unit's trust is revoked.
    trust.revoke(FRIEND_UNIT, path=trust_path)
    assert reg.get(NODE).provenance == "neighbour"


def test_revocation_reflects_in_dashboard_and_named_node_demotes(trust_path):
    # Even though the roster NAMES it (which would normally force kin), a revoked
    # builder wins — the whole point of demotion.
    trust.set_self(SELF_UNIT, "Origin", path=trust_path)
    trust.record_child_clone(FRIEND_UNIT, "Friend", parent_hash=SELF_UNIT,
                             path=trust_path)
    reg = NodeRegistry()
    reg.set_kin_roster({NODE: {"name": "FRIENDS NODE", "builder": FRIEND_UNIT}})
    trust.revoke(FRIEND_UNIT, path=trust_path)
    rec = reg.get(NODE)
    assert rec.name == "FRIENDS NODE"            # still named...
    assert rec.provenance == "neighbour"         # ...but demoted
    d = rec.to_dashboard(now=0.0)
    assert d["provenance"] == "neighbour"


def test_unknown_builder_keeps_old_beacon_is_kin(trust_path):
    # No builder recorded (a heard node) -> the interim heuristic stands: a node
    # that has spoken our beacon protocol is kin.
    from monitor.health_beacon import HealthBeacon
    reg = NodeRegistry()
    rec = reg.register("beacononly")
    assert rec.builder_hash is None
    rec.latest_beacon = HealthBeacon(
        format_version=1, uptime_s=1, free_heap_kb=1, wifi_rssi_dbm=-60,
        reset_reason=0, wifi_up=False, lora_up=True, tcp_backbone_up=False,
        local_tcp_server_up=False, wdt_armed=True, psram=True, fault=False,
        airtime_lock=False, board_id=0, firmware_version=0)
    assert rec.provenance == "kin"               # unchanged old behaviour


def test_unknown_builder_bare_mesh_hash_is_neighbour(trust_path):
    # A bare mesh-heard destination with no builder stays a neighbour.
    reg = NodeRegistry()
    reg.ingest_mesh(MeshNode(dst_hash="beefbeef", hops=2,
                             interface="RNodeInterface"), now=1.0)
    rec = reg.get("beefbeef")
    assert rec.builder_hash is None
    assert rec.provenance == "neighbour"
