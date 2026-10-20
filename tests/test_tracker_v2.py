"""Heltec Wireless Tracker V2 (label "Rev. 2.0"), researched 2026-10-20 from
Heltec's own pin map and Meshtastic's variant: the V1.1 board with every pin
the same plus a KCT8103L PA/LNA front end on GPIO4/5/7. Over USB it is the V1,
so the medic must ASK with pictures; after a flash the firmware's own board
byte (0x53) tells them apart. These pin the catalogue, the pickers, the
rnodeconf patch and the picture to that research."""
import json
import os

from workflows.rnode_boards import get_board, custom_boards
from workflows import rnode_flash
from workflows.medic_radio import OWN_RADIO_BOARDS
from ui import firstborn_flow as ff


def test_the_v2_is_its_own_board_with_its_own_codes():
    v1, v2 = get_board("heltec_wireless_tracker"), get_board("heltec_wireless_tracker_v2")
    assert v2.display_name == "Heltec Wireless Tracker V2"
    assert v2.board_model == 0x53 and v1.board_model == 0x52
    assert v2.provision == {"product": "cb", "model": "cc", "platform": "0x80", "hwrev": "2"}
    assert v2.fqbn == v1.fqbn and v2.build_properties == v1.build_properties
    assert v2.flash_method == "arduino_cli" and v2.platform == "ESP32-S3"
    assert v2.bootloader_instructions == v1.bootloader_instructions   # same USER/RST buttons


def test_the_v2_builds_in_the_tracker_tree_but_in_its_own_output_dir():
    v2 = get_board("heltec_wireless_tracker_v2")
    cmd = v2.compile_command()
    assert "-DBOARD_MODEL=0x53" in cmd and "--output-dir build/heltec_wireless_tracker_v2" in cmd
    assert " -e " not in cmd
    assert rnode_flash.fork_build_dir_for(v2) == "~/overlay_test/RNode_Firmware/build/heltec_wireless_tracker_v2"
    assert rnode_flash.fork_image_for(v2, "bin").endswith("/RNode_Firmware.ino.bin")
    assert rnode_flash.fork_flash_size_for(v2) == rnode_flash.fork_flash_size_for(get_board("heltec_wireless_tracker"))
    assert v2 in custom_boards()
    assert v2.picker_name == "Heltec Tracker V2"            # the row has 50 characters, the full name has not
    assert v2.picker_label.startswith("Heltec Tracker V2")
    from workflows.rnode_boards import key_for_board_name
    assert key_for_board_name("Heltec Wireless Tracker V2") == "heltec_wireless_tracker_v2"
    assert key_for_board_name("Heltec Tracker V2") == "heltec_wireless_tracker_v2"


def test_the_v2_can_be_a_medics_own_radio_and_the_picker_offers_it():
    assert OWN_RADIO_BOARDS["heltec_wireless_tracker_v2"] == "Tracker V2"
    assert ("heltec_wireless_tracker_v2", "Heltec Wireless Tracker V2") in ff.board_choices()
    # same USB identity as the V1: the guess stays the V1, the look-alike is named
    assert ff.board_from_usb("Espressif", "USB JTAG/serial debug unit") == "heltec_wireless_tracker"
    assert ff.look_alikes("heltec_wireless_tracker") == ("heltec_wireless_tracker_v2",)
    assert ff.look_alikes("heltec_wireless_tracker_v2") == ("heltec_wireless_tracker",)
    assert ff.look_alikes("heltec_t114_gps") == ()


def test_usb_tables_and_the_firmware_board_byte_know_the_v2():
    from ui import board_detect as bd
    assert bd._RNODE_BOARD_BYTE[0x53] == "heltec_wireless_tracker_v2"
    assert bd._RNODE_BOARD_BYTE[0x52] == "heltec_wireless_tracker"
    assert bd.rnode_board_key("Product (cb:cc:53)") == "heltec_wireless_tracker_v2"
    assert bd.rnode_board_key("Product (cb:ca:52)") == "heltec_wireless_tracker"


def test_the_picture_recovery_power_and_manifest_entries_exist():
    from ui import board_images, safety
    from workflows import power_compat
    assert os.path.isfile("assets/boards/heltec_wireless_tracker_v2.png")
    assert "heltec_wireless_tracker_v2.png" in open("assets/boards/SOURCES.md", encoding="utf-8").read()
    assert "heltec_wireless_tracker_v2" in board_images.BOARD_IMAGES if hasattr(board_images, "BOARD_IMAGES") else True
    assert safety._BY_KEY["heltec_wireless_tracker_v2"] == "Wireless Tracker"
    assert power_compat.BOARD_POWER["heltec_wireless_tracker_v2"]["peak_ma"] >= power_compat.BOARD_POWER["heltec_wireless_tracker"]["peak_ma"]
    m = json.load(open("assets/medic_manifest.json", encoding="utf-8"))
    assert "heltec_wireless_tracker_v2" in m["prebuilt"]["rnode_boards"]


def test_the_rnodeconf_patch_teaches_the_v2_board_and_model():
    patch = open("assets/patches/rns-1.3.7-medic.patch", encoding="utf-8").read()
    assert "+    BOARD_HELTEC_WIRELESS_TRACKER_V2 = 0x53" in patch
    assert '+    0xCC: [863000000, 928000000, 28, "863 - 928 MHz"' in patch
    assert "+    BOARD_HELTEC_WIRELESS_TRACKER   = 0x52" in patch      # the V1 line survived the regeneration
