"""A rebirth must wipe the board with a tool that board actually has.

Live, 2026-08-05, rebirthing a RAK4631 through the birth path::

    Rebirth failed
    Couldn't wipe the board: Could not connect to Espressif device: No serial
    data received. For troubleshooting steps visit:
    https://docs.espressif.com/projects/esptool/...

The wipe ran ``esptool erase_flash`` unconditionally. A RAK4631 is an nRF52840 —
there is no Espressif device on it to connect to, so the wipe could never have
worked, and the operator was sent to Espressif's troubleshooting page for a chip
that isn't on the board. The same wrong-tool-for-the-family mistake the flash
path had before it learned about nRF52.

For nRF52 the right move is NOT a chip erase. The Adafruit bootloader is the
only way back into the board, so erasing it would brick rather than reset. What
a rebirth needs is for the board to stop presenting as a provisioned RNode, and
``rnodeconf --eeprom-wipe`` does that; the next DFU flash overwrites the whole
firmware image anyway.
"""
import pytest

from workflows.rnode_flash import wipe_for_rebirth

ESPTOOL = "/home/x/.platformio/packages/tool-esptoolpy/esptool.py"


class _Conn:
    def __init__(self, code=0, out="", err=""):
        self.code, self.out, self.err = code, out, err
        self.cmds = []

    def run(self, cmd, timeout=None):
        self.cmds.append(cmd)
        return (self.code, self.out, self.err)


def _nrf(_port):
    return "239a"


def _esp(_port):
    return "303a"


def test_an_nrf52_board_is_never_handed_to_esptool():
    """THE bug. esptool on a RAK4631 cannot succeed."""
    c = _Conn()
    ok, msg = wipe_for_rebirth(c, "/dev/ttyACM2", esptool_path=ESPTOOL,
                               vendor_fn=_nrf)
    assert ok
    assert not any("esptool" in cmd for cmd in c.cmds), \
        "an nRF52 board was sent to the ESP32 flasher"
    assert any("--eeprom-wipe" in cmd for cmd in c.cmds)


def test_the_nrf52_wipe_does_not_touch_the_bootloader():
    """Erasing an nRF52's bootloader removes the only way back in. Whatever we
    run must be an EEPROM operation, not a chip erase."""
    c = _Conn()
    wipe_for_rebirth(c, "/dev/ttyACM2", esptool_path=ESPTOOL, vendor_fn=_nrf)
    joined = " ".join(c.cmds)
    assert "erase_flash" not in joined
    assert "erase" not in joined.replace("--eeprom-wipe", "")


def test_an_esp32_board_still_gets_a_full_chip_erase():
    """The ESP32 path is the one that already worked; don't regress it."""
    c = _Conn()
    ok, _ = wipe_for_rebirth(c, "/dev/ttyUSB0", esptool_path=ESPTOOL,
                             vendor_fn=_esp)
    assert ok
    assert any("erase_flash" in cmd and "esptool" in cmd for cmd in c.cmds)


def test_esp32_with_no_esptool_says_so_instead_of_running_nothing():
    c = _Conn()
    ok, msg = wipe_for_rebirth(c, "/dev/ttyUSB0", esptool_path="",
                               vendor_fn=_esp)
    assert not ok and "flash tool" in msg
    assert c.cmds == []


def test_nrf52_needs_no_esptool_at_all():
    """A medic that has never flashed an ESP32 must still be able to rebirth an
    nRF52 board."""
    c = _Conn()
    ok, _ = wipe_for_rebirth(c, "/dev/ttyACM2", esptool_path="", vendor_fn=_nrf)
    assert ok


def test_it_retries_through_the_port_lock_race():
    """The detect step's banner read can still hold the port for a beat."""
    class Flaky(_Conn):
        def run(self, cmd, timeout=None):
            self.cmds.append(cmd)
            if len(self.cmds) < 3:
                return (1, "", "could not exclusively lock port")
            return (0, "", "")
    c = Flaky()
    naps = []
    ok, _ = wipe_for_rebirth(c, "/dev/ttyACM2", vendor_fn=_nrf,
                             sleep=naps.append)
    assert ok and len(c.cmds) == 3 and naps


def test_a_real_failure_is_reported_not_swallowed():
    c = _Conn(code=1, err="board is on fire")
    ok, msg = wipe_for_rebirth(c, "/dev/ttyACM2", vendor_fn=_nrf, tries=2,
                               sleep=lambda _s: None)
    assert not ok and "on fire" in msg


def test_the_medics_own_radio_is_refused_before_any_wipe(monkeypatch):
    """A rebirth is a full erase — the last place the guard may be skipped."""
    import ui.onboard_roster as roster
    from ui.onboard_roster import ProtectedBoardError

    monkeypatch.setattr(roster, "guard_is_active", lambda *a, **k: True)
    monkeypatch.setattr(roster, "assert_flashable",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ProtectedBoardError("that is Jonesey")))
    c = _Conn()
    with pytest.raises(ProtectedBoardError):
        wipe_for_rebirth(c, "/dev/ttyACM0", vendor_fn=_nrf)
    assert c.cmds == [], "a command ran against a protected board"
