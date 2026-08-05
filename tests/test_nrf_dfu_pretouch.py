"""A RUNNING nRF52 must be walked into DFU before autoinstall, not during it.

Live 2026-08-05, the last row of the birth acceptance matrix — birthing a board
that is ALREADY a provisioned RNode::

    [birth] detect_port: ok — Board on /dev/ttyACM2.
    [birth] flash: FAIL — autoinstall did not complete:
            Could not find specified port /dev/ttyACM2, exiting now

Opening the port of a RUNNING RNode toggles DTR and resets it. The board
re-enumerates as its bootloader on a DIFFERENT tty, and rnodeconf — which was
handed one port and expects it to stay put — dies looking for the old one.

Re-acquiring the port BEFORE the flash could not fix this, and the earlier
attempt to do so is why this needed a second look: the port was still perfectly
valid when we checked. It vanishes underneath rnodeconf a moment later.

It also explains the pattern across the matrix. A board that arrived ALREADY in
DFU (row C) birthed fine, because nothing moved under the tool. A running one
could not. So: make the move ourselves, wait for it, follow the board, and hand
rnodeconf a port that will still be there when it looks.
"""
import workflows.rnode_flash as rf

RUN_ID = "usb-RAKwireless_WisBlock_RAK4631_4631000000000001-if00"
DFU_ID = "usb-RAKWireless_WisBlock_RAK4631_4631000000000001-if00"
SERIAL = "4631000000000001"


class _Conn:
    """Reports the board on a NEW tty once the touch command has been run."""

    def __init__(self):
        self.cmds = []
        self.touched = False

    def run(self, cmd, timeout=None):
        self.cmds.append(cmd)
        if "serial.Serial" in cmd:
            self.touched = True
            return (0, "", "")
        if self.touched:
            return (0, f"/dev/serial/by-id/{DFU_ID} /dev/ttyACM1", "")
        return (0, f"/dev/serial/by-id/{RUN_ID} /dev/ttyACM2", "")


def test_the_touch_is_the_arduino_convention():
    """1200 baud, DTR dropped, closed — verified live as 239a:8029 -> 002a."""
    c = _Conn()
    rf.touch_into_dfu(c, "/dev/ttyACM2", SERIAL, sleep=lambda _s: None)
    touch = [x for x in c.cmds if "serial.Serial" in x]
    assert touch, "no touch was performed"
    assert "1200" in touch[0]
    assert "dtr=False" in touch[0].replace(" ", "")


def test_it_returns_the_boards_NEW_port():
    c = _Conn()
    got = rf.touch_into_dfu(c, "/dev/ttyACM2", SERIAL, sleep=lambda _s: None)
    assert got == "/dev/ttyACM1", "the board was not followed to its new tty"


def test_it_waits_before_looking():
    """The re-enumeration is not instant; looking too early finds nothing."""
    naps = []
    rf.touch_into_dfu(_Conn(), "/dev/ttyACM2", SERIAL, sleep=naps.append)
    assert naps and naps[0] > 0


def test_a_board_that_does_not_come_back_keeps_its_old_port():
    """Better to let rnodeconf try and report than to invent a port."""
    class Gone(_Conn):
        def run(self, cmd, timeout=None):
            self.cmds.append(cmd)
            return (0, "", "")
    got = rf.touch_into_dfu(Gone(), "/dev/ttyACM2", SERIAL,
                            sleep=lambda _s: None)
    assert got == "/dev/ttyACM2"


# -- knowing when NOT to touch ------------------------------------------------

def test_a_board_already_in_dfu_is_recognised(monkeypatch):
    """Touching a board that is already in its bootloader would reset it back
    OUT of DFU — the exact opposite of what is wanted."""
    assert rf.in_dfu_already(None, "/dev/ttyACM1",
                             vendor_fn=lambda _p: "239a",
                             pid_fn=lambda _p: "002a") is True


def test_a_running_board_is_not_mistaken_for_a_bootloader():
    assert rf.in_dfu_already(None, "/dev/ttyACM2",
                             vendor_fn=lambda _p: "239a",
                             pid_fn=lambda _p: "8029") is False


def test_an_esp32_is_never_treated_as_an_nrf_bootloader():
    assert rf.in_dfu_already(None, "/dev/ttyUSB0",
                             vendor_fn=lambda _p: "303a",
                             pid_fn=lambda _p: "002a") is False


def test_an_unreadable_port_is_not_assumed_to_be_in_dfu():
    """Fail towards doing the touch: a needless touch costs a reset, while
    skipping a needed one costs the whole flash."""
    def boom(_p):
        raise OSError("no sysfs")
    assert rf.in_dfu_already(None, "/dev/ttyACM9", vendor_fn=boom) is False
