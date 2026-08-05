"""Rebirthing a board must retire its OLD certificate.

Live 2026-08-05. The operator rebirthed a RAK4631 that had been born as "rak2",
renamed it "rak3", and ended up with TWO certificates pointing at the SAME
physical board::

    rak2.json  usb_serial ...4631000000000001  born 23:25
    rak3.json  usb_serial ...4631000000000001  born 23:39

The wipe path already calls delete_by_usb_serial for exactly this reason, but it
compared the WHOLE /dev/serial/by-id basename with ==. That string is not stable
across a reset: the RAK4631 announces itself as ``RAKWireless`` from its
bootloader and ``RAKwireless`` once running firmware — one capital letter apart.

A certificate is written after verify, so it stores the FIRMWARE spelling. The
wipe that should retire it runs while the board is in DFU, so it looked up the
BOOTLOADER spelling. The compare never matched and the safety silently did
nothing — on every nRF52 board, every time.

Both outcomes of the missing prompt were bad, which is why this matters:
  - keep the old name  -> save_cert is "idempotent on the id", so the previous
                          certificate is OVERWRITTEN and its birth record lost
  - choose a new name  -> a stale duplicate survives, and the medic believes it
                          has two nodes where one board exists

Matching on the hardware serial fixes both: whatever the board is called next,
the record for that serial is retired first.
"""
import json
import os

from ui.cert_store import delete_by_usb_serial, save_cert

SERIAL = "4631000000000001"
FIRMWARE_ID = f"usb-RAKwireless_WisBlock_RAK4631_{SERIAL}-if00"
BOOTLOADER_ID = f"usb-RAKWireless_WisBlock_RAK4631_{SERIAL}-if00"
OTHER_ID = "usb-RAKwireless_WisBlock_RAK4631_4631000000000002-if00"


def _cert(name, usb):
    return {"_id": name, "node_name": name, "node_type": "rnode",
            "usb_serial": usb}


def test_a_cert_written_in_firmware_mode_is_retired_by_a_dfu_mode_wipe(tmp_path):
    """THE bug, in one line: the two spellings must resolve to one board."""
    d = str(tmp_path)
    save_cert(_cert("rak2", FIRMWARE_ID), cert_dir=d)
    assert delete_by_usb_serial(BOOTLOADER_ID, cert_dir=d) == 1
    assert not os.path.exists(os.path.join(d, "rak2.json"))


def test_it_still_works_when_both_spellings_agree(tmp_path):
    d = str(tmp_path)
    save_cert(_cert("rak2", FIRMWARE_ID), cert_dir=d)
    assert delete_by_usb_serial(FIRMWARE_ID, cert_dir=d) == 1


def test_a_DIFFERENT_board_is_never_retired(tmp_path):
    """The failure that would be far worse than the bug: deleting the
    certificate of a board that is not being rebirthed."""
    d = str(tmp_path)
    save_cert(_cert("rak1", OTHER_ID), cert_dir=d)
    save_cert(_cert("rak2", FIRMWARE_ID), cert_dir=d)
    assert delete_by_usb_serial(BOOTLOADER_ID, cert_dir=d) == 1
    assert os.path.exists(os.path.join(d, "rak1.json")), \
        "an unrelated board's certificate was destroyed"


def test_no_serial_retires_nothing(tmp_path):
    d = str(tmp_path)
    save_cert(_cert("rak2", FIRMWARE_ID), cert_dir=d)
    assert delete_by_usb_serial("", cert_dir=d) == 0
    assert delete_by_usb_serial(None, cert_dir=d) == 0
    assert os.path.exists(os.path.join(d, "rak2.json"))


def test_a_cert_with_no_fingerprint_is_left_alone(tmp_path):
    """Several older certificates have usb_serial: None. They cannot be matched
    to a board, and must not be swept up by a rebirth of some other one."""
    d = str(tmp_path)
    save_cert({"_id": "unity", "node_name": "unity"}, cert_dir=d)
    save_cert(_cert("rak2", FIRMWARE_ID), cert_dir=d)
    delete_by_usb_serial(BOOTLOADER_ID, cert_dir=d)
    assert os.path.exists(os.path.join(d, "unity.json"))


def test_the_esp32_style_serial_still_matches(tmp_path):
    """Jonesey-style by-id names carry a MAC with colons. Don't regress them."""
    d = str(tmp_path)
    esp = "usb-Espressif_USB_JTAG_serial_debug_unit_02:00:00:04:00:04-if00"
    save_cert(_cert("rnode-5a59", esp), cert_dir=d)
    assert delete_by_usb_serial(esp, cert_dir=d) == 1
