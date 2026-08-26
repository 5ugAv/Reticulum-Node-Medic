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
    assert "raspberry pi 5" in low and "sd" in low and "cable" in low
    assert "switched off" in low                 # power-off-first up front
    assert "take me back" in low                 # graceful exit if unprepared
    assert "_show_stage_insert()" in pf          # 'start' proceeds to the card
