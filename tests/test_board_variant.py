"""Telling apart boards that share a chip family.

Bench, 2026-08-02: a LilyGO LoRa32 v2.1 (silkscreen T3 V1.6.1, microSD slot,
unsigned.io Pocket Node build) read as plain "esp32" — the same as the T-Beam,
Heltec V2 and the older LoRa32s — leaving the operator a five-way guess. Its
exact package settles it.
"""

from ui.board_detect import (parse_chip_variant, narrow_by_variant,
                             detect_board, _CHIP_VARIANT)
from workflows.rnode_boards import RNODE_BOARDS

# Verbatim from the board on the bench.
POCKET_NODE = """esptool.py v3.3.3
Serial port /dev/ttyACM1
Connecting.......
Detecting chip type... ESP32
Chip is ESP32-PICO-D4 (revision v1.1)
Features: WiFi, BT, Dual Core, 240MHz, Embedded Flash, VRef calibration in efuse
Crystal is 40MHz
MAC: 02:00:00:0d:00:0d
"""

PLAIN_ESP32 = """Detecting chip type... ESP32
Chip is ESP32-D0WDQ6 (revision 1)
Crystal is 40MHz
"""


def test_the_exact_package_is_parsed():
    assert parse_chip_variant(POCKET_NODE) == "esp32-pico-d4"
    assert parse_chip_variant(PLAIN_ESP32) == "esp32-d0wdq6"
    assert parse_chip_variant("no chip line here") is None


def test_a_pico_d4_narrows_five_candidates_to_the_lora32_v21():
    """The whole point: 'esp32' alone can't separate these five."""
    five = [RNODE_BOARDS[k] for k in
            ("lora32_v21", "lora32_v20", "lora32_v10", "tbeam", "heltec32_v2")]
    got = narrow_by_variant(five, "esp32-pico-d4")
    assert [b.key for b in got] == ["lora32_v21"]


def test_an_unrecognised_variant_changes_nothing():
    """Board revisions vary. A variant we haven't measured must leave the
    shortlist alone rather than empty it — a wrong exclusion costs a tap."""
    five = [RNODE_BOARDS[k] for k in
            ("lora32_v21", "lora32_v20", "lora32_v10", "tbeam", "heltec32_v2")]
    assert narrow_by_variant(five, "esp32-d0wdq6") == five
    assert narrow_by_variant(five, None) == five
    assert narrow_by_variant(five, "esp32-something-new") == five


def test_narrowing_never_invents_a_board_outside_the_shortlist():
    two = [RNODE_BOARDS["tbeam"], RNODE_BOARDS["heltec32_v2"]]
    got = narrow_by_variant(two, "esp32-pico-d4")
    assert got == two, "must not add lora32_v21 to a list it wasn't in"


def test_end_to_end_the_pocket_node_is_auto_identified():
    """detect_board should now hand back one board, not five."""
    r = detect_board(list(RNODE_BOARDS.values()),
                     ports_fn=lambda: ["/dev/ttyACM1"],
                     reader=lambda p: POCKET_NODE)
    assert r["found"] is True
    assert [b.key for b in r["boards"]] == ["lora32_v21"]
    assert r["board_key"] == "lora32_v21", "a lone survivor should auto-pick"


def test_only_measured_variants_are_claimed():
    """Every entry here is a board someone physically read. Guessing a variant
    from a datasheet is how a confident wrong answer gets in."""
    assert set(_CHIP_VARIANT) == {"lora32_v21"}
