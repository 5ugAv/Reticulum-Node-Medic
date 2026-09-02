import json

import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from monitor.health_beacon import encode, decode
from workflows import clone
from monitor.registry import NodeRegistry
from workflows.clone import (
    CloneWorkflow, CLONE_DIR, REMOTE_TOOL_DIR, TOOL_ROOT,
    FIRMWARE_CACHE_LOCAL, REMOTE_WHEELS,
)

HASH = "11223344556677889900aabbccddeeff"
PI5_CPUINFO = "Model : Raspberry Pi 5 Model B Rev 1.0"

EXPECTED_STEPS = [
    "verify_target_pi5",
    "carry_the_time",
    "transfer_tool",
    "transfer_firmware_cache",
    "carry_the_toolchain",
    "install_dependencies",
    "carry_touch_cure",
    "install_display_stack",
    "copy_monitoring_db",
    "copy_offline_maps",
    "copy_kin_roster",
    "generate_fresh_identity",
    "stamp_lineage",
    "record_child_trust",
    "configure_autostart",
    "bake_recovery_bootorder",
    "final_verification",
    "restart_into_tool",
]

IDENTITY_OUT = "New identity <2233445566778899aabbccddeeff0011> written to ..."


def registry_with_node():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH", location="Wrenhill")
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
    c.rules.insert(0, ("test -f ~/.reticulum/storage/identity", 1, "", ""))
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
    # ...and has its toolchains/firmware trees, which carry_the_toolchain looks
    # for with os.path.exists. A medic missing a REQUIRED tree fails that step
    # on purpose - see test_carry_fails_when_a_required_tree_is_missing.
    monkeypatch.setattr("os.path.exists", lambda p: True)
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
    assert w.fresh_identity_hash == "2233445566778899aabbccddeeff0011"
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
    # scp to registry.json — the filename the app LOADS; the old
    # monitoring_db.json was a green-ticked no-op (review 2026-08-25).
    c = conn()
    w = wf(c)
    _run(w, "copy_monitoring_db")
    data = json.loads(w.monitoring_db_json)
    assert data["nodes"][0]["name"] == "TRUTH"
    assert any(remote.endswith("/registry.json") for _l, remote in c.pushed)


def test_copy_kin_roster_carries_locations(monkeypatch):
    """The fleet roster (names + DEPLOYED LOCATIONS + links) is written to the
    clone's kin.json, so a mitosis clone shows the same kin on its map."""
    import monitor.kin_roster as kr
    monkeypatch.setattr(kr, "load_roster", lambda *a, **k: {
        "e5e2a1c0": {"name": "EVERYWHERE", "type": "pi_propagation",
                     "lat": -37.5106, "lon": 145.5107,
                     "links": {"lora": True, "wifi": True,
                               "bluetooth": True, "internet": True}}})
    c = conn()
    res = _run(wf(c), "copy_kin_roster")
    assert res.success and "location" in res.message
    assert any(remote.endswith("/kin.json") for _l, remote in c.pushed)


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


def test_install_dependencies_bootstraps_pip_from_the_images_wheel(monkeypatch):
    """Lite ships no pip3 (HOPE 2026-08-01; relearned on HAWKEYE 2026-08-25).
    The ladder must fall back to running pip out of the image's own wheel."""
    c = conn()
    c.rules.insert(0, ("command -v pip3", 1, "", ""))
    c.rules.insert(0, ("python3 -m pip --version", 1, "", ""))
    c.rules.insert(0, ("ls /usr/share/python-wheels/pip-*.whl", 0,
                       "/usr/share/python-wheels/pip-25.1.1-py3-none-any.whl", ""))
    w = wf(c)
    r = _run(w, "install_dependencies")
    assert r.success, r.message
    assert w.pip_cmd.startswith("python3 /usr/share/python-wheels/pip-")
    assert any(w.pip_cmd in cmd and "--no-index" in cmd for cmd in c.history)


def test_display_stack_skips_when_cage_present():
    c = conn()
    c.rules.insert(0, ("command -v cage", 0, "/usr/bin/cage", ""))
    r = _run(wf(c), "install_display_stack")
    assert r.skipped


def test_autostart_is_the_proven_cage_kiosk():
    c = conn()
    w = wf(c)
    r = _run(w, "configure_autostart")
    assert r.success
    joined = "\n".join(c.history)
    assert "PAMName=login" in joined
    assert "TTYPath=/dev/tty1" in joined
    assert "ExecStart=/usr/bin/cage -s --" in joined
    assert "KIVY_GL_BACKEND=sdl2" in joined
    assert "goodix-rebind.service" in joined
    assert "WantedBy=multi-user.target" in joined
    assert "graphical.target" not in joined


# -- the recovery boot-order rung (2026-08-25) ------------------------------
# Bonus hardening: a failure must NEVER fail the clone (SD boot is unaffected),
# and a successful bake rewrites BOOT_ORDER to 0xf321 (SD -> NETWORK -> RPIBOOT
# -> loop). The NETWORK rung is what lets a SEALED sibling medic be rescued over
# the ethernet cable alone — the lesson HAWKEYE taught (0x71/mode-7 had no
# headless door).

def test_bake_recovery_bootorder_is_a_skip_when_chip_unreadable():
    c = conn()
    c.rules.insert(0, ("rpi-eeprom-config", 127, "", "not found"))
    r = _run(wf(c), "bake_recovery_bootorder")
    assert r.success and r.skipped
    assert "SD boot still works" in r.message


def test_bake_recovery_bootorder_applies_network_boot_order():
    from workflows.clone import RECOVERY_BOOT_ORDER
    # the baked order must include the NETWORK nibble (2) so a sealed medic is
    # recoverable over ethernet, and must NOT be the mode-7 HTTP trap (7).
    assert "2" in RECOVERY_BOOT_ORDER and "7" not in RECOVERY_BOOT_ORDER
    c = conn()
    c.rules.insert(0, ("rpi-eeprom-config", 0,
                       "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf461\n", ""))
    r = _run(wf(c), "bake_recovery_bootorder")
    assert r.success and not r.skipped
    assert any(f"BOOT_ORDER={RECOVERY_BOOT_ORDER}" in h for h in c.history), \
        "apply never sent"
    assert "ethernet cable" in r.message


def test_bake_recovery_bootorder_skips_when_already_baked():
    from workflows.clone import RECOVERY_BOOT_ORDER
    c = conn()
    c.rules.insert(0, ("rpi-eeprom-config", 0,
                       f"[all]\nBOOT_ORDER={RECOVERY_BOOT_ORDER}\n", ""))
    r = _run(wf(c), "bake_recovery_bootorder")
    assert r.success and r.skipped
    assert not any("--apply" in h for h in c.history)


# ---------------------------------------------------------------------------
# Total self-replication (2026-09-02)
#
# The first real clone reached its firstborn step and stopped: "the medic's
# Tracker fork build is missing" and "arduino-cli not installed". TRACKER_BUILD_DIR
# points at ~/overlay_test - OUTSIDE the tool tree - and nothing installed a
# toolchain, so the clone inherited the code and the Python stack but could not
# build firmware, image a card, or birth its own radio.
# ---------------------------------------------------------------------------

def test_carry_fails_when_a_required_tree_is_missing(monkeypatch):
    """A medic that cannot pass on its toolchain has not replicated, and must
    say so rather than report success and leave the failure for the operator to
    discover at the firstborn step."""
    monkeypatch.setattr("os.path.exists", lambda p: False)
    w = wf()
    r = _run(w, "carry_the_toolchain")
    assert r.success is False
    assert "arduino15" in r.message or "build firmware" in r.message


def test_optional_trees_are_skipped_not_fatal(monkeypatch):
    """A medic that never had an RTNode tree must still be able to make a medic."""
    import os as _os
    required = {p for p, _w, req in clone.CARRIED_TREES if req}
    monkeypatch.setattr("os.path.exists",
                        lambda p: any(_os.path.expanduser(r) == p
                                      for r in required))
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    w = wf()
    r = _run(w, "carry_the_toolchain")
    assert r.success is True
    assert "not carried" in r.message


def test_the_tracker_build_dir_is_actually_carried():
    """The specific gap that broke the first clone: whatever path
    rnode_flash.TRACKER_BUILD_DIR points at must be inside something the clone
    sends, or the new medic cannot flash its own firstborn."""
    from workflows import rnode_flash
    carried = [p for p, _w, _r in clone.CARRIED_TREES]
    tracker = rnode_flash.TRACKER_BUILD_DIR
    assert any(tracker.startswith(p) for p in carried), (
        f"{tracker} is carried by nothing - the clone will fail at its firstborn")


def test_scratch_is_not_carried():
    """Working images and one-off build dirs must not travel: gigabytes, and it
    passes this medic's mess on as if it were the tool."""
    assert "imgwork" in clone.CARRY_SKIP
    assert not any("imgwork" in p for p, _w, _r in clone.CARRIED_TREES)
