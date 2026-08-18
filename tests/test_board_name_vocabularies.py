"""One board, four spellings — and the code that has to cross between them.

WHY THIS FILE EXISTS
--------------------
On 2026-08-18 a Heltec V3 was built as an RTNode-2400 on the bench. The birth
certificate the build wrote, read back off the medic afterwards, says:

    board      = 'Heltec32 V3'
    usb_serial = 'usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-...'

Two separate places then failed to recognise that board, both by comparing
names across vocabularies that can never be equal:

* the RTNode gate compared it against the literal "Heltec LoRa32 v3" and
  refused to build a board it had just finished building; and
* the guided flow's ``_board_key_of`` accepted only a catalogue key or a
  catalogue display name, so a board this medic built came back as "" — no
  photo, "this radio" instead of its name, power check with an empty key.

The spellings differ by vocabulary AND by case, which is why this is a table
and not a guess.
"""

import pytest

from workflows.rnode_boards import RNODE_BOARDS, key_for_board_name

#: (spelling, source it comes from) -> the board it means.
SPELLINGS = [
    ("heltec32_v3",      "catalogue key",            "heltec32_v3"),
    ("Heltec LoRa32 v3", "catalogue display_name",   "heltec32_v3"),
    ("Heltec32 V3",      "health beacon / birth cert", "heltec32_v3"),
    ("Heltec LoRa32 V3", "node_profile value",       "heltec32_v3"),
    ("heltec32_v4",      "catalogue key",            "heltec32_v4"),
    ("Heltec LoRa32 v4", "catalogue display_name",   "heltec32_v4"),
    ("Heltec32 V4",      "health beacon / birth cert", "heltec32_v4"),
    ("Heltec LoRa32 V4", "node_profile value",       "heltec32_v4"),
]


@pytest.mark.parametrize("raw,source,expected", SPELLINGS)
def test_every_spelling_of_a_board_resolves_to_one_key(raw, source, expected):
    assert key_for_board_name(raw) == expected, f"{source} spelling not accepted"


def test_the_spellings_really_are_different_strings():
    """If these ever converge, this file is redundant — but until then, the
    reason the crossing is needed has to be visible."""
    from node_profile import NodeHardware
    from monitor.health_beacon import BOARD_IDS
    catalogue = RNODE_BOARDS["heltec32_v3"].display_name
    profile = NodeHardware.HELTEC_V3.value
    beacon = BOARD_IDS[0x3A]
    assert len({catalogue, profile, beacon, "heltec32_v3"}) == 4, (
        "four distinct spellings expected; update this test if that changed")


def test_a_name_we_do_not_stock_resolves_to_nothing():
    for raw in ["", None, "   ", "Nonsense 9000", "Heltec32 V9"]:
        assert key_for_board_name(raw) == ""


def test_every_beacon_board_name_resolves_to_a_stocked_board():
    """A beacon can only report boards we build, so every name it can emit must
    cross back to the catalogue — otherwise a real node reports as unknown."""
    from monitor.health_beacon import BOARD_IDS
    unresolved = {n for n in BOARD_IDS.values()
                  if n.lower().startswith("heltec") and not key_for_board_name(n)}
    assert not unresolved, f"beacon names with no catalogue board: {unresolved}"


def test_the_guided_flow_uses_the_shared_resolver():
    """Wiring guard: _board_key_of must not grow its own comparison again."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_guide_screen.py", "_board_key_of",
                      cls="BirthGuideScreen")
    assert "key_for_board_name" in src
    assert "display_name" not in src, (
        "comparing display names here is the defect this replaced")
