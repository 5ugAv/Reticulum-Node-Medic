import pytest

from transport.connection import EmulatedConnection
from workflows.rnode_boards import get_board
from workflows.rnode_flash import (
    RNodeFlashWorkflow,
    flash_command,
    SUCCESS_MARKER,
    FIRMWARE_VERSION,
)

V4 = get_board("heltec32_v4")


# ---- flash_command (the hardware-verified sequence) ---------------------


def test_flash_command_matches_verified_heltec_v4_sequence():
    cmd = flash_command(V4, "/dev/ttyACM0", band_mhz=915, version="1.86")
    # verified live: device 9 -> enter -> band 2 (915) -> confirm y
    assert cmd.startswith("printf '%s\\n' 9 '' 2 y | ")
    assert "rnodeconf /dev/ttyACM0 --autoinstall" in cmd
    assert "--nocheck" in cmd                    # offline, from the cache
    assert "--fw-version 1.86" in cmd


def test_flash_command_refuses_unverified_band():
    # Heltec V4 has no 433 MHz option -> must not guess
    with pytest.raises(ValueError):
        flash_command(V4, "/dev/ttyACM0", band_mhz=433)


def test_flash_command_refuses_board_without_verified_sequence():
    tbeam = get_board("tbeam")               # band map intentionally left blank
    with pytest.raises(ValueError):
        flash_command(tbeam, "/dev/ttyACM0", band_mhz=915)


# ---- workflow -----------------------------------------------------------


def flash_conn(ports="/dev/ttyACM0", online=False, flash_ok=True,
               provisioned=True):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0 if online else 7, "")             # connectivity
    c.rule("ls /dev/ttyACM", 0, ports)                      # single/multi board
    c.rules.insert(0, ("ls ~/.config/rnodeconf/update/1.86/*.zip",
                       0, "rnode_firmware_heltec32v4pa.zip", ""))
    c.rules.insert(0, ("--autoinstall", 0 if flash_ok else 97,
                       ("...\nRNode Firmware autoinstallation complete!"
                        if flash_ok else "Flash error"), ""))
    info = ("Device signature   : Validated\nFirmware version   : 1.86"
            if provisioned else "No answer from device")
    c.rules.insert(0, ("--info", 0, info, ""))
    return c


def test_workflow_happy_path_offline_flash():
    wf = RNodeFlashWorkflow(flash_conn(), V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert [r.name for r in results] == [
        "detect_port", "ensure_single_board", "ensure_firmware", "flash",
        "verify"]
    assert all(r.success for r in results)


def test_workflow_refuses_multiple_boards():
    conn = flash_conn(ports="/dev/ttyACM0 /dev/ttyACM1")
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "ensure_single_board"
    assert results[-1].success is False


def test_workflow_offline_without_cache_fails():
    conn = flash_conn()
    # no cached firmware zip
    conn.rules.insert(0, ("ls ~/.config/rnodeconf/update/1.86/*.zip", 2, "", ""))
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "ensure_firmware"
    assert results[-1].success is False


def test_workflow_flash_failure_stops_before_verify():
    conn = flash_conn(flash_ok=False)
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "flash"
    assert results[-1].success is False
    assert "verify" not in [r.name for r in results]


def test_workflow_verify_detects_unprovisioned_board():
    conn = flash_conn(provisioned=False)
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "verify"
    assert results[-1].success is False


def test_workflow_already_provisioned_board_is_a_skip_not_a_failure():
    conn = flash_conn()
    # rnodeconf refuses to re-flash a provisioned board (real behaviour)
    conn.rules.insert(0, ("--autoinstall", 0,
                          "Device connected\nThis device is already installed "
                          "and provisioned. No further action will be taken.", ""))
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    flash = next(r for r in results if r.name == "flash")
    assert flash.success is True
    assert flash.skipped is True
    assert all(r.success for r in results)          # continues to verify
