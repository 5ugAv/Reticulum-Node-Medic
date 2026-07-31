"""The RNode birth-cry patcher — anchors, idempotency, choreography parity."""

import importlib.util
import os

_SCRIPT = os.path.join(
    os.path.dirname(__file__), os.pardir, "assets", "scripts",
    "apply_birth_cry.py")
_spec = importlib.util.spec_from_file_location("apply_birth_cry", _SCRIPT)
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)

FAKE_INO = """\
#include "Config.h"
#include "Utilities.h"

void setup() {
  boot_seq();
  led_init();
}

void serial_callback(uint8_t sbyte) {
    } else if (command == CMD_RESET) {
      if (sbyte == CMD_RESET_BYTE) {
        hard_reset();
      }
    } else if (command == CMD_ROM_READ) {
      kiss_dump_eeprom();
    }
}
"""


def _write_sketch(tmp_path):
    ino = tmp_path / "RNode_Firmware.ino"
    ino.write_text(FAKE_INO)
    return str(ino)


def test_patch_inserts_include_and_serial_trigger(tmp_path):
    ino = _write_sketch(tmp_path)
    msg = bc.apply(ino)
    src = open(ino).read()
    assert "include" in msg and "serial-trigger" in msg
    # include comes right after Utilities.h (BirthCry needs npset)
    assert '#include "Utilities.h"\n#include "BirthCry.h"' in src
    # the trigger slots into the KISS chain BEFORE CMD_ROM_READ, and only
    # sings on the 0xF8 confirmation byte
    assert src.index("command == 0xB5") < src.index("command == CMD_ROM_READ")
    assert "birth_cry();" in src
    assert (tmp_path / "BirthCry.h").exists()


def test_patch_is_idempotent(tmp_path):
    ino = _write_sketch(tmp_path)
    bc.apply(ino)
    once = open(ino).read()
    msg = bc.apply(ino)                      # second run: refresh only
    assert "already present" in msg
    assert open(ino).read() == once
    assert once.count("birth_cry();") == 1


def test_missing_anchor_is_loud(tmp_path):
    ino = tmp_path / "RNode_Firmware.ino"
    ino.write_text("void setup() {}\n")
    try:
        bc.apply(str(ino))
        raise AssertionError("expected ValueError on missing anchor")
    except ValueError:
        pass


def test_choreography_matches_the_rtnode_cry():
    """The tuned constants must stay in lockstep with the RTNode original —
    the fleet sings ONE song (operator spec: same birth cry on all nodes)."""
    rt = open(os.path.join(
        os.path.dirname(__file__), os.pardir, "firmware", "rtnode-2400",
        "BirthCry.h")).read()
    for magic in ("GROW_MS = 9000", "0.008f * expf(2.70f * p)",
                  "ROCKET_MS = 1000", "0.12f * expf(2.12f * p)",
                  "period = 340.0f", "period *= 0.88f", "delay(260)"):
        assert magic in rt, f"RTNode cry lost: {magic}"
        assert magic in bc.BIRTH_CRY_H, f"RNode cry differs: {magic}"


def test_header_no_op_without_neopixel():
    assert "inline void birth_cry() {}" in bc.BIRTH_CRY_H  # no-NP fallback


def test_workflow_ends_with_the_cry():
    """The flash pipeline's LAST step commands the song — after verify, so
    the show lands with the green confirmation."""
    from workflows.rnode_v4_rgb import HeltecV4RGBWorkflow
    assert HeltecV4RGBWorkflow._FLASH[-1] == "_birth_cry"
    assert HeltecV4RGBWorkflow._FLASH[-2] == "_verify"
