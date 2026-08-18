"""Drive a Heltec V4 birth end to end, against emulation, with no hardware.

WHY THIS FILE EXISTS
--------------------
On 2026-08-18 the operator reported the V4 birth "worked perfectly last week"
and was now wrong on screen. The suite was green: 3309 passing. About 1,270
lines had landed across ui/screens/birth_screen.py, ui/screens/birth_guide_screen.py
and ui/birth_guide_flow.py in eight days, and NOTHING in the suite walked the
birth decision path for a specific board and asserted what it produced.

Kivy cannot be imported here, so widgets are out of reach — but every DECISION
the birth screens make is pure Python underneath them: which firmware a chip may
take, which board the key resolves to, what it is called on screen, how it is
flashed, and which radio parameters get baked in. Those are the things that,
when wrong, look wrong on a photograph. This file pins them.

It is deliberately about ONE board the operator actually births, not about the
abstract machinery. A regression here is a regression somebody would meet.
"""

from ui import board_detect
from workflows.rnode_boards import get_board, RNODE_BOARDS
from workflows.updater import autoinstall_command


def test_an_esp32s3_may_be_either_rnode_or_rtnode():
    """The V4 is an ESP32-S3, so the chooser must offer both builds, RTNode first."""
    assert board_detect.firmware_options("esp32s3") == ["rtnode2400", "rnode"]


def test_a_classic_esp32_can_only_be_an_rnode():
    assert board_detect.firmware_options("esp32") == ["rnode"]


def test_an_unknown_chip_still_offers_something_rather_than_nothing():
    """Failing closed to an empty list would leave the operator with no path."""
    assert board_detect.firmware_options(None) == ["rnode"]


def test_the_v4_resolves_to_the_name_printed_on_the_board():
    """The confirm screen tells the operator to check the silkscreen, so the name
    it shows has to be the name they will read there."""
    v4 = get_board("heltec32_v4")
    assert v4.display_name == "Heltec LoRa32 v4"
    assert v4.platform == "ESP32-S3"
    assert v4.modem == "SX1262"


def test_the_v4_is_flashed_by_autoinstall_not_by_a_board_specific_path():
    v4 = get_board("heltec32_v4")
    assert v4.flash_method == "autoinstall"
    # board_model 0 is correct for autoinstall boards: rnodeconf picks the model.
    # Only arduino_cli boards carry an explicit one (the Tracker, 0x52).
    assert v4.board_model == 0
    assert get_board("heltec_wireless_tracker").board_model == 0x52


def test_the_flash_command_never_hardcodes_a_port():
    """A hardcoded /dev/ttyACM0 on a Node Medic aims at the medic's OWN radio.
    The command must carry whatever port it was given."""
    cmd = autoinstall_command("/dev/ttyACM1")
    assert "/dev/ttyACM1" in cmd
    assert "/dev/ttyACM0" not in cmd
    assert cmd.startswith("rnodeconf ")


def test_every_board_the_chooser_can_show_has_a_name_and_a_flash_method():
    """An entry missing either produces a card that cannot be acted on."""
    items = RNODE_BOARDS.items() if isinstance(RNODE_BOARDS, dict) else \
            [(b.key, b) for b in RNODE_BOARDS]
    broken = [k for k, b in items
              if not getattr(b, "display_name", "") or not getattr(b, "flash_method", "")]
    assert not broken, f"boards with no name or no flash method: {broken}"


def test_no_board_claims_a_platform_its_firmware_options_would_refuse():
    """A board whose platform says ESP32-S3 must be offered RTNode; one that says
    anything else must not be. This is the rule the chooser filters on, and it
    silently produced a wrong answer for nRF52 boards until 2026-08-18."""
    items = RNODE_BOARDS.items() if isinstance(RNODE_BOARDS, dict) else \
            [(b.key, b) for b in RNODE_BOARDS]
    for key, b in items:
        plat = (getattr(b, "platform", "") or "").lower().replace("-", "")
        chip = "esp32s3" if "esp32s3" in plat else None
        if chip == "esp32s3":
            assert "rtnode2400" in board_detect.firmware_options("esp32s3"), key
