import json

import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from monitor.health_beacon import encode, decode
from monitor.registry import NodeRegistry
from workflows.clone import (
    CloneWorkflow, CLONE_DIR, REMOTE_TOOL_DIR, TOOL_ROOT,
    FIRMWARE_CACHE_LOCAL, REMOTE_WHEELS,
)

HASH = "eabdd142596bcae888242ec1b172d566"
PI5_CPUINFO = "Model : Raspberry Pi 5 Model B Rev 1.0"

EXPECTED_STEPS = [
    "verify_target_pi5",
    "transfer_tool",
    "transfer_firmware_cache",
    "install_dependencies",
    "copy_monitoring_db",
    "copy_kin_roster",
    "generate_fresh_identity",
    "stamp_lineage",
    "record_child_trust",
    "configure_autostart",
    "final_verification",
]

IDENTITY_OUT = "New identity <45ada7a3c6c8809fa815e5790d2b3b62> written to ..."


def registry_with_node():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH", location="Northcote")
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    r.ingest(HASH, decode(encode(**kw)), 1_000_000.0)
    return r


def conn(cpuinfo=PI5_CPUINFO):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("/proc/cpuinfo", 0, cpuinfo, ""))
    c.rules.insert(0, ("id -un", 0, "nodemedic", ""))
    c.rules.insert(0, ("rnid --generate", 0, IDENTITY_OUT, ""))
    return c


def wf(c=None, registry=None):
    return CloneWorkflow(c or conn(), registry or registry_with_node())


def _run(w, name):
    idx = next(i for i, (n, _) in enumerate(w.steps) if n == name)
    return w.steps[idx][1](w)


# ---- structure -----------------------------------------------------------

def test_steps_registered_in_order():
    assert [n for n, _ in wf().steps] == EXPECTED_STEPS


def test_full_run_completes(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: True)   # medic has a fw cache
    w = wf()
    w.run_all()
    assert w.current_index == len(EXPECTED_STEPS)
    assert all(r.success for r in w.results)


def test_verify_fails_on_non_pi5():
    w = wf(conn(cpuinfo="Model : Raspberry Pi 4 Model B"))
    assert _run(w, "verify_target_pi5").success is False


def test_run_all_stops_on_verify_failure():
    w = wf(conn(cpuinfo="not a pi"))
    w.run_all()
    assert w.current_index == 0
    assert w.results[-1].name == "verify_target_pi5"
    assert w.results[-1].success is False


# ---- transfer ------------------------------------------------------------

def test_transfer_tool_rsyncs_the_tree_and_checks_main():
    c = conn()
    w = wf(c)
    r = _run(w, "transfer_tool")
    assert r.success
    assert (TOOL_ROOT, REMOTE_TOOL_DIR) in c.pushed_trees   # whole tree copied


def test_transfer_tool_fails_when_main_missing():
    c = conn()
    c.rules.insert(0, (f"test -f {REMOTE_TOOL_DIR}/main.py", 1, "", ""))
    w = wf(c)
    assert _run(w, "transfer_tool").success is False


def test_transfer_firmware_cache_copies_when_present(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    c = conn()
    w = wf(c)
    r = _run(w, "transfer_firmware_cache")
    assert r.success and r.skipped is False
    assert any(local == FIRMWARE_CACHE_LOCAL for local, _ in c.pushed_trees)


def test_transfer_firmware_cache_skips_when_medic_has_none(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    r = _run(wf(), "transfer_firmware_cache")
    assert r.success and r.skipped is True


# ---- dependencies --------------------------------------------------------

def test_install_deps_prefers_carried_wheels_offline():
    c = conn()
    w = wf(c)
    _run(w, "install_dependencies")
    assert any("--no-index" in cmd and REMOTE_WHEELS in cmd for cmd in c.history)


def test_install_deps_falls_back_to_online_pip():
    c = conn()
    c.rules.insert(0, (f"ls {REMOTE_WHEELS}/*.whl", 2, "", ""))   # no wheels
    c.rules.insert(0, ("curl -fsI", 0, "", ""))                   # but online
    w = wf(c)
    r = _run(w, "install_dependencies")
    assert r.success
    assert any(cmd.startswith("pip3 install --break-system-packages")
               for cmd in c.history)


def test_install_deps_fails_offline_without_wheels():
    c = conn()
    c.rules.insert(0, (f"ls {REMOTE_WHEELS}/*.whl", 2, "", ""))   # no wheels
    c.rules.insert(0, ("curl -fsI", 7, "", ""))                  # and offline
    r = _run(wf(c), "install_dependencies")
    assert r.success is False
    assert "offline" in r.message.lower()


# ---- fresh identity (never the source's) ---------------------------------

def test_generate_fresh_identity_captures_hash():
    w = wf()
    r = _run(w, "generate_fresh_identity")
    assert r.success
    assert w.fresh_identity_hash == "45ada7a3c6c8809fa815e5790d2b3b62"
    assert w.fresh_identity_generated is True


def test_clone_never_copies_the_source_identity():
    c = conn()
    w = wf(c)
    w.run_all()
    # no step should rsync the source medic's ~/.reticulum identity
    assert not any(".reticulum" in local for local, _ in c.pushed_trees)
    assert not any(("cp " in cmd or "scp" in cmd) and "identity" in cmd
                   for cmd in c.history)


# ---- autostart -----------------------------------------------------------

def test_stamp_lineage_records_parent_on_clone(monkeypatch):
    # source medic's own identity/name are read locally; clone gets them as parent
    monkeypatch.setattr("provisioning.tool_identity.identity_hash",
                        lambda run=None: "abc123")
    monkeypatch.setattr("provisioning.tool_identity.tool_name",
                        lambda path=None: "Origin Medic")
    c = conn()
    w = wf(c)
    r = _run(w, "stamp_lineage")
    assert r.success
    wrote = [cmd for cmd in c.history if "tool_identity.json" in cmd]
    assert wrote and '"parent"' in wrote[0]
    assert "abc123" in wrote[0] and "Origin Medic" in wrote[0]
    assert "cloned from this unit" in wrote[0]


def test_configure_autostart_writes_and_enables_service():
    c = conn()
    w = wf(c)
    r = _run(w, "configure_autostart")
    assert r.success
    assert any("reticulum-node-medic.service" in cmd and "tee" in cmd
               for cmd in c.history)
    assert any("systemctl enable reticulum-node-medic.service" in cmd
               for cmd in c.history)


# ---- final verification + DB ---------------------------------------------

def test_final_verification_fails_without_rns():
    c = conn()
    c.rules.insert(0, ("python3 -c 'import RNS'", 1, "", ""))
    w = wf(c)
    w.fresh_identity_generated = True
    r = _run(w, "final_verification")
    assert r.success is False
    assert "RNS not importable" in r.message


def test_monitoring_db_serialises_the_registry():
    c = conn()
    w = wf(c)
    _run(w, "copy_monitoring_db")
    data = json.loads(w.monitoring_db_json)
    assert data["nodes"][0]["name"] == "TRUTH"
    assert any("monitoring_db.json" in cmd for cmd in c.history)


def test_copy_kin_roster_carries_locations(monkeypatch):
    """The fleet roster (names + DEPLOYED LOCATIONS + links) is written to the
    clone's kin.json, so a mitosis clone shows the same kin on its map."""
    import monitor.kin_roster as kr
    monkeypatch.setattr(kr, "load_roster", lambda *a, **k: {
        "5463bddf": {"name": "EVERYWHERE", "type": "pi_propagation",
                     "lat": -37.7006, "lon": 145.007,
                     "links": {"lora": True, "wifi": True,
                               "bluetooth": True, "internet": True}}})
    c = conn()
    res = _run(wf(c), "copy_kin_roster")
    assert res.success and "location" in res.message
    assert any("kin.json" in cmd and "EVERYWHERE" in cmd and "-37.7006" in cmd
               for cmd in c.history)


def test_maps_are_not_excluded_from_the_clone_tree():
    """The offline map tiles (assets/maps/*.mbtiles) must travel with the tool
    tree to a clone — never in the exclude list."""
    from workflows.clone import TOOL_EXCLUDES
    assert not any("map" in e for e in TOOL_EXCLUDES)


def test_clone_does_not_carry_the_parents_onboard_roster():
    # Onboard serials are per-medic (each has unique physical boards). A clone must
    # NEVER inherit the parent's onboard.json, or it would mis-protect the wrong
    # serials and leave its own radio flashable — it self-commissions instead (#82).
    c = conn()
    w = wf(c)
    w.run_all()
    assert not any("onboard.json" in cmd for cmd in c.history)
    assert not any("onboard" in (local or "") for local, _ in c.pushed_trees)
