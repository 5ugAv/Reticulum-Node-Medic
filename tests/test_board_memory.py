"""The medic must not ask the same question about the same board twice.

Six ESP32-S3 boards in the catalogue share a chip family AND a native-USB
connection, so esptool separates them not at all. The operator, live
2026-08-09, on the third pass over that six-way grid with one board plugged in:
"I'm just hoping that we can whittle down this section of boards in this
stage." The chip's MAC is burned in and unique, so their answer is good for the
life of the board.
"""
import os

from ui import board_memory
from ui.board_detect import (detect_board, narrow_by_flash_size, parse_flash_size,
                             parse_mac)


# Real esptool 3.3.3 output in SHAPE, captured from a Heltec V4 on the bench
# 2026-08-09. The MAC is a stand-in: the real one is a chip identifier, and a
# chip identifies a board while a board tends to identify a place (see
# ui/board_memory.py, which keeps them on the medic). Nothing here needs the
# true value — these tests check parsing and lookup.
V4_OUT = """esptool.py v3.3.3
Serial port /dev/ttyACM1
Connecting....
Detecting chip type... ESP32-S3
Chip is ESP32-S3 (revision v0.2)
Features: WiFi, BLE
Crystal is 40MHz
MAC: aa:bb:cc:dd:ee:01
Uploading stub...
Running stub...
Stub running...
Manufacturer: 68
Device: 4018
Detected flash size: 16MB
Hard resetting via RTS pin...
"""


def test_it_reads_the_mac_and_flash_size_off_a_real_board():
    assert parse_mac(V4_OUT) == "aa:bb:cc:dd:ee:01"
    assert parse_flash_size(V4_OUT) == "16MB"


def test_the_datasheet_is_not_the_board():
    """The V4's own datasheet says ESP32-S3FN8 — 8MB. This one measured 16MB.
    Sizes come off the bench or they don't come at all."""
    from ui.board_detect import _FLASH_SIZE
    assert _FLASH_SIZE["heltec32_v4"] == ("16MB",)


class _B:
    def __init__(self, key):
        self.key = key


def test_an_unmeasured_board_is_never_ruled_out():
    """We don't know its flash size, so we cannot say it isn't this one."""
    out = narrow_by_flash_size([_B("heltec32_v4"), _B("tdeck")], "16MB")
    assert {b.key for b in out} == {"heltec32_v4", "tdeck"}


def test_a_measured_match_leads():
    out = narrow_by_flash_size([_B("tdeck"), _B("heltec32_v4")], "16MB")
    assert out[0].key == "heltec32_v4"


def test_an_unknown_size_changes_nothing():
    boards = [_B("tdeck"), _B("heltec32_v4")]
    assert narrow_by_flash_size(boards, None) is boards


# --- remembering ----------------------------------------------------------

def _path(tmp_path):
    return str(tmp_path / "board_memory.json")


def test_what_the_operator_says_once_is_remembered(tmp_path):
    p = _path(tmp_path)
    assert board_memory.recall("aa:bb:cc:dd:ee:01", p) is None
    board_memory.remember("aa:bb:cc:dd:ee:01", "heltec32_v4", p)
    assert board_memory.recall("aa:bb:cc:dd:ee:01", p) == "heltec32_v4"


def test_the_mac_format_does_not_matter(tmp_path):
    p = _path(tmp_path)
    board_memory.remember("AA:BB:CC:DD:EE:01", "heltec32_v4", p)
    assert board_memory.recall("aabbccddee01", p) == "heltec32_v4"


def test_a_correction_overwrites(tmp_path):
    p = _path(tmp_path)
    board_memory.remember("aa:bb", "tdeck", p)
    board_memory.remember("aa:bb", "heltec32_v4", p)
    assert board_memory.recall("aa:bb", p) == "heltec32_v4"


def test_forgetting_really_forgets(tmp_path):
    p = _path(tmp_path)
    board_memory.remember("aa:bb", "tdeck", p)
    board_memory.forget("aa:bb", p)
    assert board_memory.recall("aa:bb", p) is None


def test_a_corrupt_file_costs_a_question_not_a_birth(tmp_path):
    p = _path(tmp_path)
    with open(p, "w") as fh:
        fh.write("{not json")
    assert board_memory.recall("aa:bb", p) is None
    assert board_memory.remember("aa:bb", "tdeck", p) is True


def test_bench_notes_are_not_world_readable(tmp_path):
    """A chip MAC identifies a board, a board tends to identify a place."""
    p = _path(tmp_path)
    board_memory.remember("aa:bb", "tdeck", p)
    assert oct(os.stat(p).st_mode & 0o077) == "0o0"


# --- and the whole point: detection stops asking ---------------------------

def test_detection_leads_with_the_remembered_board(tmp_path, monkeypatch):
    monkeypatch.setattr(board_memory, "MEMORY_PATH", _path(tmp_path))
    from ui.birth import rnode_board_choices
    boards = rnode_board_choices()

    first = detect_board(boards, ports_fn=lambda: ["/dev/ttyACM1"],
                         reader=lambda p: V4_OUT)
    assert len(first["boards"]) > 1, "nothing known yet — it has to ask"
    assert first["mac"] == "aa:bb:cc:dd:ee:01"

    board_memory.remember(first["mac"], "heltec32_v4",
                          board_memory.MEMORY_PATH)

    again = detect_board(boards, ports_fn=lambda: ["/dev/ttyACM1"],
                         reader=lambda p: V4_OUT)
    assert again["board_key"] == "heltec32_v4"
    assert [b.key for b in again["boards"]] == ["heltec32_v4"]
    assert again["remembered"] is True


def test_a_memory_of_a_board_that_cannot_be_this_chip_is_ignored(tmp_path, monkeypatch):
    """Remembering an nRF52 board against an ESP32-S3 MAC must not override the
    silicon — the operator can be wrong, or the file can be stale."""
    monkeypatch.setattr(board_memory, "MEMORY_PATH", _path(tmp_path))
    board_memory.remember("aa:bb:cc:dd:ee:01", "rak4631",
                          board_memory.MEMORY_PATH)
    from ui.birth import rnode_board_choices
    res = detect_board(rnode_board_choices(), ports_fn=lambda: ["/dev/ttyACM1"],
                       reader=lambda p: V4_OUT)
    assert res["board_key"] != "rak4631"
    assert not res.get("remembered")


# --- and it must not become a trap ----------------------------------------

def test_the_escape_from_a_remembered_board_shows_the_full_list():
    """A remembered board leaves ONE candidate, and a one-candidate list
    auto-advances. Without force_ask, "Not right — change" bounces straight
    back to the same confirmation."""
    from tests.srcutil import func_source
    S = "ui/screens/birth_guide_screen.py"
    src = func_source(S, "_change_hardware")
    assert "force_ask=True" in src
    assert "_detected = None" in src, "re-read rather than trust the snapshot"
    pick = func_source(S, "_render_pick_board")
    assert "ignore_memory=force_ask" in pick, \
        "forcing the ask must also bypass the remembered shortcut"


def test_walking_backwards_never_unlearns_a_board():
    """THE 2026-08-09 regression, and the rule it produced. _change_hardware is
    a BACK action — reachable by the left-edge swipe as well as the button —
    and it used to erase the memory of the chip. One stray back gesture and the
    medic had unlearned a board it had been told about correctly, so the
    six-way grid returned. Navigation must not destroy learned state; the next
    pick overwrites it, which is the correction."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_guide_screen.py", "_change_hardware")
    assert "forget" not in src.split('"""')[2], \
        "a back gesture must not erase what the operator taught the medic"


def test_ignoring_the_memory_is_a_read_switch_not_an_erase():
    import types
    from ui import board_memory
    from ui.board_detect import detect_board
    from ui.birth import rnode_board_choices

    import tempfile
    import os
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    board_memory.remember("aa:bb:cc:dd:ee:01", "heltec32_v4", path)
    real = board_memory.MEMORY_PATH
    board_memory.MEMORY_PATH = path
    try:
        wide = detect_board(rnode_board_choices(),
                            ports_fn=lambda: ["/dev/ttyACM1"],
                            reader=lambda p: V4_OUT, use_memory=False)
        assert len(wide["boards"]) > 1, "asked to ignore memory -> show them all"
        assert not wide.get("remembered")
        # and it is STILL remembered afterwards
        assert board_memory.recall("aa:bb:cc:dd:ee:01", path) == "heltec32_v4"
    finally:
        board_memory.MEMORY_PATH = real


def test_picking_a_board_records_it():
    from tests.srcutil import func_source
    S = "ui/screens/birth_guide_screen.py"
    assert "_remember_board" in func_source(S, "_board_picked")
    assert "remember" in func_source(S, "_remember_board")


def test_a_chip_mac_never_leaves_the_medic():
    """It is a bench note. Announced or written into a certificate it would tie
    a node to a workshop — see the anonymity rule."""
    import subprocess
    hits = subprocess.run(
        ["grep", "-rn", "board_memory", "--include=*.py",
         "ui/", "monitor/", "workflows/", "transport/", "provisioning/"],
        capture_output=True, text=True).stdout.splitlines()
    allowed = ("ui/board_memory.py", "ui/board_detect.py",
               "ui/screens/birth_guide_screen.py")
    for line in hits:
        assert line.startswith(allowed), f"board memory reached {line}"
