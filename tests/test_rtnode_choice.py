"""The two RTNode board decisions, pinned to what the bench proved.

WHY THIS FILE EXISTS
--------------------
On 2026-08-18 the operator walked a Heltec V3 through an RTNode-2400 birth. The
build SUCCEEDED, and then the medic popped "Not available for this board — this
board cannot currently be flashed as an RTNode-2400", with the board still
plugged in. Two screens earlier it had printed "Detected ESP32-S3 on
/dev/ttyUSB0" and asked which board it was, under the sentence "V3 and V4 look
identical to Node Medic".

Both failures were about names, not hardware:

* the gate compared a birth certificate's ``board`` (beacon vocabulary,
  "Heltec32 V3" — monitor/health_beacon.py) against the literal strings
  "Heltec LoRa32 v3"/"v4". Those can never be equal, so the certificate a build
  had just written was the evidence that the board could not be built; and
* the chooser hardcoded both Heltec cards and ignored the fact that the V3 is
  the only ESP32-S3 in the catalogue behind a USB bridge.

The suite was green through both. These tests are the ones that would not have
been.
"""

import pytest

from ui.board_detect import _USB_KIND
from ui.rtnode_choice import (HELTEC_PAIR, blocked_board, identified_target,
                              target_options)
from workflows.rnode_boards import available_boards
from workflows.rtnode_build import RTNODE_TARGETS, target_for_board_key


class _Board:
    """Stands in for an RNodeBoard — target_options only ever reads .key."""
    def __init__(self, key):
        self.key = key


def _det(board_key=None, keys=()):
    return {"found": True, "port": "/dev/ttyUSB0", "chip": "esp32s3",
            "board_key": board_key, "boards": [_Board(k) for k in keys]}


# --- the hardware fact the whole thing rests on -------------------------------

def test_the_v3_is_the_only_esp32s3_behind_a_usb_bridge():
    """This is WHY the chooser can be skipped. If another bridged S3 is ever
    stocked, this fails and the skip must be re-thought before it ships."""
    bridged = [b.key for b in available_boards()
               if str(getattr(b, "platform", "")) == "ESP32-S3"
               and _USB_KIND.get(b.key) == "bridge"]
    assert bridged == ["heltec32_v3"]


# --- the key vocabularies must cross, and only here ---------------------------

@pytest.mark.parametrize("detect_key,expected", [
    ("heltec32_v3", "heltec_v3"),
    ("heltec32_v4", "heltec_v4"),
    ("heltec_v3", "heltec_v3"),          # already a target key
    ("tbeam_supreme", "tbeam_supreme"),
])
def test_detector_keys_resolve_to_build_targets(detect_key, expected):
    assert target_for_board_key(detect_key) == expected


@pytest.mark.parametrize("key", ["techo", "rak4631", "not_a_board", None, ""])
def test_boards_without_a_build_resolve_to_nothing(key):
    assert target_for_board_key(key) is None


def test_every_heltec_pair_member_is_a_real_build_target():
    """The cards must not offer a target the builder cannot build."""
    for key in HELTEC_PAIR:
        assert key in RTNODE_TARGETS


# --- THE REGRESSION -----------------------------------------------------------

def test_a_freshly_built_v3_is_not_told_it_cannot_be_built():
    """The bench failure, exactly: board still plugged in after a successful
    RTNode-2400 build. Nothing about it has changed, so nothing may block it."""
    assert blocked_board(_det("heltec32_v3", ["heltec32_v3"])) is None


def test_a_freshly_built_v4_is_not_told_it_cannot_be_built():
    assert blocked_board(_det("heltec32_v4", ["heltec32_v4"])) is None


def test_the_gate_never_compares_boards_by_display_name():
    """The defect was a comparison between two naming systems. Any board the
    builder HAS a target for must pass, whatever anyone calls it on screen."""
    for key in RTNODE_TARGETS:
        assert blocked_board(_det(key, [key])) is None


# --- the gate still does its job ---------------------------------------------

def test_a_board_with_no_rtnode_build_is_still_blocked():
    """Its whole purpose: a T-Echo would flash and land on the wrong pins."""
    assert blocked_board(_det("techo", ["techo"])) == "LilyGO T-Echo"


def test_an_unidentified_board_is_never_blocked():
    """Fail OPEN. 'Don't know' is not 'cannot' — the chooser and its
    confirmation gate stand behind this, and they are the real brick guard."""
    assert blocked_board(_det(None, ["heltec32_v4", "heltec_wireless_tracker"])) is None
    assert blocked_board(None) is None
    assert blocked_board({}) is None


# --- when the operator is asked, and when they are not ------------------------

def test_a_bridged_v3_is_identified_and_the_question_is_not_asked():
    det = _det("heltec32_v3", ["heltec32_v3"])
    assert identified_target(det) == "heltec_v3"
    assert target_options(det) == ["heltec_v3"]


def test_a_native_s3_is_still_asked_because_it_could_be_a_tracker():
    """A native ESP32-S3 is a V4 or a Wireless Tracker or a T-Beam Supreme.
    Pre-picking the V4 is how a Tracker walked into these cards (2026-08-01)."""
    det = _det(None, ["heltec32_v4", "heltec_wireless_tracker", "tbeam_supreme"])
    assert identified_target(det) is None
    assert target_options(det) == list(HELTEC_PAIR)


def test_nothing_detected_offers_both_boards_rather_than_none():
    assert target_options(None) == list(HELTEC_PAIR)
    assert identified_target(None) is None


def test_a_board_with_no_heltec_target_never_auto_picks_a_heltec():
    """A T-Echo must not be silently treated as a V3 or a V4."""
    det = _det("techo", ["techo"])
    assert identified_target(det) is None
    assert target_options(det) == list(HELTEC_PAIR)
