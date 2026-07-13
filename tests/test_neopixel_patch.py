import importlib.util
import os

import pytest

# Load the script module from assets/scripts (not a package).
_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "assets", "scripts",
    "apply_neopixel_patch.py")
_spec = importlib.util.spec_from_file_location("apply_neopixel_patch", _SCRIPT)
neo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(neo)


# A realistic slice of RNode_Firmware/Boards.h: a per-board `#elif BOARD_MODEL`
# chain. The V4 block has NO HAS_NP/pin_np (that's what we add); the neighbouring
# NG_20 block DOES (with a nested `#if HAS_NP == false`), so the patch must stay
# block-scoped and the nested #if must not confuse block-boundary detection.
BOARDS_H = """\
#if BOARD_MODEL == BOARD_HELTEC32_V3
      const int pin_cs = 8;
      const int pin_busy = 13;
      const int pin_sclk = 9;
    #elif BOARD_MODEL == BOARD_HELTEC32_V4
      #define HAS_DISPLAY true
      const int pin_cs = 8;
      const int pin_busy = 13;
      const int pin_dio = 14;
      const int pin_mosi = 10;
      const int pin_miso = 11;
      const int pin_sclk = 9;
    #elif BOARD_MODEL == BOARD_RNODE_NG_20
      #define HAS_NP true
      const int pin_cs = 18;
      const int pin_np = 4;
      #if HAS_NP == false
        const int pin_led_rx = 2;
        const int pin_led_tx = 0;
      #endif
    #endif
"""


def test_detect_unpatched():
    assert neo.is_patched(BOARDS_H) is False


def test_neighbouring_block_np_does_not_count_as_patched():
    # NG_20 has HAS_NP true + pin_np, but the V4 block does not -> not patched.
    assert neo.is_patched(BOARDS_H) is False


def test_apply_patch_adds_directives_inside_v4_block():
    out = neo.apply_patch(BOARDS_H)
    assert neo.is_patched(out) is True
    lines = out.splitlines()
    v4 = next(i for i, l in enumerate(lines) if "BOARD_HELTEC32_V4" in l)
    ng = next(i for i, l in enumerate(lines) if "BOARD_RNODE_NG_20" in l)
    # both new directives land strictly inside the V4 block
    has_np = next(i for i, l in enumerate(lines)
                  if "#define HAS_NP true" in l and v4 < i < ng)
    pin_np = next(i for i, l in enumerate(lines)
                  if "const int pin_np = 47;" in l and v4 < i < ng)
    assert has_np < pin_np  # inserted right after pin_sclk, in order


def test_apply_patch_inserts_after_pin_sclk():
    out = neo.apply_patch(BOARDS_H).splitlines()
    sclk_idxs = [i for i, l in enumerate(out) if "pin_sclk = 9;" in l]
    # the V4 sclk (2nd occurrence) is immediately followed by our two lines
    v4_sclk = sclk_idxs[1]
    assert "#define HAS_NP true" in out[v4_sclk + 1]
    assert "const int pin_np = 47;" in out[v4_sclk + 2]


def test_apply_patch_leaves_other_blocks_untouched():
    out = neo.apply_patch(BOARDS_H)
    # V3 block still has no NeoPixel; NG_20 still has exactly its own pin_np = 4
    assert out.count("const int pin_np = 47;") == 1
    assert out.count("const int pin_np = 4;") == 1


def test_apply_patch_idempotent():
    once = neo.apply_patch(BOARDS_H)
    twice = neo.apply_patch(once)
    assert neo.is_patched(twice) is True
    assert twice.count("const int pin_np = 47;") == 1


def test_nested_if_does_not_truncate_block_detection():
    # the NG_20 block's nested `#if HAS_NP == false ... #endif` must not be read
    # as the end of the block: patching NG_20 still finds its pin anchor.
    out = neo.apply_patch(BOARDS_H, board="BOARD_RNODE_NG_20", pin=48)
    # inserted inside NG_20 (which already had pin_np = 4) -> now also pin_np = 48
    assert "const int pin_np = 48;" in out


def test_generalises_to_another_board_and_pin():
    out = neo.apply_patch(BOARDS_H, board="BOARD_HELTEC32_V3", pin=33)
    assert neo.is_patched(out, board="BOARD_HELTEC32_V3", pin=33) is True
    lines = out.splitlines()
    v3 = next(i for i, l in enumerate(lines) if "BOARD_HELTEC32_V3" in l)
    v4 = next(i for i, l in enumerate(lines) if "BOARD_HELTEC32_V4" in l)
    assert any("pin_np = 33;" in l for l in lines[v3:v4])  # inside V3 block only


def test_missing_board_raises():
    with pytest.raises(ValueError):
        neo.apply_patch(BOARDS_H, board="BOARD_DOES_NOT_EXIST")
