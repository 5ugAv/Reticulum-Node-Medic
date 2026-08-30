"""Learned board traits — the self-thinning board picker.

Operator, 2026-08-30, staring at a gallery of ESP32-S3 boards with a
Tracker in hand: "can node medic thin this selection... more information
gatherable to distinguish it?" The rule that makes this safe: a model is
only ever filtered out by a trait we MEASURED on a board the operator
confirmed — never by a datasheet, and never on missing data.
"""

import json

from ui.board_detect import parse_psram
from ui.board_traits import learn, narrow_by_traits, traits_for


class B:
    def __init__(self, key):
        self.key = key


def store(tmp_path):
    return str(tmp_path / "board_traits.json")


# ---------------------------------------------------------------- parsing

def test_psram_size_is_read_from_the_features_line():
    out = ("Chip is ESP32-S3 (revision v0.2)\n"
           "Features: WiFi, BLE, Embedded PSRAM 8MB (AP_3v3)\n")
    assert parse_psram(out) == "8MB"


def test_no_psram_is_a_FACT_not_a_shrug():
    # a Features line without PSRAM means the chip HAS none — that is a
    # discriminator, and it must not be confused with "we didn't look"
    assert parse_psram("Chip is ESP32-S3\nFeatures: WiFi, BLE\n") == "none"


def test_absent_features_line_says_nothing():
    assert parse_psram("Chip is ESP32-S3 (revision v0.2)\n") is None
    assert parse_psram("") is None


def test_psram_present_but_unsized():
    assert parse_psram("Features: WiFi, BLE, Embedded PSRAM\n") == "yes"


# --------------------------------------------------------------- learning

def test_confirmed_board_teaches_its_model(tmp_path):
    p = store(tmp_path)
    assert learn("tracker", {"psram": "none", "flash_size": "8MB"}, p)
    assert traits_for("tracker", p) == {"psram": "none", "flash_size": "8MB"}


def test_learning_never_stores_blanks_or_raises(tmp_path):
    p = store(tmp_path)
    assert learn("tracker", {"psram": None, "flash_size": ""}, p) is False
    assert traits_for("tracker", p) == {}
    assert learn("", {"psram": "8MB"}, p) is False
    # an unwritable path is a shrug, never a crash mid-birth
    assert learn("x", {"psram": "8MB"}, "/proc/nope/traits.json") is False


def test_newer_reading_wins(tmp_path):
    p = store(tmp_path)
    learn("v4", {"flash_size": "8MB"}, p)
    learn("v4", {"flash_size": "16MB"}, p)      # the V4 really did measure 16
    assert traits_for("v4", p)["flash_size"] == "16MB"


# -------------------------------------------------------------- narrowing

def test_a_contradicted_model_is_dropped(tmp_path):
    p = store(tmp_path)
    learn("xiao", {"psram": "8MB"}, p)          # XIAO has PSRAM
    learn("tracker", {"psram": "none"}, p)      # the Tracker doesn't
    kept = narrow_by_traits([B("xiao"), B("tracker")], {"psram": "none"}, p)
    assert [b.key for b in kept] == ["tracker"]


def test_unknown_models_always_survive(tmp_path):
    p = store(tmp_path)
    learn("xiao", {"psram": "8MB"}, p)
    kept = narrow_by_traits([B("xiao"), B("t3s3"), B("tdeck")],
                            {"psram": "none"}, p)
    # xiao contradicted and dropped; the two we know nothing about stay
    assert [b.key for b in kept] == ["t3s3", "tdeck"]


def test_nothing_measured_narrows_nothing(tmp_path):
    p = store(tmp_path)
    learn("xiao", {"psram": "8MB"}, p)
    boards = [B("xiao"), B("tracker")]
    assert narrow_by_traits(boards, {}, p) == boards
    assert narrow_by_traits(boards, {"psram": None}, p) == boards


def test_wiping_out_the_whole_list_is_refused(tmp_path):
    # our learning contradicting every candidate means the LEARNING is
    # wrong; the operator must still be able to pick the board in their hand
    p = store(tmp_path)
    learn("xiao", {"psram": "8MB"}, p)
    learn("tracker", {"psram": "8MB"}, p)
    boards = [B("xiao"), B("tracker")]
    assert narrow_by_traits(boards, {"psram": "none"}, p) == boards


def test_store_is_json_and_readable(tmp_path):
    p = store(tmp_path)
    learn("tracker", {"psram": "none"}, p)
    with open(p) as f:
        assert json.load(f)["tracker"]["psram"] == "none"


# ------------------------------------------------------------- the wiring

def test_detect_ladder_uses_traits_and_confirmation_teaches():
    import pathlib
    det = pathlib.Path("ui/board_detect.py").read_text()
    assert "from ui.board_traits import narrow_by_traits" in det
    assert "parse_psram(out)" in det and '"psram": psram' in det
    guide = pathlib.Path("ui/screens/birth_guide_screen.py").read_text()
    assert "from ui.board_traits import learn" in guide
    assert 'det.get("psram")' in guide
