import json

import pytest

from monitor.mesh import parse_rnpath, discover_mesh, MeshNode

# Real `rnpath -t --json` shape (captured from a live mesh node).
RNPATH = json.dumps([
    {"hash": "445566778899aabbccddeeff00112233", "via": "445566778899aabbccddeeff00112233",
     "hops": 0, "expires": 1784517558.19, "interface": "LocalInterface[rns/default]"},
    {"hash": "5a0c000c000000000000000000000002", "via": "5a0c000c000000000000000000000002",
     "hops": 1, "expires": 1784171781.0, "interface": "RNodeInterface[RNode LoRa Interface]"},
    {"hash": "5a0b000b000000000000000000000006", "via": "aa11", "hops": 2,
     "expires": 1784171781.0, "interface": "RNodeInterface[RNode LoRa Interface]"},
])


def test_parse_rnpath_fields():
    nodes = parse_rnpath(RNPATH)
    assert len(nodes) == 3
    n = nodes[1]
    assert n.dst_hash == "5a0c000c000000000000000000000002"
    assert n.hops == 1
    assert n.interface.startswith("RNodeInterface")
    assert n.local is False


def test_parse_rnpath_bad_json_is_empty():
    assert parse_rnpath("<html>oops") == []
    assert parse_rnpath("") == []


def test_discover_mesh_excludes_local_destinations():
    run = lambda cmd: RNPATH
    nodes = discover_mesh(run)
    # the LocalInterface (0-hop own destination) is filtered out
    assert all(not n.local for n in nodes)
    assert {n.dst_hash for n in nodes} == {
        "5a0c000c000000000000000000000002", "5a0b000b000000000000000000000006"}


def test_discover_mesh_can_include_local():
    assert len(discover_mesh(lambda cmd: RNPATH, include_local=True)) == 3


def test_discover_mesh_runs_rnpath_json():
    seen = {}
    def run(cmd):
        seen["cmd"] = cmd
        return "[]"
    discover_mesh(run)
    assert "rnpath" in seen["cmd"] and "--json" in seen["cmd"]
