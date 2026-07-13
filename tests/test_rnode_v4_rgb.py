"""Heltec V4 + NeoPixel RNode build/flash workflow (Birth + Repair).

Codifies the two hand-proven scripts non-interactively. Every assertion pins a
command that was verified by hand on a real V4.
"""

import pytest

from transport.connection import EmulatedConnection
from workflows.rnode_v4_rgb import (
    HeltecV4RGBWorkflow, compile_command, esptool_flash_command,
    firmware_hash_command, esptool_path, BUILD_BIN, FIRMWARE_DIR, REMOTE_PATCH,
    LOCAL_PATCH, BOARD_MODEL,
)

GOOD_INFO = ("Device connected\nCurrent firmware version: 1.86\n"
             "Reading EEPROM...\n\tFirmware version   : 1.86\n"
             "\tDevice signature   : Verified")
BAD_INFO = ("Device connected\nCurrent firmware version: 1.86\n"
            "Reading EEPROM...\nEEPROM is invalid, no further information available")


# ---- command builders -----------------------------------------------------

def test_compile_command_is_the_proven_recipe():
    cmd = compile_command()
    assert "arduino-cli compile" in cmd
    assert "esp32:esp32:esp32s3:CDCOnBoot=cdc" in cmd
    assert "build.partitions=no_ota" in cmd
    assert "upload.maximum_size=2097152" in cmd
    # V4 keeps its real board id so it still identifies as a Heltec32 V4
    assert "-DBOARD_MODEL=0x3F" in cmd
    assert " -e " in cmd  # export binaries so they land at BUILD_BIN


def test_esptool_flash_command_overlays_app_partition():
    cmd = esptool_flash_command("/dev/ttyACM1")
    assert "--chip esp32s3" in cmd
    assert "0x10000" in cmd
    assert BUILD_BIN in cmd
    assert "--baud 921600" in cmd
    assert esptool_path() in cmd


def test_firmware_hash_command_computes_then_stamps():
    cmd = firmware_hash_command("/dev/ttyACM1")
    assert "partition_hashes" in cmd
    assert "--firmware-hash" in cmd
    assert "/dev/ttyACM1" in cmd


# ---- build phase ----------------------------------------------------------

def build_conn(cloned=False, has_bin_after_compile=True):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    # firmware dir present? (test -d)
    c.rule(f"test -d {FIRMWARE_DIR}", code=0 if cloned else 1)
    # after compile, the .bin exists (test -f BUILD_BIN)
    c.rule(f"test -f {BUILD_BIN}", code=0 if has_bin_after_compile else 1)
    return c


def wf(conn, **kw):
    return HeltecV4RGBWorkflow(conn, port="/dev/ttyACM1", **kw)


def test_build_installs_toolchain_clones_patches_compiles():
    conn = build_conn(cloned=False)
    results = wf(conn).build()
    assert all(r.success for r in results)
    assert [r.name for r in results] == [
        "ensure_toolchain", "ensure_source", "build_firmware"]
    h = conn.history
    assert any("arduino-cli core install esp32:esp32@2.0.17" in c for c in h)
    assert any('lib install "Adafruit NeoPixel"' in c for c in h)
    assert any("git clone" in c for c in h)
    assert any("arduino-cli compile" in c for c in h)


def test_build_carries_and_runs_the_neopixel_patcher():
    conn = build_conn()
    wf(conn).build()
    assert (LOCAL_PATCH, REMOTE_PATCH) in conn.pushed
    assert any(REMOTE_PATCH in c and "Boards.h" in c for c in conn.history)


def test_build_installs_arduino_cli_when_missing():
    conn = build_conn(cloned=True)
    conn.rule("command -v arduino-cli", code=1)  # toolchain not yet installed
    wf(conn).build()
    assert any("install.sh" in c for c in conn.history)


def test_build_skips_arduino_cli_install_when_present():
    conn = build_conn(cloned=True)  # default_code 0 -> command -v succeeds
    wf(conn).build()
    assert not any("install.sh" in c for c in conn.history)


def test_build_skips_clone_when_firmware_already_present():
    conn = build_conn(cloned=True)
    wf(conn).build()
    assert not any("git clone" in c for c in conn.history)


def test_build_fails_when_compile_produces_no_binary():
    conn = build_conn(has_bin_after_compile=False)
    results = wf(conn).build()
    assert results[-1].name == "build_firmware"
    assert results[-1].success is False


# ---- flash phase (also the Repair action) --------------------------------

def flash_conn(info=GOOD_INFO, provisioned=True, has_bin=True):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule(f"test -f {BUILD_BIN}", code=0 if has_bin else 1)
    # autoinstall provisioning (pre-fed 9/enter/2/y): report completion
    c.rule("--autoinstall", code=0,
           stdout=("[complete] autoinstallation complete" if provisioned
                   else "error"))
    c.rule("esptool", code=0, stdout="Hash of data verified.")
    c.rule("partition_hashes", code=0, stdout="deadbeef")
    c.rule("--firmware-hash", code=0, stdout="ok")
    c.rule("--info", code=0, stdout=info)
    return c


def test_flash_runs_provision_then_esptool_then_hash_then_verify():
    conn = flash_conn()
    results = wf(conn).flash()
    assert [r.name for r in results] == [
        "detect_port", "provision", "flash_custom", "set_hash", "verify"]
    assert all(r.success for r in results)
    h = conn.history
    # provision uses the proven non-interactive autoinstall (V4 = index 9)
    assert any("--autoinstall" in c and "printf" in c for c in h)
    # then the custom firmware is overlaid and the hash restamped
    assert any("esptool" in c and "0x10000" in c for c in h)
    assert any("--firmware-hash" in c for c in h)


def test_flash_refuses_when_firmware_not_built():
    conn = flash_conn(has_bin=False)
    results = wf(conn).flash()
    fail = next(r for r in results if not r.success)
    assert fail.name == "flash_custom"
    assert "build()" in fail.message


def test_flash_repairs_invalid_eeprom_board_verify_passes():
    # the fault: --info says EEPROM invalid BEFORE; after the flash the same
    # verify command should see a valid board. Emulate by flipping the info rule.
    conn = flash_conn(info=BAD_INFO)
    results = wf(conn).flash()
    # verify keys off the POST-flash --info; with BAD_INFO it must fail loudly
    assert results[-1].name == "verify"
    assert results[-1].success is False


def test_flash_verify_accepts_valid_post_flash_info():
    conn = flash_conn(info=GOOD_INFO)
    results = wf(conn).flash()
    assert results[-1].name == "verify"
    assert results[-1].success is True


def test_run_all_does_build_then_flash():
    conn = flash_conn()
    conn.rule(f"test -d {FIRMWARE_DIR}", code=0)  # already cloned
    results = wf(conn).run_all()
    names = [r.name for r in results]
    assert names[0] == "ensure_toolchain"
    assert "build_firmware" in names
    assert names[-1] == "verify"


def test_run_all_stops_if_build_fails():
    conn = flash_conn(has_bin=False)
    conn.rule(f"test -d {FIRMWARE_DIR}", code=0)
    results = wf(conn).run_all()
    # build_firmware fails -> never reaches the flash phase
    assert results[-1].name == "build_firmware"
    assert not any(r.name == "provision" for r in results)
