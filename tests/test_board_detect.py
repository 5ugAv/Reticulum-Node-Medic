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
