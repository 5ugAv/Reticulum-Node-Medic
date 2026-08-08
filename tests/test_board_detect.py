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
