"""Board auto-detection — chip parsing, firmware mapping, shortlist."""

from ui.board_detect import parse_chip, firmware_options, detect_board


class _Board:
    def __init__(self, key, platform):
        self.key = key
        self.platform = platform


BOARDS = [
    _Board("heltec_v4", "ESP32-S3"),
    _Board("xiao_s3", "ESP32-S3"),
    _Board("lilygo_v21", "ESP32"),
    _Board("tbeam", "ESP32"),
    _Board("rak4631", "nRF52"),
]

S3_OUT = "esptool.py v4.5\nDetecting chip type... ESP32-S3\nChip is ESP32-S3 (QFN56)"
ESP32_OUT = "Detecting chip type... ESP32\nChip is ESP32-D0WD-V3 (revision v3.1)"


def test_parse_chip_distinguishes_s3_from_plain_esp32():
    assert parse_chip(S3_OUT) == "esp32s3"       # not fooled by 'esp32' substring
    assert parse_chip(ESP32_OUT) == "esp32"
    assert parse_chip("Chip is ESP32-C3") == "esp32c3"
    assert parse_chip("no chip here") is None


def test_firmware_options_prefers_rtnode_on_s3():
    assert firmware_options("esp32s3")[0] == "rtnode2400"
    assert firmware_options("esp32s3") == ["rtnode2400", "rnode"]
    assert firmware_options("esp32") == ["rnode"]
    assert firmware_options(None) == ["rnode"]


def test_detect_no_board_connected():
    res = detect_board(BOARDS, ports_fn=lambda: [], reader=lambda p: "")
    assert res["found"] is False
    assert "plug the board in" in res["reason"].lower()


def test_detect_s3_shortlists_not_unique():
    res = detect_board(BOARDS, ports_fn=lambda: ["/dev/ttyACM1"],
                       reader=lambda p: S3_OUT)
    assert res["found"] is True
    assert res["chip"] == "esp32s3" and res["platform"] == "ESP32-S3"
    assert res["firmware"][0] == "rtnode2400"
    keys = {b.key for b in res["boards"]}
    assert keys == {"heltec_v4", "xiao_s3"}       # both S3 boards shortlisted
    assert res["board_key"] is None               # ambiguous -> no auto-pick


def test_detect_unique_platform_auto_picks():
    one_s3 = [_Board("heltec_v4", "ESP32-S3"), _Board("tbeam", "ESP32")]
    res = detect_board(one_s3, ports_fn=lambda: ["/dev/ttyACM1"],
                       reader=lambda p: S3_OUT)
    assert res["board_key"] == "heltec_v4"        # only one S3 -> auto-selected


def test_detect_unreadable_chip_gives_boot_hint():
    res = detect_board(BOARDS, ports_fn=lambda: ["/dev/ttyACM1"],
                       reader=lambda p: "connecting...___ failed")
    assert res["found"] is False
    assert "boot" in res["reason"].lower()


def test_detect_reader_exception_is_handled():
    def boom(p):
        raise OSError("port busy")
    res = detect_board(BOARDS, ports_fn=lambda: ["/dev/ttyACM1"], reader=boom)
    assert res["found"] is False and "port busy" in res["reason"]


def test_port_type_refines_s3_shortlist_to_the_v3():
    # An ESP32-S3 on ttyUSB can only be the CP2102-bridged V3 — auto-picked
    # (operator spec 2026-08-01: only show boards the medic can't distinguish).
    from ui.birth import rnode_board_choices
    r = detect_board(rnode_board_choices(),
                     ports_fn=lambda: ["/dev/ttyUSB0"],
                     reader=lambda p: "Chip is ESP32-S3")
    assert [b.key for b in r["boards"]] == ["heltec32_v3"]
    assert r["board_key"] == "heltec32_v3"


def test_port_type_keeps_native_s3_boards_ambiguous():
    # Native-CDC S3s (ttyACM) stay a genuine multi-candidate list, V3 excluded.
    from ui.birth import rnode_board_choices
    r = detect_board(rnode_board_choices(),
                     ports_fn=lambda: ["/dev/ttyACM1"],
                     reader=lambda p: "Chip is ESP32-S3")
    keys = [b.key for b in r["boards"]]
    assert "heltec32_v3" not in keys
    assert "heltec32_v4" in keys and len(keys) > 1
    assert r["board_key"] is None


# --- a board that keeps rebooting is not a board that isn't there ----------
#
# Live, 2026-08-09: a Heltec V4 with a bad bootloader header re-enumerated about
# every two seconds. The operator tapped "Flash this radio", detection took ONE
# snapshot, landed in a gap, and the medic said "No work board on the medic's
# USB — plug the board in with a known-good data cable". It was plugged in the
# whole time, with a good cable. The medic blamed the operator for its own
# sampling window.

def _flaky_ports(pattern):
    """ports_fn returning the given sequence of snapshots, one per call."""
    seq = list(pattern)
    def fn():
        return seq.pop(0) if seq else []
    return fn


def test_a_present_board_costs_no_delay():
    slept = []
    res = detect_board(BOARDS, ports_fn=lambda: ["/dev/ttyACM1"],
                       reader=lambda p: S3_OUT, sleep_fn=slept.append)
    assert res["found"] is True
    assert slept == [], "the normal case must not wait"
    assert not res.get("unstable")


def test_a_flapping_port_is_waited_for_and_named():
    slept = []
    res = detect_board(BOARDS,
                       ports_fn=_flaky_ports([[], [], ["/dev/ttyACM1"]]),
                       reader=lambda p: S3_OUT, sleep_fn=slept.append)
    assert res["found"] is True and res["port"] == "/dev/ttyACM1"
    assert slept == [1.0, 1.0]
    assert res.get("unstable") is True
    assert "rebooting in a loop" in res["unstable_reason"]


def test_a_flapping_board_that_cannot_be_read_still_says_why():
    # The chip read fails too — the board is resetting under esptool. The
    # operator must not be sent hunting for another cable.
    res = detect_board(BOARDS,
                       ports_fn=_flaky_ports([[], ["/dev/ttyACM1"]]),
                       reader=lambda p: "connecting...___ failed",
                       sleep_fn=lambda s: None)
    assert res["found"] is False
    assert res.get("unstable") is True


def test_a_genuinely_absent_board_still_says_so():
    slept = []
    res = detect_board(BOARDS, ports_fn=lambda: [],
                       reader=lambda p: S3_OUT, attempts=3, sleep_fn=slept.append)
    assert res["found"] is False
    assert "No work board" in res["reason"]
    assert len(slept) == 2, "it should look a few times before giving up"


def test_the_flapping_warning_reaches_the_birth_screen():
    """The reason is useless if the screen never renders it."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_build_chooser")
    assert "unstable" in src and "unstable_reason" in src


# --- self-naming ESP32 boards (the XIAO S3, 2026-08-20) -----------------------

def _named_det(product):
    from ui.board_detect import detect_board
    from workflows.rnode_boards import RNODE_BOARDS
    return detect_board(
        list(RNODE_BOARDS.values()),
        ports_fn=lambda: ["/dev/ttyACM1"],
        reader=lambda p: (_ for _ in ()).throw(AssertionError(
            "esptool must not run — the name already answered")),
        vendor_fn=lambda p: "Espressif Systems",
        product_fn=lambda p: product,
        sleep_fn=lambda s: None)


def test_stock_xiao_names_itself_and_skips_esptool():
    """Read live off the bench 2026-08-20: the stock board's CDC product
    string is "seeed-xiao-s3". A name outranks silicon — esptool sees the
    same chip on a V4 and a XIAO."""
    det = _named_det("seeed-xiao-s3")
    assert det["found"] and det["board_key"] == "xiao_esp32s3"


def test_birthed_xiao_is_generic_and_falls_through_to_the_ladder():
    """Hardware verdict from the first XIAO birth (2026-08-20): our image
    runs ARDUINO_USB_MODE=1, whose USB descriptors are burned into the chip —
    a USB_PRODUCT define is a no-op there, so a BIRTHED board presents the
    generic Espressif identity and must NOT be treated as named."""
    from ui.board_detect import esp32_self_named_key
    assert esp32_self_named_key("XIAO-S3 RTNode-2400") is None
    assert esp32_self_named_key("USB JTAG/serial debug unit") is None


def test_generic_jtag_identity_names_nothing():
    from ui.board_detect import esp32_self_named_key
    assert esp32_self_named_key("USB JTAG/serial debug unit") is None
    assert esp32_self_named_key("") is None


def test_named_xiao_skips_the_rtnode_chooser():
    """The whole point: one less question. identified_target on the named
    board resolves straight to the xiao target (confirm gate still stands)."""
    from ui.rtnode_choice import identified_target, target_options
    det = _named_det("seeed-xiao-s3")
    assert identified_target(det) == "xiao_esp32s3"
    assert target_options(det) == ["xiao_esp32s3"]


def test_stock_rnode_techo_is_inferred_from_the_generic_nordic_identity():
    """A stock-RNode T-Echo presents Nordic's generic "nRF52840 DK" (Mark's
    build uses pca10056 defaults) — and the RTNode option vanished for a
    board proven an hour earlier (live, 2026-08-20). Within the stocked
    catalogue only the T-Echo stock build is generic (RAK and T114 name
    themselves), so the only-candidate inference applies."""
    from ui.board_detect import nrf52_board_key
    assert nrf52_board_key("nRF52840 DK") == "techo"
    assert nrf52_board_key("WisCore RAK4631 Board") == "rak4631"
    # A board that NAMES itself T114 still resolves...
    assert nrf52_board_key("Mesh Node T114") == "heltec_t114"


def test_ht_n5262_is_ambiguous_and_must_not_name_a_board():
    """"HT-n5262" is Heltec's MODULE name, shared by the Mesh Node T114, Mesh
    Node T1, Mesh Solar and the MeshPocket. It used to map to heltec_t114,
    which was safe only while the T114 was the sole one of the four on this
    bench. A MeshPocket was converted here on 2026-09-01, so that inference
    would now offer T114 firmware for a MeshPocket - the wrong-board flash
    that left two of them boot-looping on Heltec's own forum.

    Returning None sends the operator to the nRF52 picker to CHOOSE, which is
    the honest answer to an ambiguous identity. Do not "fix" this by mapping
    it to a board."""
    from ui.board_detect import nrf52_board_key
    assert nrf52_board_key("HT-n5262") is None
    assert nrf52_board_key("Heltec_HT-n5262") is None
    assert nrf52_board_key("Heltec_AutoMation_HT-n5262") is None
