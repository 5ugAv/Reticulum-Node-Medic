import json

import pytest

from monitor.mesh import (parse_rnpath, discover_mesh, MeshNode,
                          parse_path_probe, attach_with_retry, is_hex_hash)

# Real `rnpath -t --json` shape (captured from a live mesh node).
RNPATH = json.dumps([
    {"hash": "445566778899aabbccddeeff00112233", "via": "445566778899aabbccddeeff00112233",
     "hops": 0, "expires": 1784517558.19, "interface": "LocalInterface[rns/default]"},
    {"hash": "c4d5e6f708192a3b4c5d6e7f80912a3b", "via": "c4d5e6f708192a3b4c5d6e7f80912a3b",
     "hops": 1, "expires": 1784171781.0, "interface": "RNodeInterface[RNode LoRa Interface]"},
    {"hash": "b7c8d9e0f1a2b3c4d5e6f70819a2b3c4", "via": "aa11", "hops": 2,
     "expires": 1784171781.0, "interface": "RNodeInterface[RNode LoRa Interface]"},
])


def test_parse_rnpath_fields():
    nodes = parse_rnpath(RNPATH)
    assert len(nodes) == 3
    n = nodes[1]
    assert n.dst_hash == "c4d5e6f708192a3b4c5d6e7f80912a3b"
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
        "c4d5e6f708192a3b4c5d6e7f80912a3b", "b7c8d9e0f1a2b3c4d5e6f70819a2b3c4"}


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
    out = ("Path to <b7c8d9e0> requested\nPath found, destination <b7c8d9e0> "
           "is 1 hop away via <b7c8d9e0> on RNodeInterface[RNode LoRa Interface]")
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


def test_rns_already_initialised_detects_the_real_error():
    from monitor.mesh import rns_already_initialised
    # the exact error RNS raises (RNS/Reticulum.py) when Reticulum() is called
    # twice in one process — for an attach step this means ALREADY ATTACHED
    e = OSError("Attempt to reinitialise Reticulum, when it was already running")
    assert rns_already_initialised(e) is True


def test_rns_already_initialised_rejects_other_errors():
    from monitor.mesh import rns_already_initialised
    assert rns_already_initialised(OSError("connection refused")) is False
    assert rns_already_initialised(RuntimeError(
        "Attempt to reinitialise Reticulum, when it was already running")) is False
    assert rns_already_initialised(ValueError("boom")) is False


def test_attach_pattern_proceeds_when_rns_already_running():
    # The app's _attach pattern: an "already running" init must NOT abort the
    # handler registration — the 2026-07-30 deaf-app bug. Simulate it: init
    # raises already-running, registration still happens, attach succeeds.
    from monitor.mesh import rns_already_initialised
    registered = []

    def fake_init():
        raise OSError("Attempt to reinitialise Reticulum, when it was already running")

    def attach():
        try:
            fake_init()
        except Exception as e:
            if not rns_already_initialised(e):
                raise
        registered.append("handlers")

    ok = attach_with_retry(attach, sleep=lambda s: None)
    assert ok is True
    assert registered == ["handlers"]   # handlers registered despite the raise


def test_rns_thread_signal_error_detects_the_real_error():
    from monitor.mesh import rns_thread_signal_error
    # the exact CPython error when RNS.Reticulum() runs on a non-main thread
    # (reproduced live on the medic 2026-07-30) — init is COMPLETE bar the
    # signal hooks, so an attach step must treat it as attached
    e = ValueError("signal only works in main thread of the main interpreter")
    assert rns_thread_signal_error(e) is True


def test_rns_thread_signal_error_rejects_other_errors():
    from monitor.mesh import rns_thread_signal_error
    assert rns_thread_signal_error(ValueError("bad value")) is False
    assert rns_thread_signal_error(OSError(
        "signal only works in main thread of the main interpreter")) is False


def test_attach_pattern_proceeds_on_thread_signal_error():
    # The app's _attach on a clean-client boot from a thread: init raises the
    # signal ValueError with everything functional already up — handlers must
    # still be registered and attach must succeed on attempt 1.
    from monitor.mesh import rns_already_initialised, rns_thread_signal_error
    registered = []

    def fake_init():
        raise ValueError("signal only works in main thread of the main interpreter")

    def attach():
        try:
            fake_init()
        except Exception as e:
            if not (rns_already_initialised(e) or rns_thread_signal_error(e)):
                raise
        registered.append("handlers")

    ok = attach_with_retry(attach, sleep=lambda s: None)
    assert ok is True
    assert registered == ["handlers"]


def test_is_hex_hash():
    assert is_hex_hash("b7c8d9e0f1a2b3c4d5e6f70819a2b3c4") is True
    assert is_hex_hash("rtnode:FAITH RTnode") is False   # non-hex display key
    assert is_hex_hash("b7c8d9e0") is False              # too short
    assert is_hex_hash("g" * 32) is False                # not hex
    assert is_hex_hash("") is False and is_hex_hash(None) is False
