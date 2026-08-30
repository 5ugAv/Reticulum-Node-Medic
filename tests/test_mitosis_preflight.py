"""The MITOSIS flow must open with a 'what you need' pre-flight, so a keeper
never reaches 'move the card to the new medic' holding a card with nowhere to
put it (walkthrough 2026-08-26). Source-level pins — the screen is kivy."""

import pathlib

SRC = pathlib.Path("ui/screens/mitosis_screen.py").read_text()


def test_begin_opens_the_preflight_not_the_card_insert():
    assert "def _show_stage_preflight" in SRC
    # begin() routes to preflight first
    b = SRC.split("def begin(self):", 1)[1].split("def ", 1)[0]
    assert "_show_stage_preflight()" in b
    assert "_show_stage_insert()" not in b   # insert is now reached FROM preflight


def test_preflight_lists_the_hardware_and_offers_a_way_back():
    pf = SRC.split("def _show_stage_preflight", 1)[1].split("def _leave_home", 1)[0]
    low = pf.lower()
    # parts, not products (operator, 2026-08-30): raw parts a person can
    # actually go and fetch — the old "a second Node Medic, switched OFF"
    # was circular (they are MAKING it). Power-state guidance moved to the
    # stage where the Pi first enters the flow.
    assert "raspberry pi 5" in low and "sd" in low
    assert "ethernet cable" in low and "card reader" in low
    assert "32" in low and "64" in low           # capacity guidance
    # the FULL build (operator, 2026-08-30): radio + its plumbing included,
    # so nobody discovers a missing part at the firstborn step
    assert "tracker" in low and "915" in low
    assert "usb-a to usb-c" in low and "pigtail" in low
    assert "power supply" in low and "touch screen" in low
    assert "take me back" in low                 # graceful exit if unprepared
    assert "_show_stage_insert()" in pf          # 'start' proceeds to the card
