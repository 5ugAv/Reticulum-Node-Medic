import importlib.util
import os

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "assets", "scripts",
    "apply_boot_error_color.py")
_spec = importlib.util.spec_from_file_location("apply_boot_error_color", _SCRIPT)
bec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bec)


# The real stock function shape from RNode_Firmware/Utilities.h: a NeoPixel
# branch that latches full white forever, and a discrete-LED #else branch that
# must NOT be touched.
UTIL_H = """\
void led_indicate_airtime_lock() { npset(0xFF, 0x00, 0xFF); }

void led_indicate_boot_error() {
\t#if HAS_NP == true
\t\twhile(true) {
\t\t\tnpset(0xFF, 0xFF, 0xFF);
\t\t}
\t#else
\t\twhile (true) {
\t\t    led_tx_on();
\t\t    led_rx_off();
\t\t    delay(10);
\t\t}
\t#endif
}

void led_indicate_warning(int cycles) { npset(0xFF, 0xFF, 0xFF); }
"""


def test_detect_unpatched():
    assert bec.is_patched(UTIL_H) is False


def test_apply_replaces_white_with_dim_red():
    out = bec.apply_patch(UTIL_H)
    assert bec.is_patched(out) is True
    assert "npset(0x40, 0x00, 0x00)" in out
    # the boot-error white is gone
    lines = out.splitlines()
    s = next(i for i, l in enumerate(lines) if "led_indicate_boot_error" in l)
    e = next(i for i, l in enumerate(lines[s:]) if "led_indicate_warning" in l) + s
    body = "\n".join(lines[s:e])
    assert "npset(0xFF, 0xFF, 0xFF)" not in body


def test_only_touches_boot_error_function():
    out = bec.apply_patch(UTIL_H)
    # warning() still has its own white; airtime_lock magenta untouched
    assert "void led_indicate_warning(int cycles) { npset(0xFF, 0xFF, 0xFF); }" in out
    assert "npset(0xFF, 0x00, 0xFF)" in out  # airtime lock magenta intact


def test_else_branch_untouched():
    out = bec.apply_patch(UTIL_H)
    assert "led_tx_on();" in out and "led_rx_off();" in out


def test_custom_red_value():
    out = bec.apply_patch(UTIL_H, red=0x20)
    assert "npset(0x20, 0x00, 0x00)" in out


def test_idempotent():
    once = bec.apply_patch(UTIL_H)
    twice = bec.apply_patch(once)
    assert twice.count("npset(0x40, 0x00, 0x00)") == 1
    assert bec.is_patched(twice) is True


def test_missing_function_raises():
    with pytest.raises(ValueError):
        bec.apply_patch("int main() { return 0; }")
