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


# --- the RAK4631 shape: two traps the Heltec V4 never exposed ---------------
#
# 1. The board is tested by `#if BOARD_MODEL ==` TWICE — once to pick its modem
#    (no pins at all), once to declare its pin list. Taking the first match
#    found the modem block and died with "no pin anchor".
# 2. Its pin block ALREADY declares `#define HAS_NP false`, above the pin list.
#    Appending a second `#define HAS_NP true` below it is a macro redefinition:
#    the compiler warns, the last one wins, and it looks like it worked.
#
# The V4's block has neither property, which is why the original recipe was
# correct there and wrong here. Shape transcribed from Boards.h at d39339f.
BOARDS_H_RAK = """\
#if BOARD_MODEL == BOARD_RAK4631
      #define MODEM SX1262
    #elif BOARD_MODEL == BOARD_GENERIC_NRF52
      #define MODEM SX1262
    #endif

    #if BOARD_MODEL == BOARD_RAK4631
      #define HAS_DISPLAY true
      #define HAS_NP false
      #define HAS_BUSY true
      const int pin_btn_usr1 = 9;
      const int pin_reset = 38;
      const int pin_cs = 42;
      const int pin_sclk = 43;
      const int pin_mosi = 44;
      const int pin_miso = 45;
      const int pin_busy = 46;
      const int pin_dio = 47;
    #elif BOARD_MODEL == BOARD_TECHO
      #define HAS_NP false
      const int pin_sclk = 19;
    #endif
"""


def _rak_block(text):
    lines = text.splitlines()
    start, end = neo._block_bounds(lines, "BOARD_RAK4631")
    return lines[start:end]


def test_block_bounds_picks_the_pin_block_not_the_modem_block():
    # The modem block is the FIRST textual match and declares no pins.
    block = _rak_block(BOARDS_H_RAK)
    assert any("const int pin_sclk" in ln for ln in block)
    assert not any("#define MODEM" in ln for ln in block)


def test_rak_patch_flips_has_np_instead_of_redefining_it():
    out = neo.apply_patch(BOARDS_H_RAK, "BOARD_RAK4631", 17)
    block = _rak_block(out)
    defines = [ln for ln in block if neo._HAS_NP_DEFINE.match(ln)]
    # Exactly one HAS_NP define, and it says true. Two would compile with a
    # "macro redefined" warning that -Werror turns into a failed build.
    assert len(defines) == 1, f"macro redefinition: {defines}"
    assert "true" in defines[0]
    assert any("const int pin_np = 17;" in ln for ln in block)


def test_rak_patch_is_idempotent_and_detected():
    once = neo.apply_patch(BOARDS_H_RAK, "BOARD_RAK4631", 17)
    assert neo.is_patched(once, "BOARD_RAK4631", 17) is True
    assert neo.apply_patch(once, "BOARD_RAK4631", 17) == once


def test_rak_patch_leaves_the_techo_block_alone():
    out = neo.apply_patch(BOARDS_H_RAK, "BOARD_RAK4631", 17)
    before = "\n".join(_block(BOARDS_H_RAK, "BOARD_TECHO"))
    after = "\n".join(_block(out, "BOARD_TECHO"))
    assert before == after
    assert "pin_np" not in after


def _block(text, board):
    lines = text.splitlines()
    start, end = neo._block_bounds(lines, board)
    return lines[start:end]


def test_a_leftover_false_alongside_pin_np_is_not_counted_as_patched():
    # The half-applied state the redefinition bug produced: pin_np present, but
    # the board's own HAS_NP still false. That must read as UNpatched so a
    # re-run repairs it rather than reporting "Already patched."
    half = BOARDS_H_RAK.replace(
        "      const int pin_dio = 47;",
        "      const int pin_dio = 47;\n      const int pin_np = 17;")
    assert neo.is_patched(half, "BOARD_RAK4631", 17) is False
    fixed = neo.apply_patch(half, "BOARD_RAK4631", 17)
    assert neo.is_patched(fixed, "BOARD_RAK4631", 17) is True
    block = _rak_block(fixed)
    # repaired, not added to: two `const int pin_np` in one block is a hard C++
    # redefinition error, which is worse than the warning we set out to fix.
    assert sum(1 for l in block if neo._PIN_NP_DECL.match(l)) == 1
    assert sum(1 for l in block if neo._HAS_NP_DEFINE.match(l)) == 1


def test_repairs_a_block_the_old_patcher_doubly_defined():
    # Exactly what the previous version produced on the RAK: the board's own
    # `HAS_NP false` left in place, a second `HAS_NP true` appended below it.
    old = BOARDS_H_RAK.replace(
        "      const int pin_sclk = 43;",
        "      const int pin_sclk = 43;\n"
        "      #define HAS_NP true\n"
        "      const int pin_np = 17;")
    assert neo.is_patched(old, "BOARD_RAK4631", 17) is False   # damaged, not done
    block = _rak_block(neo.apply_patch(old, "BOARD_RAK4631", 17))
    assert sum(1 for l in block if neo._HAS_NP_DEFINE.match(l)) == 1
    assert sum(1 for l in block if neo._PIN_NP_DECL.match(l)) == 1
