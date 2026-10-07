"""Rebirthing a board must retire its OLD certificate.

Live 2026-08-05. The operator rebirthed a RAK4631 that had been born as "rak2",
renamed it "rak3", and ended up with TWO certificates pointing at the SAME
physical board::

    rak2.json  usb_serial ...<same serial>  born 23:25
    rak3.json  usb_serial ...<same serial>  born 23:39

(The hardware serial itself stays on the medic; the tests use a stand-in.)

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
    esp = "usb-Espressif_USB_JTAG_serial_debug_unit_A1:B2:C3:D4:E5:F6-if00"
    save_cert(_cert("rnode-5a59", esp), cert_dir=d)
    assert delete_by_usb_serial(esp, cert_dir=d) == 1


# -- a repair-rebirth keeps its name AND stays kin ----------------------------

def test_a_board_is_recognised_whichever_mode_it_is_plugged_in(tmp_path):
    """Operator, 2026-08-05: "someone's rebirthing a node ... because it's got
    issues, not because they need to change it ... the user should be able to
    use the same name on a board that was kin and reflashing it as the same
    name and still making it kin after that rebirth."

    That only holds if the board's identity survives the reflash. The cert is
    written in FIRMWARE mode; the board may next be seen in its BOOTLOADER. Both
    must resolve to the same board or it comes back a stranger and loses its
    name — the exact opposite of keeping fleet records consistent.
    """
    from ui.cert_store import cert_for_usb_serial
    d = str(tmp_path)
    save_cert(_cert("rak4", FIRMWARE_ID), cert_dir=d)
    found = cert_for_usb_serial(BOOTLOADER_ID, cert_dir=d)
    assert found is not None, "a rebirthed board came back unrecognised"
    assert found["node_name"] == "rak4", "it lost its name"


def test_the_same_name_may_be_reused_and_leaves_ONE_record(tmp_path):
    """Reusing the name is legitimate: the node is the same node, repaired. The
    end state must be one certificate, not a duplicate and not a hole."""
    d = str(tmp_path)
    save_cert(_cert("rak4", FIRMWARE_ID), cert_dir=d)
    delete_by_usb_serial(BOOTLOADER_ID, cert_dir=d)      # the wipe
    save_cert(_cert("rak4", FIRMWARE_ID), cert_dir=d)    # reborn, same name
    from ui.cert_store import load_certs
    same = [c for c in load_certs(d) if c.get("node_name") == "rak4"]
    assert len(same) == 1, f"expected one record, got {len(same)}"


def test_a_different_board_is_never_confused_for_it(tmp_path):
    from ui.cert_store import cert_for_usb_serial, same_board
    d = str(tmp_path)
    save_cert(_cert("rak1", OTHER_ID), cert_dir=d)
    assert cert_for_usb_serial(BOOTLOADER_ID, cert_dir=d) is None
    assert not same_board(OTHER_ID, FIRMWARE_ID)
    assert same_board(OTHER_ID, OTHER_ID)


def test_an_empty_fingerprint_matches_nothing(tmp_path):
    """Older certificates carry usb_serial: None. Two of those must not be
    treated as 'the same board' just because both are blank."""
    from ui.cert_store import same_board
    assert not same_board(None, None)
    assert not same_board("", "")


# A factory-default serial identifies NOTHING. Two DIFFERENT-model bridge boards
# both ship the CP2102/FTDI default "0001"; keying them on that serial would
# call them one board and hand back the wrong certificate. The shared reader can
# now read "0001" off boards it used to read blank, so this cross-model case is
# newly reachable and must be closed here.
_V3_CP2102 = ("usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_"
              "0001-if00-port0")
_OTHER_DEFAULT_0001 = "usb-1a86_USB_Single_Serial_0001-if00"


def test_a_factory_default_serial_does_not_merge_two_boards():
    from ui.cert_store import usb_serial_key, same_board
    # Two different-model boards, both carrying the default "0001", stay apart.
    assert usb_serial_key(_V3_CP2102) != usb_serial_key(_OTHER_DEFAULT_0001)
    assert not same_board(_V3_CP2102, _OTHER_DEFAULT_0001)
    # A board is still the same as itself.
    assert same_board(_V3_CP2102, _V3_CP2102)


def test_a_real_unique_serial_still_matches_itself_across_the_reflash():
    """The placeholder guard must not cost the RAK4631 its cross-spelling match:
    its 16-hex serial is unique, so bootloader and firmware spellings fold."""
    from ui.cert_store import same_board
    assert same_board(FIRMWARE_ID, BOOTLOADER_ID)
