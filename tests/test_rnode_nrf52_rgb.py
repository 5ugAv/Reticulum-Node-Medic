"""The nRF52 RGB build recipe must match the firmware's own Makefile.

These are contract tests, not behaviour tests. Every command in
``workflows/rnode_nrf52_rgb.py`` is transcribed from ``release-rak4631`` and
``prep-nrf`` upstream, and a silent drift there produces an image that flashes
cleanly and then behaves like a hardware fault — the worst failure shape we
have, on a board whose only job is the radio.
"""

import importlib.util
import os

import pytest

from workflows import rnode_nrf52_rgb as nrf

_PATCHER = os.path.join(os.path.dirname(__file__), "..", "assets", "scripts",
                        "apply_neopixel_patch.py")
_spec = importlib.util.spec_from_file_location("apply_neopixel_patch", _PATCHER)
neo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(neo)


def _rak():
    return nrf.target_for("rak4631")


# --- the recipe, as upstream states it -----------------------------------

def test_fqbn_is_the_rakwireless_core_not_adafruit():
    # adafruit:nrf52:pca10056 is the T-Echo's FQBN. Using it here would compile
    # against the wrong variant's pin map.
    assert _rak().fqbn == "rakwireless:nrf52:WisCoreRAK4631Board"


def test_build_dir_matches_the_fqbn():
    # arduino-cli derives the build subdirectory from the FQBN by replacing ':'
    # with '.'. If these disagree the copy step silently finds nothing.
    assert _rak().build_subdir == _rak().fqbn.replace(":", ".")


def test_compile_carries_the_big_image_properties():
    cmd = nrf.compile_command(_rak())
    assert "build.partitions=no_ota" in cmd
    assert "upload.maximum_size=2097152" in cmd


def test_compile_declares_the_rak_board_model():
    # 0x51 is BOARD_RAK4631 in Boards.h. A wrong value compiles a different
    # board's pin block into the image.
    assert "-DBOARD_MODEL=0x51" in nrf.compile_command(_rak())


def test_compile_uses_export_binaries_so_the_hex_lands_in_the_tree():
    assert " -e " in nrf.compile_command(_rak())


def test_package_uses_the_nrf52840_dev_type():
    cmds = " ".join(nrf.package_commands(_rak()))
    assert "--dev-type 0x0052" in cmds
    assert "adafruit-nrfutil dfu genpkg" in cmds


def test_package_wraps_the_hex_the_compile_produced():
    t = _rak()
    assert t.hex_path.endswith(
        "build/rakwireless.nrf52.WisCoreRAK4631Board/RNode_Firmware.ino.hex")
    assert t.package.endswith("Release/rnode_firmware_rak4631.zip")


# --- the guard rails ------------------------------------------------------

def test_an_unresearched_nrf52_board_has_no_recipe():
    # The T-Echo and Heltec T114 build the same way but no NeoPixel pin has been
    # verified on either. They must fail, not inherit the RAK's pin.
    for key in ("techo", "heltec_t114", "", "rak4631_v2"):
        with pytest.raises(nrf.UnsupportedTarget):
            nrf.target_for(key)


def test_patch_command_does_not_pass_a_pin():
    # The pin has exactly one home: VERIFIED_NP_PIN in the patcher. Passing one
    # here would let this module drift from the guard that refuses a claimed pin.
    cmd = nrf.patch_command(_rak())
    assert "--pin" not in cmd
    assert "--board BOARD_RAK4631" in cmd


def test_patch_command_never_forces_the_pin_check():
    assert "--i-have-verified-this-pin" not in nrf.patch_command(_rak())


def test_the_board_macro_this_module_patches_is_one_the_patcher_knows():
    # The two tables have to agree or the build patches nothing and compiles a
    # NeoPixel-less image that looks like a dead LED.
    assert _rak().board_macro in neo.VERIFIED_NP_PIN


def test_the_verified_pin_is_not_a_claimed_one():
    macro = _rak().board_macro
    neo.check_pin(macro, neo.VERIFIED_NP_PIN[macro])      # must not raise


def test_the_lora_dio_pin_is_still_refused_for_this_board():
    # 47 is the Heltec V4's free header pin and the RAK's LoRa DIO line. This is
    # the collision the whole guard exists for.
    with pytest.raises(neo.UnverifiedPin):
        neo.check_pin(_rak().board_macro, 47)


# --- ordering -------------------------------------------------------------

def test_build_runs_patch_then_compile_then_package():
    cmds = nrf.build_commands("rak4631")
    joined = [c for c in cmds]
    assert "apply_neopixel_patch" in joined[0]
    assert "arduino-cli compile" in joined[1]
    assert "cp " in joined[2]
    assert "genpkg" in joined[3]


def test_build_refuses_an_unknown_board_before_running_anything():
    with pytest.raises(nrf.UnsupportedTarget):
        nrf.build_commands("heltec32_v4")     # ESP32 — wrong module entirely


def test_dfu_flash_targets_a_port_and_the_built_package():
    cmd = nrf.dfu_flash_command(_rak(), "/dev/ttyACM1")
    assert "dfu serial" in cmd
    assert "-p /dev/ttyACM1" in cmd
    assert "rnode_firmware_rak4631.zip" in cmd
    # ~ must be expanded: adafruit-nrfutil is not run through a shell that would.
    assert "~" not in cmd


def test_rgb_package_available_is_false_when_nothing_is_built(tmp_path):
    assert nrf.rgb_package_available("rak4631", str(tmp_path)) is False


def test_rgb_package_available_is_false_for_an_unsupported_board():
    assert nrf.rgb_package_available("techo") is False
