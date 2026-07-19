"""Heltec V4 + NeoPixel RNode build/flash workflow (Birth + Repair).

Codifies the two hand-proven scripts non-interactively. Every assertion pins a
command that was verified by hand on a real V4.
"""

import pytest

from transport.connection import EmulatedConnection
from workflows.rnode_v4_rgb import (
    HeltecV4RGBWorkflow, compile_command, esptool_flash_command,
    firmware_hash_command, esptool_path, BUILD_BIN, FIRMWARE_DIR, REMOTE_PATCH,
    LOCAL_PATCH, REMOTE_BOOT_ERR, LOCAL_BOOT_ERR, BOARD_MODEL,
    rgb_firmware_available, flash_rgb_carried, REMOTE_RGB_BIN, REMOTE_RGB_HASHER,
    DEFAULT_FLASH_BAUD,
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
    assert f"--baud {DEFAULT_FLASH_BAUD}" in cmd     # safer default, not 921600
    assert "921600" not in cmd
    assert esptool_path() in cmd
    # a caller can still ask for a specific baud
    assert "--baud 115200" in esptool_flash_command("/dev/ttyACM1", baud=115200)


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
    assert any('lib install "Crypto"' in c for c in h)  # provides Ed25519.h
    assert any("git clone" in c for c in h)
    assert any("arduino-cli compile" in c for c in h)


def test_build_carries_and_runs_the_neopixel_patcher():
    conn = build_conn()
    wf(conn).build()
    assert (LOCAL_PATCH, REMOTE_PATCH) in conn.pushed
    assert any(REMOTE_PATCH in c and "Boards.h" in c for c in conn.history)


def test_build_carries_and_runs_the_boot_error_patcher():
    conn = build_conn()
    wf(conn).build()
    assert (LOCAL_BOOT_ERR, REMOTE_BOOT_ERR) in conn.pushed
    # the dim-red boot-error patch is applied to Utilities.h with a --red value
    assert any(REMOTE_BOOT_ERR in c and "Utilities.h" in c and "--red" in c
               for c in conn.history)


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


def test_flash_runs_provision_then_esptool_then_hash_then_params_then_verify():
    conn = flash_conn()
    results = wf(conn).flash()
    assert [r.name for r in results] == [
        "detect_port", "provision", "flash_custom", "set_hash", "set_params",
        "verify"]
    assert all(r.success for r in results)
    h = conn.history
    # provision uses the proven non-interactive autoinstall (V4 = index 9)
    assert any("--autoinstall" in c and "printf" in c for c in h)
    # then the custom firmware is overlaid and the hash restamped
    assert any("esptool" in c and "0x10000" in c for c in h)
    assert any("--firmware-hash" in c for c in h)


def test_flash_bakes_canonical_params_at_birth_then_host_mode():
    # The real fix for "Radio state mismatch": stale 250/SF11 default config is
    # overwritten with the canonical params, then the board is returned to
    # host-controlled mode so rnsd drives it. rnodeconf needs --tnc WITH the
    # flags; the flags alone are a silent no-op.
    conn = flash_conn()
    wf(conn).flash()
    h = conn.history
    tnc = next(c for c in h if "--tnc" in c and "--freq" in c)
    assert "--freq 915125000" in tnc
    assert "--bw 125000" in tnc
    assert "--sf 9" in tnc and "--cr 5" in tnc and "--txp 17" in tnc
    # and afterwards it is left host-controlled (-N), issued after the params
    assert any(c.rstrip().endswith("-N") for c in h)
    assert h.index(tnc) < next(i for i, c in enumerate(h) if c.rstrip().endswith("-N"))


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


# ---- carried RGB flash (Pi+RNode target with the board on the remote Pi) ---

def test_rgb_firmware_available_needs_both_bin_and_hasher(tmp_path):
    b = tmp_path / "RNode_Firmware.ino.bin"
    h = tmp_path / "partition_hashes"
    assert rgb_firmware_available(str(b), str(h)) is False      # neither yet
    b.write_bytes(b"BIN")
    assert rgb_firmware_available(str(b), str(h)) is False      # hasher missing
    h.write_text("#!/usr/bin/env python\n")
    assert rgb_firmware_available(str(b), str(h)) is True       # both present


def carried_conn():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("--autoinstall", code=0, stdout="autoinstallation complete")
    c.rule("esptool", code=0, stdout="Hash of data verified.")
    c.rule("partition_hashes", code=0, stdout="deadbeef")
    c.rule("--firmware-hash", code=0, stdout="ok")
    return c


def test_flash_rgb_carried_provisions_carries_overlays_stamps(tmp_path):
    b = tmp_path / "RNode_Firmware.ino.bin"; b.write_bytes(b"BIN")
    h = tmp_path / "partition_hashes"; h.write_text("#!/usr/bin/env python\n")
    conn = carried_conn()
    ok, msg, rgb = flash_rgb_carried(conn, "/dev/ttyACM0", band_mhz=915,
                                     bin_path=str(b), hasher_path=str(h))
    assert ok and rgb, msg           # LED applied on the happy path
    # 1) stock provision via the offline pre-fed autoinstall
    assert any("--autoinstall" in c and "printf" in c for c in conn.history)
    # 2) carried the compiled bin + hasher to their staging paths on the target
    assert (str(b), REMOTE_RGB_BIN) in conn.pushed
    assert (str(h), REMOTE_RGB_HASHER) in conn.pushed
    # 3) overlaid from the CARRIED bin (not the local build path) + 4) restamped
    assert any("esptool" in c and REMOTE_RGB_BIN in c and "0x10000" in c
               for c in conn.history)
    assert any("--firmware-hash" in c and REMOTE_RGB_HASHER in c
               for c in conn.history)


def test_flash_rgb_carried_falls_back_to_working_stock_when_overlay_fails(tmp_path):
    """The status LED is an enhancement: a failed overlay must leave a WORKING
    radio (re-flashed stock), reported as success, not a corrupt/bricked board."""
    b = tmp_path / "RNode_Firmware.ino.bin"; b.write_bytes(b"BIN")
    h = tmp_path / "partition_hashes"; h.write_text("x")
    conn = carried_conn()
    conn.rules.insert(0, ("esptool", 1, "", "Serial data stream stopped"))  # first wins
    ok, msg, rgb = flash_rgb_carried(conn, "/dev/ttyACM0", overlay_retries=1,
                                     bin_path=str(b), hasher_path=str(h))
    assert ok is True and rgb is False           # working radio, no LED
    assert "functional" in msg
    # it retried the overlay, then re-flashed stock to restore a clean app
    esptool_calls = [c for c in conn.history if "esptool" in c and "write_flash" in c]
    assert len(esptool_calls) == 2               # initial + one retry
    autoinstalls = [c for c in conn.history if "--autoinstall" in c and "printf" in c]
    assert len(autoinstalls) >= 2                # initial provision + restore


def test_flash_rgb_carried_fails_if_provision_fails(tmp_path):
    b = tmp_path / "RNode_Firmware.ino.bin"; b.write_bytes(b"BIN")
    h = tmp_path / "partition_hashes"; h.write_text("x")
    conn = carried_conn()
    conn.rules.insert(0, ("--autoinstall", 1, "Flash error", ""))   # first wins
    ok, msg, rgb = flash_rgb_carried(conn, "/dev/ttyACM0",
                                     bin_path=str(b), hasher_path=str(h))
    assert ok is False and rgb is False and "provision" in msg
    # never carried the firmware if the board wasn't provisioned
    assert not conn.pushed
