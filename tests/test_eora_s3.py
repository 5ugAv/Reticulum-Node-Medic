"""The Ebyte EoRa-S3-900TB: ESP32-S3 + E22-900MM22S (a raw SX1262).

Ported 2026-09-08, BEFORE the board was ever plugged in, so everything here is
pinned against sources rather than a bench reading. What made that safe was
that three sources disagreed and two of them agreed with each other:

  * Meshtastic variant CDEBYTE_EoRa-S3 - names this exact hardware chain in
    its own comment (EoRa-S3-900TB <- E22-900MM22S <- SX1262)
  * Tech500/EoRa-PI-Foundation - a working sketch from someone who owns one
  * Ebyte's own product page - disagreed on DIO1/BUSY/LED and also called it a
    433 MHz board, so it was discarded as misread

The first two agree on every pin. These tests hold that agreement in place.
"""

from ui.board_detect import _USB_KIND, parse_flash_size, parse_psram
from workflows.rnode_boards import RNODE_BOARDS, custom_boards
from workflows import power_compat


def test_the_board_is_in_the_catalogue_and_buildable_here():
    b = RNODE_BOARDS["eora_s3"]
    assert b.platform == "ESP32-S3" and b.modem == "SX1262"
    assert b.flash_method == "arduino_cli"      # built here, not from upstream
    assert b.board_model == 0x47
    assert b in custom_boards(), "must appear in a picker, not fall between them"


def test_cdc_on_boot_is_pinned_in_the_fqbn():
    """THE defect that would ship a silent board.

    The generic esp32s3 FQBN leaves CDCOnBoot disabled, which maps the
    firmware's Serial to UART0 on GPIO 43/44. The board then flashes perfectly
    and says nothing at all over USB, so rnodeconf can never reach it. The
    proven XIAO S3 carries the same flag for the same reason.
    """
    fqbn = RNODE_BOARDS["eora_s3"].fqbn
    assert "CDCOnBoot=cdc" in fqbn, "without this the flashed board is mute on USB"
    assert "FlashSize=4M" in fqbn, "ESP32-S3FH4R2 is 4 MB; a wrong size boot-loops it"


def test_it_is_a_native_usb_board_not_a_bridged_one():
    """Keeps it out of ttyUSB shortlists, where only the CP2102-bridged
    Heltec V3 can legitimately appear. Evidence: the Meshtastic board JSON
    declares hwid 303a:1001 (Espressif native USB), and the board carries no
    bridge chip."""
    assert _USB_KIND["eora_s3"] == "native"


def test_the_radio_can_be_powered_by_something():
    """Every pickable board needs a power model, or the Pi-pairing check
    silently has nothing to say about it."""
    e = power_compat.BOARD_POWER["eora_s3"]
    assert e["peak_ma"] > 0
    assert e["src"] == "estimate", (
        "still an estimate - replace with a measured figure once one of these "
        "has been put on a meter")


def test_measured_traits_would_separate_it_from_the_other_s3_boards():
    """The identification story, as an executable claim.

    Unflashed, this board is indistinguishable from every other ESP32-S3 over
    USB - they all enumerate as 303a:1001. What separates it is what esptool
    MEASURES: the ESP32-S3FH4R2 carries 4 MB of in-package flash and 2 MB of
    PSRAM, where the XIAO reads 8 MB with no PSRAM and the Heltec V4 reads
    16 MB.

    This asserts the PARSING, not the board: the figures are a prediction
    until a real one is read, and ui.board_traits learns the truth on first
    birth rather than trusting anything written here.
    """
    esptool_out = (
        "Chip is ESP32-S3 (QFN56) (revision v0.2)\n"
        "Features: WiFi, BLE, Embedded PSRAM 2MB (AP_3v3)\n"
        "Detected flash size: 4MB\n")
    assert parse_flash_size(esptool_out) == "4MB"
    assert parse_psram(esptool_out) == "2MB"


def test_the_provisioning_identity_does_not_collide():
    """product 0xD3 / model 0xCF, checked against every other board here."""
    me = RNODE_BOARDS["eora_s3"].provision
    assert me["product"] == "d3" and me["model"] == "cf"
    clashes = [b.key for b in RNODE_BOARDS.values()
               if b.key != "eora_s3" and b.provision
               and b.provision.get("product") == me["product"]]
    assert not clashes, f"product byte 0xD3 already used by {clashes}"
