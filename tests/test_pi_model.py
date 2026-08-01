"""Identifying WHICH Pi is attached.

The host Pi is what the power check reasons about, so a wrong pick produces a
wrong verdict about whether a pairing needs a powered hub. These tests are
mostly about not answering when we don't actually know.
"""

import pytest

from provisioning import pi_model as pm


# --- exact, from the Pi's own model string ---------------------------------

@pytest.mark.parametrize("model,key", [
    ("Raspberry Pi Zero 2 W Rev 1.0", "pi_zero_2w"),   # read live off HOPE
    ("Raspberry Pi 3 Model A Plus Rev 1.0", "pi_3a_plus"),
    ("Raspberry Pi 3 Model B Plus Rev 1.3", "pi_3b_plus"),
    ("Raspberry Pi 4 Model B Rev 1.4", "pi_4b"),
    ("Raspberry Pi 5 Model B Rev 1.1", "pi_5"),        # the medic itself
])
def test_real_model_strings_map_to_the_right_power_profile(model, key):
    got = pm.from_model_string(model)
    assert got.key == key
    assert got.confidence == pm.EXACT
    assert got.should_auto_select and not got.is_assumed


def test_a_trailing_null_from_device_tree_is_handled():
    """/proc/device-tree/model is NUL-terminated; the raw read carries it."""
    got = pm.from_model_string("Raspberry Pi Zero 2 W Rev 1.0\x00")
    assert got.key == "pi_zero_2w" and "\x00" not in got.model


def test_something_that_is_not_a_pi_is_not_guessed_at():
    for text in ("", "Some Other SBC v2", "Raspberry Pi Pico"):
        got = pm.from_model_string(text)
        assert got.should_auto_select is False


# --- the hint, from the boot-ROM USB id ------------------------------------

def test_a_zero_and_a_3a_plus_are_NOT_told_apart_by_the_chip():
    """Both are BCM2837-class. Measured on the medic: iSerial 0, bcdDevice 0.00
    — there is no bit present that separates them."""
    got = pm.from_usb_id("0a5c:2764")
    assert got.confidence == pm.NARROWED
    assert got.is_assumed
    assert set(got.candidates) >= {"pi_zero_2w", "pi_3a_plus"}


def test_an_ambiguous_chip_assumes_the_LOWEST_power_candidate():
    """The failure modes are not symmetric. Assuming a Zero 2 W costs a warning
    that wasn't needed; assuming a 3 B+ costs a genuinely under-powered node
    shipping with no warning at all."""
    from workflows.power_compat import PI_POWER
    got = pm.from_usb_id("0a5c:2764")
    budgets = {k: PI_POWER[k]["budget_ma"] for k in got.candidates if k in PI_POWER}
    assert got.key == min(budgets, key=budgets.get) == "pi_zero_2w"
    assert got.should_auto_select, "should fill in rather than block the operator"


def test_a_single_candidate_chip_is_filled_in_but_still_not_called_exact():
    """A Pi 4 is the only board we profile on BCM2711 — but it was inferred,
    not read, and must not claim otherwise."""
    got = pm.from_usb_id("0a5c:2711")
    assert got.key == "pi_4b"
    assert got.confidence == pm.NARROWED and got.is_assumed
    assert got.should_auto_select


def test_an_unknown_usb_id_says_so():
    assert pm.from_usb_id("1234:5678").confidence == pm.UNKNOWN
    assert pm.from_usb_id("").confidence == pm.UNKNOWN


# --- detection order --------------------------------------------------------

def test_the_pis_own_answer_beats_the_chip_hint():
    got = pm.detect(
        run_on_pi=lambda cmd: (0, "Raspberry Pi Zero 2 W Rev 1.0\x00", ""),
        lsusb_fn=lambda: "ID 0a5c:2711 Broadcom")     # would say Pi 4
    assert got.key == "pi_zero_2w" and got.confidence == pm.EXACT


def test_it_falls_back_to_the_chip_when_the_pi_cannot_be_reached():
    got = pm.detect(run_on_pi=lambda cmd: (1, "", "no cable link"),
                    lsusb_fn=lambda: "Bus 003 Device 9: ID 0a5c:2764 Broadcom")
    assert got.confidence == pm.NARROWED
    assert "pi_zero_2w" in got.candidates


def test_detection_never_raises():
    def boom(_cmd):
        raise OSError("ssh exploded")
    got = pm.detect(run_on_pi=boom, lsusb_fn=lambda: (_ for _ in ()).throw(OSError))
    assert got.confidence == pm.UNKNOWN


# --- what the operator is told ---------------------------------------------

def _name(k):
    return {"pi_zero_2w": "Raspberry Pi Zero 2 W", "pi_3a_plus": "Raspberry Pi 3 A+",
            "pi_3b_plus": "Raspberry Pi 3 B+", "pi_4b": "Raspberry Pi 4 B"}.get(k, k)


def test_an_exact_read_says_where_it_came_from():
    line = pm.describe(pm.from_model_string("Raspberry Pi Zero 2 W Rev 1.0"), _name)
    assert "Zero 2 W" in line and "read from the Pi itself" in line


def test_an_assumed_answer_says_it_is_assumed_and_will_be_confirmed():
    """The operator must be able to tell a guess from a reading."""
    line = pm.describe(pm.from_usb_id("0a5c:2764"), _name)
    assert "Zero 2 W" in line
    assert "assumed" in line and "confirm" in line


def test_no_pi_at_all_falls_back_to_the_plain_prompt():
    assert "Tap to choose" in pm.describe(pm.from_usb_id(""), _name)


def test_the_birth_screen_auto_selects_only_on_an_exact_read():
    """Source guard: auto-selecting from the chip hint would silently choose
    between a 500 mA and a 1000 mA power budget."""
    src = open("ui/screens/birth_screen.py").read()
    assert "pi_model.detect()" in src
    assert "should_auto_select" in src


def test_the_birth_screen_shows_how_it_decided():
    src = open("ui/screens/birth_screen.py").read()
    assert "pi_model.describe(" in src
