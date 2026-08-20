"""nRF52 boards are identified WITHOUT esptool (operator, 2026-08-05).

A RAK4631 was plugged into the medic and the board picker offered nothing at
all. Detection ran esptool unconditionally; esptool cannot read an nRF52840, so
it timed out, and with no chip there were no candidates. The failure then told
the operator to "hold BOOT, tap RST" — a procedure this board family does not
have. It double-taps RESET into a UF2 bootloader.

Everything else was already in place: vendor 239a was classified as native USB,
all three nRF52 boards were in the catalogue with art and power profiles, and
the catalogue even carried the correct double-tap guidance. Only the identify
step assumed every board is an ESP32.
"""
import pytest

from ui import board_detect as bd
from workflows.rnode_boards import RNODE_BOARDS


def _boards():
    return (list(RNODE_BOARDS.values()) if isinstance(RNODE_BOARDS, dict)
            else list(RNODE_BOARDS))


def _detect(vendor, product):
    """Detect with a reader that EXPLODES — esptool must never be reached."""
    def never(_port):
        raise AssertionError("esptool was run against an nRF52 board")
    return bd.detect_board(_boards(),
                           ports_fn=lambda: ["/dev/ttyACM1"],
                           reader=never,
                           vendor_fn=lambda _p: vendor,
                           product_fn=lambda _p: product)


def test_the_rak4631_is_identified_by_its_usb_product_string():
    """The exact string the medic reads off the real board. It names the model
    outright, which is better evidence than esptool gives for any board."""
    r = _detect("239a", "WisCore RAK4631 Board")
    assert r["found"] is True
    assert r["chip"] == "nrf52840"
    assert r["platform"] == "nRF52"
    assert r["board_key"] == "rak4631"
    assert [b.key for b in r["boards"]] == ["rak4631"]


def test_an_unknown_nrf52_offers_every_nrf52_board_rather_than_none():
    """THE bug: an empty picker. Offering all three costs the operator one tap;
    guessing one costs a flash onto the wrong board."""
    r = _detect("239a", "Some Unlabelled nRF52 Widget")
    keys = [b.key for b in r["boards"]]
    assert r["found"] is True
    assert r["board_key"] is None, "must not guess when the product is unknown"
    assert set(keys) == {"rak4631", "techo", "heltec_t114"}


def test_nordics_own_vendor_id_counts_too():
    r = _detect("1915", "RAK4631")
    assert r["found"] is True and r["board_key"] == "rak4631"


def test_nrf52_boards_are_offered_per_the_build_registry():
    """This test's original docstring said "RTNode-2400 needs an ESP32-S3. An
    nRF52 can only ever be an RNode" — both halves are now FALSE: the T-Echo
    (2026-08-19) and the RAK4631 (2026-08-20) run RTNode-2400. The offer comes
    from the build registry per identified board; the T114 is the remaining
    nRF52 without a build and holds the RNode-only line."""
    assert _detect("239a", "WisCore RAK4631 Board")["firmware"] == [
        "rtnode2400", "rnode"]


def test_an_esp32_still_goes_through_esptool():
    """The nRF52 branch must not swallow ESP32 boards — they are identified by
    reading the chip, and that path stays exactly as it was."""
    r = bd.detect_board(_boards(),
                        ports_fn=lambda: ["/dev/ttyUSB0"],
                        reader=lambda _p: "Chip is ESP32-S3 (QFN56)",
                        vendor_fn=lambda _p: "10c4",
                        product_fn=lambda _p: "CP2102 USB to UART Bridge")
    assert r["found"] is True
    assert r["chip"] == "esp32s3"
    assert "rtnode2400" in r["firmware"]


@pytest.mark.parametrize("product,expected", [
    ("WisCore RAK4631 Board", "rak4631"),
    ("RAK4631", "rak4631"),
    ("LilyGO T-Echo", "techo"),
    ("Heltec Mesh Node T114", "heltec_t114"),
    ("", None),
    ("Arduino Nano 33 BLE", None),
])
def test_product_string_to_board_key(product, expected):
    assert bd.nrf52_board_key(product) == expected
