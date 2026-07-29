import json

import pytest

from monitor.mesh import (parse_rnpath, discover_mesh, MeshNode,
                          parse_path_probe, attach_with_retry)

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


# -- parse_path_probe: the "Ping node now" reachability parser ----------------

def test_path_probe_found_with_hops():
    out = ("Path to <5a0b000b> requested\nPath found, destination <5a0b000b> "
           "is 1 hop away via <5a0b000b> on RNodeInterface[RNode LoRa Interface]")
    assert parse_path_probe(out) == (True, 1)


def test_path_probe_found_multi_hop():
    out = "Path found, destination <abcd> is 2 hops away via < x >"
    assert parse_path_probe(out) == (True, 2)


def test_path_probe_not_found():
    assert parse_path_probe("Path to <abcd> requested\nPath not found") == (False, None)


def test_path_probe_empty_or_garbage():
    assert parse_path_probe("") == (False, None)
    assert parse_path_probe(None) == (False, None)
    # a bare table dump (the OLD buggy path) must NOT read as reachable
    assert parse_path_probe("<abcd> is 1 hop away via <abcd>")[0] is False


# -- attach_with_retry: rides out the rnsd boot race -------------------------

def test_attach_retries_until_success():
    calls = {"n": 0}
    def attach():
        calls["n"] += 1
        if calls["n"] < 3:          # rnsd not ready for the first 2 tries
            raise OSError("rnsd not up")
    slept = []
    ok = attach_with_retry(attach, sleep=slept.append, base_delay=1.0)
    assert ok is True
    assert calls["n"] == 3
    assert len(slept) == 2          # slept between the 3 attempts


def test_attach_gives_up_after_budget_without_raising():
    calls = {"n": 0}
    def attach():
        calls["n"] += 1
        raise RuntimeError("no rnsd ever (dev box)")
    logs = []
    ok = attach_with_retry(attach, sleep=lambda s: None,
                           log=logs.append, max_attempts=4)
    assert ok is False              # gave up, but did not raise
    assert calls["n"] == 4
    assert any("attach failed" in m for m in logs)


def test_attach_succeeds_first_try_no_sleep():
    slept = []
    assert attach_with_retry(lambda: None, sleep=slept.append) is True
    assert slept == []              # no backoff when it works immediately
