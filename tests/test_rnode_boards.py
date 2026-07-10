import os

import pytest

from workflows.rnode_boards import (
    RNodeBoard,
    RNODE_BOARDS,
    available_boards,
    official_boards,
    custom_boards,
    get_board,
)
from ui.safety import recovery_text


def test_wireless_tracker_registered():
    b = get_board("heltec_wireless_tracker")
    assert isinstance(b, RNodeBoard)
    assert b.display_name == "Heltec Wireless Tracker"
    assert b.board_model == 0x52
    assert "esp32s3" in b.fqbn
    assert "CDCOnBoot=cdc" in b.fqbn


def test_bootloader_instructions_mention_the_real_buttons():
    b = get_board("heltec_wireless_tracker")
    assert "USER" in b.bootloader_instructions
    assert "RST" in b.bootloader_instructions
    # native-USB caveat researched from Heltec docs
    assert "native USB" in b.bootloader_instructions or "USB" in b.bootloader_instructions


def test_recovery_matches_the_shared_safety_module():
    b = get_board("heltec_wireless_tracker")
    assert b.recovery_instructions == recovery_text("Wireless Tracker")


def test_provisioning_codes_match_the_flasher():
    b = get_board("heltec_wireless_tracker")
    assert b.provision["platform"] == "0x80"     # ESP32
    assert b.provision["product"] == "cb"
    assert b.provision["model"] == "ca"
    assert b.provision["hwrev"] == "1"


def test_available_boards_lists_it():
    keys = [b.key for b in available_boards()]
    assert "heltec_wireless_tracker" in keys


def test_get_unknown_board_returns_none():
    assert get_board("does_not_exist") is None


def test_compile_command_uses_fqbn_and_board_model():
    b = get_board("heltec_wireless_tracker")
    cmd = b.compile_command()
    assert b.fqbn in cmd
    assert "-DBOARD_MODEL=0x52" in cmd
    assert "arduino-cli compile" in cmd


def test_upload_command_uses_same_fqbn_and_port():
    b = get_board("heltec_wireless_tracker")
    cmd = b.upload_command("/dev/cu.usbmodem2101")
    assert b.fqbn in cmd                          # same FQBN as compile (the fix)
    assert "/dev/cu.usbmodem2101" in cmd
    assert "arduino-cli upload" in cmd


def test_provision_commands_wipe_then_provision():
    b = get_board("heltec_wireless_tracker")
    cmds = b.provision_commands("/dev/ttyUSB0")
    assert any("--eeprom-wipe" in c for c in cmds)
    prov = next(c for c in cmds if "--product" in c)
    assert "--platform 0x80" in prov and "--model ca" in prov


# ---- full official-board registry ---------------------------------------


def test_registry_lists_more_than_just_the_tracker():
    # the whole point: all official RNode boards, not one custom board
    assert len(available_boards()) >= 14
    names = {b.display_name for b in available_boards()}
    assert {"Heltec LoRa32 v3", "LilyGO T-Beam", "RAK4631",
            "Seeed XIAO ESP32S3 (Wio-SX1262)"} <= names


def test_official_boards_are_autoinstall_with_unique_menu_indices():
    off = official_boards()
    assert len(off) >= 14
    idxs = [b.autoinstall_index for b in off]
    assert len(idxs) == len(set(idxs))              # no collisions
    assert all(3 <= i <= 16 for i in idxs)          # real rnodeconf menu range
    assert all(b.flash_method == "autoinstall" for b in off)


def test_official_boards_are_offline_flashable_via_autoinstall():
    b = get_board("heltec32_v3")
    assert b.autoinstall_index == 8
    assert b.platform == "ESP32"
    cmd = b.autoinstall_command("/dev/ttyACM0", version="1.86")
    assert "rnodeconf /dev/ttyACM0 --autoinstall" in cmd
    assert "--nocheck" in cmd                       # offline by default
    assert "--fw-version 1.86" in cmd


def test_every_board_has_bootloader_and_recovery_text():
    for b in available_boards():
        assert b.bootloader_instructions.strip()
        assert b.recovery_instructions.strip()


def test_nrf52_boards_use_uf2_recovery_wording():
    rak = get_board("rak4631")
    assert rak.platform == "nRF52"
    assert "UF2" in rak.bootloader_instructions or "double-tap" in \
        rak.bootloader_instructions.lower()


def test_custom_boards_are_the_tracker_only():
    customs = custom_boards()
    assert [b.key for b in customs] == ["heltec_wireless_tracker"]
    assert customs[0].flash_method == "arduino_cli"


def test_carried_flasher_script_exists_and_is_hardened():
    b = get_board("heltec_wireless_tracker")
    path = os.path.join(os.path.dirname(__file__), "..", "assets", "scripts",
                        b.carried_script)
    body = open(path).read()
    # the multi-board / eeprom-wipe guard and single FQBN fixes
    assert "More than one USB board" in body
    assert "count_boards" in body
    assert 'FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc"' in body
    assert "mapfile -t" not in body               # bash 3.2 safe (no mapfile cmd)
