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


def _boards_h(lines):
    return "\n".join(lines)


def unpatched_boards():
    # >440 lines so the pin_np line (if added) would sit past index 440
    lines = [f"// line {i}" for i in range(460)]
    lines[100] = "#define HAS_NP false"
    return _boards_h(lines)


def patched_boards():
    lines = [f"// line {i}" for i in range(460)]
    lines[100] = "#define HAS_NP true"
    lines[450] = "const int pin_np = 47;"
    return _boards_h(lines)


def test_detect_unpatched():
    assert neo.is_patched(unpatched_boards()) is False


def test_detect_patched():
    assert neo.is_patched(patched_boards()) is True


def test_pin_np_must_be_past_line_440():
    # a pin_np = 47 that appears early should NOT count as the V4 patch
    lines = [f"// line {i}" for i in range(460)]
    lines[10] = "const int pin_np = 47;"
    assert neo.is_patched(_boards_h(lines)) is False


def test_apply_patch_adds_directives():
    out = neo.apply_patch(unpatched_boards())
    assert "#define HAS_NP true" in out
    assert "pin_np = 47" in out
    assert neo.is_patched(out) is True


def test_apply_patch_idempotent():
    once = neo.apply_patch(unpatched_boards())
    twice = neo.apply_patch(once)
    assert neo.is_patched(twice) is True
    # applying twice should not duplicate the pin_np definition
    assert twice.count("const int pin_np = 47;") == 1
