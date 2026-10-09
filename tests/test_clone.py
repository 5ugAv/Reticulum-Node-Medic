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
    "install_carried_packages",
    "copy_monitoring_db",
    "copy_offline_maps",
    "copy_kin_roster",
    "generate_fresh_identity",
    "stamp_lineage",
    "record_child_trust",
    "configure_autostart",
    "bake_recovery_bootorder",
    "install_card_helper",
    "install_radio_helper",
    "ensure_ssh_keypair",
    "final_verification",
    "restart_into_tool",
    "confirm_tool_running",
    # every clone ends locked like its parent; a new community's clone then
    # loses this medic's key, as the very last thing done to it (2026-10-08)
    "harden_new_medic",
    "remove_parent_key",
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
    c.rules.insert(0, ("systemctl is-active reticulum-node-medic", 0, "active", ""))
    c.rules.insert(0, ("NRestarts", 0, "0", ""))
    boots = iter(["boot-a"] + ["boot-b"] * 50)   # the reboot changes the boot id
    real = c.run
    # The new medic as harden_new_medic meets it: full sudo until its root
    # supervisor (security/apply_all.sh) has applied the locks, scoped after,
    # full again if it rolled back; and once this medic's key is removed, every
    # login refused. c.child lets a test start it in another state.
    c.child = {"supervisor": "", "keys": True}

    def run(cmd, *a, **k):
        child = c.child
        if "boot_id" in cmd:
            return (0, next(boots, "boot-b"), "")
        if not child["keys"]:
            c.history.append(cmd)
            return (255, "", "pi@10.55.0.1: Permission denied (publickey).")
        scoped = child["supervisor"] in ("applied", "confirmed")
        if "systemd-run" in cmd and "apply_all.sh" in cmd:
            child["supervisor"] = "applied"
        elif cmd.startswith("touch ~/.nodemedic-harden-confirm") and child["supervisor"] == "applied":
            child["supervisor"] = "confirmed"
        elif cmd.startswith("touch ~/.nodemedic-harden-rollback") and child["supervisor"] == "applied":
            child["supervisor"] = "rolled-back: the checking medic found a problem"
        elif cmd.startswith("cat /run/nodemedic-harden/status"):
            c.history.append(cmd)
            state = child["supervisor"]
            return (0, state + "\n", "") if state else (1, "", "No such file or directory")
        elif cmd.startswith("sudo -n /usr/bin/true"):
            c.history.append(cmd)
            return (1, "", "sudo: a password is required") if scoped else (0, "", "")
        elif cmd.startswith("test -f /etc/ssh/sshd_config.d/01-nodemedic-hardening.conf"):
            c.history.append(cmd)
            return (0, "", "") if scoped else (1, "", "")
        elif cmd.startswith("test -f /etc/nftables.d/nodemedic-ssh.nft"):
            c.history.append(cmd)
            return (0, "", "") if child["supervisor"] == "confirmed" else (1, "", "")
        elif cmd.startswith("rm -f ~/.ssh/authorized_keys"):
            c.history.append(cmd)
            child["keys"] = False
            return (0, "nm-no-keys-left\n", "")
        return real(cmd, *a, **k)
    c.run = run
    return c


def wf(c=None, registry=None):
    w = CloneWorkflow(c or conn(), registry or registry_with_node())
    w.sleep = lambda _s: None                  # confirm_tool_running waits for real otherwise
    return w


def test_the_last_step_fails_when_the_app_keeps_restarting():
    """The first real clone said "verified" over a crash-looping app (2026-10-06)."""
    c = conn()
    counts = iter(["3", "5"])
    w = CloneWorkflow(c, registry_with_node()); w.sleep = lambda _s: None
    w.boot_id_before = "boot-a"
    real_run = c.run
    def run(cmd, *a, **k):
        if "NRestarts" in cmd:
            return (0, next(counts, "5"), "")
        return real_run(cmd, *a, **k)
    c.run = run
    r = _run(w, "confirm_tool_running")
    assert not r.success and "not staying open" in r.message


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
    # only the OS image is required (readiness ledger #126): a parent built
    # from GitHub has no toolchains yet and must still be able to clone
    assert "pi_os_lite" in r.message
    assert clone.REQUIRED == ("pi_os_lite.img.xz",)


def test_optional_trees_are_skipped_not_fatal(monkeypatch):
    """A medic that never had an RTNode tree must still be able to make a medic."""
    monkeypatch.setattr("os.listdir", lambda p: ["pi_os_lite.img.xz", ".arduino15"])
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr("os.path.islink", lambda p: False)
    monkeypatch.setattr("os.path.isdir", lambda p: p.endswith(".arduino15"))
    monkeypatch.setattr("os.path.isfile", lambda p: p.endswith(".xz"))
    w = wf()
    r = _run(w, "carry_the_toolchain")
    assert r.success is True
    assert "home folder" in r.message


def test_the_tracker_build_dir_is_actually_carried():
    """The specific gap that broke the first clone: whatever path
    rnode_flash.TRACKER_BUILD_DIR points at must be inside something the clone
    sends, or the new medic cannot flash its own firstborn."""
    from workflows import rnode_flash
    tracker = rnode_flash.TRACKER_BUILD_DIR
    top = tracker[2:].split("/")[0]
    assert tracker.startswith("~/") and top not in clone.HOME_NEVER, (
        f"{tracker} would stay behind - the clone would fail at its firstborn")


def test_scratch_is_not_carried():
    """Working images and one-off build dirs must not travel: gigabytes, and it
    passes this medic's mess on as if it were the tool."""
    assert "imgwork" in clone.CARRY_SKIP and "imgwork" in clone.HOME_NEVER
    assert "scratch" in clone.HOME_NEVER and "this-medic" in clone.HOME_NEVER


def test_the_carried_install_is_judged_by_dpkg_not_path():
    """gpsd lives in /usr/sbin, off a normal user's PATH: `command -v gpsd`
    failed a perfect offline install on the Wi-Fi-off proof clone."""
    from tests.srcutil import func_source
    body = func_source("workflows/clone.py", "install_carried_packages")
    assert "command -v gpsd" not in body and "command -v direwolf" not in body
    assert "dpkg-query -W" in body and "install ok installed" in body


def test_the_clone_carries_the_medics_own_python_packages_and_settings():
    """~/.local/bin without ~/.local/lib left pio and esptool.py as dead
    scripts on Node Medic 2 (2026-10-06); and the band the fleet is on must
    travel or the clone is deaf to it."""
    from workflows import clone as cl
    # ~/.local travels whole except desktop data: lib and bin both go
    assert ".local" not in cl.HOME_NEVER
    assert set(cl.CLUTTER_IN[".local"]) == {"/share", "/state"}
    assert "radio_defaults.json" in cl.SETTINGS_ALWAYS and "language" in cl.SETTINGS_ALWAYS
    # the fleet's records travel by default; only what is named stays behind
    assert "forgotten.json" not in cl.RECORDS_NEVER
    # the board picker's memory holds chip MACs: a MAC never leaves the medic
    picker = "board_" + "memory.json"
    for never in ("trust.json", "location_salt", "onboard.json", "first_use.json",
                  "tool_identity.json", picker, "board_traits.json"):
        assert never in cl.RECORDS_NEVER and never not in cl.SETTINGS_ALWAYS
    from tests.srcutil import func_source
    assert "_carry_settings(wf)" in func_source("workflows/clone.py", "copy_monitoring_db")
    auto = func_source("workflows/clone.py", "configure_autostart")
    assert "99-nodemedic-usb0.conf" in auto
    trust = func_source("workflows/clone.py", "record_child_trust")
    assert "ensure_signing_key(" in trust and "trusted_keys" in trust


def test_a_missing_pty_driver_refuses_before_the_write_and_never_erases():
    from workflows.rnode_flash import refused_before_write
    from tests.srcutil import func_source
    body = func_source("workflows/rnode_flash.py", "birth_flash")
    assert "import pexpect" in body
    assert refused_before_write("Node Medic can't flash this board here: its PTY driver (python3-pexpect) is not installed on this medic. Nothing was written.")


# ---------------------------------------------------------------------------
# Locked like its parent (keeper, 2026-10-08): EVERY clone ends with the
# parent's hardening — scoped sudo, key-only SSH, the SSH firewall. A clone for
# the keeper's own fleet keeps this medic's key; one for a NEW community has it
# removed as the very last action, leaving no key at all.
# ---------------------------------------------------------------------------

def _started(c):
    return next(i for i, h in enumerate(c.history) if "systemd-run" in h)


def test_the_lock_steps_are_the_very_last_two():
    assert [n for n, _ in wf().steps][-3:] == [
        "confirm_tool_running", "harden_new_medic", "remove_parent_key"]


def test_harden_installs_the_kit_root_owned_then_checks_then_confirms():
    c = conn()
    r = _run(wf(c), "harden_new_medic")
    assert r.success, r.message
    # the whole kit goes across and lands root-owned in one place, so the root
    # supervisor never runs a file the app account could edit
    pushed = {remote for _l, remote in c.pushed}
    for _rel, dest, mode in clone.HARDENING_KIT:
        assert f"/tmp/nm-security/{dest}" in pushed, dest
        assert (f"sudo -n install -m {mode} -o root -g root /tmp/nm-security/{dest} "
                f"{clone.HARDENING_DIR}/{dest}") in c.history, dest
    start = _started(c)
    assert (f"/bin/bash {clone.HARDENING_DIR}/apply_all.sh --user nodemedic "
            "--window 600") in c.history[start]
    # the keeper's checks — a fresh login, an allowed and an unlisted sudo —
    # come AFTER the locks went on and BEFORE anything is confirmed
    confirm = c.history.index("touch ~/.nodemedic-harden-confirm")
    between = c.history[start:confirm]
    for check in ("true", clone.HARDEN_ALLOWED_PROBE, clone.HARDEN_REFUSED_PROBE,
                  "test -f /etc/ssh/sshd_config.d/01-nodemedic-hardening.conf"):
        assert check in between, check
    assert "touch ~/.nodemedic-harden-rollback" not in c.history
    assert c.child["supervisor"] == "confirmed"
    # and once more after: still in, and the firewall survives a restart
    after = c.history[confirm:]
    assert "true" in after and "test -f /etc/nftables.d/nodemedic-ssh.nft" in after


def test_harden_rolls_back_when_an_allowed_admin_job_is_refused():
    c = conn()
    c.rules.insert(0, (clone.HARDEN_ALLOWED_PROBE, 1, "", "sudo: a password is required"))
    r = _run(wf(c), "harden_new_medic")
    assert not r.success
    assert "touch ~/.nodemedic-harden-rollback" in c.history[_started(c):]
    assert "touch ~/.nodemedic-harden-confirm" not in c.history
    assert c.child["supervisor"].startswith("rolled-back")
    assert "allowed admin jobs" in r.message and "undid every change" in r.message


def test_harden_rolls_back_when_the_app_can_still_do_anything():
    c = conn()
    real = c.run

    def run(cmd, *a, **k):
        if cmd.startswith("sudo -n /usr/bin/true"):        # the scoping did not take
            c.history.append(cmd)
            return (0, "", "")
        return real(cmd, *a, **k)
    c.run = run
    r = _run(wf(c), "harden_new_medic")
    assert not r.success and "can still do anything" in r.message
    assert "touch ~/.nodemedic-harden-confirm" not in c.history
    assert c.child["supervisor"].startswith("rolled-back")


def test_harden_reports_the_new_medics_own_rollback_and_confirms_nothing():
    c = conn()
    real = c.run

    def run(cmd, *a, **k):
        if "systemd-run" in cmd and "apply_all.sh" in cmd:
            c.history.append(cmd)
            c.child["supervisor"] = "rolled-back: the SSH firewall did not go in"
            return (0, "", "")
        return real(cmd, *a, **k)
    c.run = run
    r = _run(wf(c), "harden_new_medic")
    assert not r.success
    assert "the SSH firewall did not go in" in r.message and "as it was" in r.message
    assert not any(h.startswith("touch ~/.nodemedic-harden") for h in c.history)


def test_harden_that_loses_sight_of_the_new_medic_confirms_nothing():
    """Locked out after the change (or the cable pulled): nothing can be
    confirmed, so the new medic undoes the change itself — and says when."""
    c = conn()
    real = c.run

    def run(cmd, *a, **k):
        if c.child["supervisor"] == "applied":
            c.history.append(cmd)
            return (255, "", "ssh: connect to host 10.55.0.1 port 22: Connection timed out")
        return real(cmd, *a, **k)
    c.run = run
    r = _run(wf(c), "harden_new_medic")
    assert not r.success
    assert "touch ~/.nodemedic-harden-confirm" not in c.history
    assert f"within {clone.HARDEN_UNDO_WITHIN_MIN} minutes" in r.message


def test_harden_refuses_without_a_key_for_this_medic_and_changes_nothing():
    c = conn()
    c.rules.insert(0, ("test -s ~/.ssh/authorized_keys", 1, "", ""))
    r = _run(wf(c), "harden_new_medic")
    assert not r.success and "Nothing was changed" in r.message
    assert not any("systemd-run" in h or "install -m" in h for h in c.history)
    assert not c.pushed


def test_harden_on_a_retry_after_it_finished_is_a_clean_success():
    c = conn()
    c.child["supervisor"] = "confirmed"           # this medic lost sight of a run that finished
    r = _run(wf(c), "harden_new_medic")
    assert r.success and "already locked" in r.message
    assert not any("systemd-run" in h for h in c.history) and not c.pushed


def test_harden_stops_an_earlier_supervisor_before_starting_again():
    c = conn()
    c.child["supervisor"] = "applied"             # an earlier attempt still waiting
    r = _run(wf(c), "harden_new_medic")
    assert r.success, r.message
    assert c.history.index("touch ~/.nodemedic-harden-rollback") < _started(c)
    assert c.child["supervisor"] == "confirmed"


def test_a_same_fleet_clone_keeps_this_medics_key():
    c = conn()
    r = _run(wf(c), "remove_parent_key")
    assert r.success and r.skipped and "keeps its key" in r.message
    assert not any("authorized_keys" in h for h in c.history)
    assert c.child["keys"]


def _fresh(c):
    w = CloneWorkflow(c, registry_with_node(), fresh_fleet=True)
    w.sleep = lambda _s: None
    return w


def test_a_new_communitys_clone_loses_this_medics_key_and_the_door_is_proven_shut():
    c = conn()
    r = _run(_fresh(c), "remove_parent_key")
    assert r.success and not r.skipped and "Nobody can log in" in r.message
    rm = next(h for h in c.history if h.startswith("rm -f ~/.ssh/authorized_keys"))
    assert "~/.ssh/authorized_keys2" in rm            # sshd's second default file too
    assert c.history[-1] == "true"                   # last of all: a login, refused
    assert not c.child["keys"]


def test_the_key_step_fails_if_this_medic_can_still_get_in():
    c = conn()
    real = c.run

    def run(cmd, *a, **k):
        if cmd.startswith("rm -f ~/.ssh/authorized_keys"):    # says gone, is not
            c.history.append(cmd)
            return (0, "nm-no-keys-left\n", "")
        return real(cmd, *a, **k)
    c.run = run
    r = _run(_fresh(c), "remove_parent_key")
    assert not r.success and "can still log in" in r.message


def test_a_whole_clone_for_a_new_community_ends_with_the_key_gone(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("os.path.exists", lambda p: True)
    c = conn()
    w = _fresh(c)
    w.run_all()
    assert all(r.success for r in w.results), [
        (r.name, r.message) for r in w.results if not r.success]
    assert [r.name for r in w.results][-2:] == ["harden_new_medic", "remove_parent_key"]
    assert not w.results[-1].skipped
    # nothing reached the new medic after its key went, but the proof the door is shut
    rm = max(i for i, h in enumerate(c.history) if h.startswith("rm -f ~/.ssh/authorized_keys"))
    assert c.history[rm + 1:] == ["true"]


def test_the_new_medics_screen_is_left_alone_for_the_lock_steps():
    c = conn()
    w = wf(c)
    before = len(c.history)
    w._tell_new_medic("harden_new_medic")
    w._tell_new_medic("remove_parent_key", failed=True)
    assert len(c.history) == before and not c.pushed


def test_every_file_of_the_kit_is_in_the_tool_and_beside_apply_all():
    import os
    names = {dest for _rel, dest, _mode in clone.HARDENING_KIT}
    for rel, _dest, _mode in clone.HARDENING_KIT:
        assert os.path.isfile(os.path.join(TOOL_ROOT, rel)), rel
    for needed in ("apply_all.sh", "apply_sshd.sh", "rollback_sshd.sh", "apply_firewall.sh",
                   "rollback_firewall.sh", "apply_sudoers.sh", "rollback_sudoers.sh",
                   "render_sudoers.sh", "sshd_config.d/01-nodemedic-hardening.conf",
                   "nftables/nodemedic-ssh.nft", "sudoers.nodemedic"):
        assert needed in names, needed


def test_the_promise_of_when_it_undoes_itself_matches_the_script():
    import os
    with open(os.path.join(TOOL_ROOT, "provisioning", "security", "apply_all.sh")) as fh:
        src = fh.read()
    assert "REVERT_MIN=$(( WINDOW / 60 + 10 ))" in src
    assert clone.HARDEN_UNDO_WITHIN_MIN == clone.HARDEN_WINDOW_S // 60 + 10


def test_the_radio_helper_is_installed_root_owned_and_read_back():
    from workflows.medic_radio import RADIO_HELPER, RADIO_HELPER_SOURCE
    c = conn()
    r = _run(wf(c), "install_radio_helper")
    assert r.success, r.message
    assert (RADIO_HELPER_SOURCE, "/tmp/nm-radio-units") in c.pushed
    assert (f"sudo -n install -D -m 755 -o root -g root /tmp/nm-radio-units "
            f"{RADIO_HELPER}") in c.history
    c2 = conn()
    c2.rules.insert(0, (f"test -x {RADIO_HELPER}", 1, "", ""))
    assert not _run(wf(c2), "install_radio_helper").success


def test_final_verification_names_a_missing_radio_helper():
    from workflows.medic_radio import RADIO_HELPER
    c = conn()
    c.rules.insert(0, (f"test -x {RADIO_HELPER}", 1, "", ""))
    w = wf(c)
    w.fresh_identity_generated = True
    r = _run(w, "final_verification")
    assert not r.success and "radio set-up helper missing" in r.message
