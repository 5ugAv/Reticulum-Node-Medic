"""The radio's serial must survive the trip from flash to build.

2026-08-12 handover: the medic flashes the radio at step 0 and reads its USB
serial; the radio then rides in the operator's pocket while the Pi is built, so
the node-side probe finds nothing; and the one string that could pin
/dev/rnode to THIS radio was discarded at the hand-back. These tests hold each
link of the chain. They are source-level because Kivy is not importable in CI —
the carried value's real behaviour is covered in test_build_workflow.py
(test_radio_rule_uses_the_serial_the_medic_carried_when_none_attached).
"""

from tests.srcutil import func_source


def test_the_flash_hand_back_carries_the_radio_serial():
    src = func_source("ui/screens/birth_screen.py", "_hand_back_to_guide")
    assert "radio_usb_serial" in src
    assert "_usb_serial" in src         # from the flash workflow's own capture


def test_begin_guided_accepts_and_stores_the_serial():
    src = func_source("ui/screens/birth_screen.py", "begin_guided")
    assert "radio_usb_serial" in src


def test_a_fresh_lap_forgets_the_last_radios_serial():
    # One node's serial must never leak onto the next birth — same hygiene as
    # the location prefill (2026-08-01 bug).
    src = func_source("ui/screens/birth_screen.py", "_fresh_lap")
    assert "_guided_radio_usb_serial" in src


def test_the_pi_build_profile_carries_the_serial():
    src = func_source("ui/screens/birth_screen.py", "_make_workflow")
    assert "usb_serial" in src


def test_the_guide_hands_the_serial_on():
    src = func_source("ui/screens/birth_guide_screen.py", "_hand_over_name")
    assert "radio_usb_serial" in src


def test_the_profile_has_a_place_for_it():
    from node_profile import RadioConfig
    assert RadioConfig().usb_serial == ""
