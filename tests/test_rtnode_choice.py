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


def _det(board_key=None, keys=(), port="/dev/ttyUSB0"):
    return {"found": True, "port": port, "chip": "esp32s3",
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


@pytest.mark.parametrize("key", ["heltec_t114", "not_a_board", None, ""])
def test_boards_without_a_build_resolve_to_nothing(key):
    assert target_for_board_key(key) is None


def test_the_techo_gained_a_build_on_2026_08_19():
    """It was in the no-build list above until the noalloc image was proven
    booting and provisioned on the bench. If this fails, the target was removed
    — check techo-support before letting the chooser offer it again."""
    assert target_for_board_key("techo") == "techo"


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
    """Its whole purpose. The example board used to be the T-Echo — which then
    GAINED a build (2026-08-19), which is exactly why this gate must key on the
    build registry and never on a hardcoded list."""
    # The example board keeps graduating: first the T-Echo (2026-08-19),
    # then the RAK4631 (2026-08-20) gained builds — which is the point of
    # keying on the registry. The T114 is the current no-build nRF52.
    assert blocked_board(_det("heltec_t114", ["heltec_t114"])) == "Heltec Mesh Node T114"


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
    det = _det(None, ["heltec32_v4", "heltec_wireless_tracker", "tbeam_supreme"],
               port="/dev/ttyACM1")
    assert identified_target(det) is None
    # Still ASKED — the chooser may prune impossibilities by port type but
    # must never come back with a single pre-picked card here.
    assert len(target_options(det)) > 1


def test_nothing_detected_offers_every_card_rather_than_none():
    from ui.rtnode_choice import ALL_CARDS
    assert target_options(None) == list(ALL_CARDS)
    assert identified_target(None) is None


def test_a_board_with_no_heltec_target_never_auto_picks_a_heltec():
    """A RAK4631 must not be silently treated as a V3 or a V4."""
    det = _det("heltec_t114", ["heltec_t114"], port="/dev/ttyACM0")
    assert identified_target(det) is None
    assert "heltec_t114" not in target_options(det)
    assert len(target_options(det)) > 1


def test_an_identified_techo_skips_the_chooser_to_its_own_target():
    """The T-Echo names itself over USB ("T-Echo" in the product string), so
    when detection pins it, the operator is not asked a Heltec question about
    a board that is neither."""
    det = _det("techo", ["techo"])
    assert identified_target(det) == "techo"
    assert target_options(det) == ["techo"]
    assert blocked_board(det) is None


# --- port -> no question: the whole chain, the way the bench ran it -----------

def _detect(port):
    """A real detect_board run against a simulated ESP32-S3 on ``port``."""
    from ui.birth import rnode_board_choices
    from ui.board_detect import detect_board
    return detect_board(rnode_board_choices(),
                        ports_fn=lambda: [port],
                        reader=lambda p: "Chip is ESP32-S3",
                        use_memory=False)


def test_a_v3_on_a_bridge_is_never_asked_which_board_it_is():
    """The bench sequence: plug in a V3, the medic prints "Detected ESP32-S3 on
    /dev/ttyUSB0" — and used to ask which board it was anyway.

    detect_board has resolved this since 2026-08-01 (see
    test_port_type_refines_s3_shortlist_to_the_v3, which has passed the whole
    time). What was missing was anybody asking it. This is that link.
    """
    det = _detect("/dev/ttyUSB0")
    assert det["board_key"] == "heltec32_v3"
    assert identified_target(det) == "heltec_v3"
    assert target_options(det) == ["heltec_v3"]
    assert blocked_board(det) is None


def test_a_native_s3_is_still_asked():
    """Same chain, opposite answer — and it must stay that way while a native
    port cannot separate a V4 from a Tracker or a Supreme."""
    det = _detect("/dev/ttyACM1")
    assert det["board_key"] is None
    assert identified_target(det) is None
    # A native ttyACM can never be the bridged V3 — the port fact prunes it —
    # but V4-vs-XIAO is a question only the operator can answer.
    from ui.rtnode_choice import S3_NATIVE_CARDS
    assert target_options(det) == list(S3_NATIVE_CARDS)
    assert blocked_board(det) is None


def test_xiao_s3_target_exists_and_reuses_the_proven_esp32_pipeline():
    """The Seeed XIAO ESP32S3 (Wio-SX1262) target: pio mechanism, beacon
    verify, the 8MB boundary_local env — pinned so a rename in the firmware
    tree or a mechanism drift surfaces here, not on the bench."""
    from workflows.rtnode_build import RTNODE_TARGETS, target_for_board_key
    t = RTNODE_TARGETS["xiao_esp32s3"]
    assert t.mechanism == "pio"
    assert t.verify == "beacon"
    assert t.build_env == "seeed_xiao_esp32s3_sx1262_boundary_local"
    assert target_for_board_key("xiao_esp32s3") is t.key or \
        target_for_board_key("xiao_esp32s3") == "xiao_esp32s3"


def test_bridge_port_prunes_to_the_v3_alone():
    """A ttyUSB port is a CP2102 bridge — the only bridged S3 we stock is the
    V3, and native-USB boards (V4, XIAO S3) physically cannot present it."""
    assert target_options({"port": "/dev/ttyUSB0"}) == ["heltec_v3"]


def test_native_port_offers_v4_and_xiao_but_never_v3():
    from ui.rtnode_choice import S3_NATIVE_CARDS
    opts = target_options({"port": "/dev/ttyACM2"})
    assert opts == list(S3_NATIVE_CARDS)
    assert "heltec_v3" not in opts
