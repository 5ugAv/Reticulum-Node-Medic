"""A board does not keep its port number across a flash.

Live, 2026-08-05, the first successful RAK4631 birth. The flash itself worked::

    [birth] flash: ok — Flashed RAK4631 from the offline cache

and then the very next step failed::

    [birth] set_params: FAIL — Refusing to write radio params: /dev/ttyACM1:
    can't resolve a USB serial — refusing to write it (fail-closed; the medic's
    own radio must never be a target).

The board had been flashed on ``ttyACM1`` while in DFU (239a:002a) and came back
running RNode firmware (239a:8029) on ``ttyACM2``. To the kernel those are two
different USB devices, so the tty was re-issued and ``ttyACM1`` simply stopped
existing.

The onboard guard was RIGHT to refuse. Writing blind to a tty that has vanished
is precisely how the wrong board gets flashed once something else claims that
number — the same family as the 2026-07-22 near-miss. The bug was upstream: the
workflow kept using a stale port instead of re-finding the board.

The serial is the only stable handle. Note the vendor string is NOT stable —
the bootloader says ``RAKWireless`` and the firmware says ``RAKwireless``, a
capital W becoming lowercase — so matching on the whole by-id name would fail.
"""
from workflows.rnode_flash import by_id_serial, find_port_by_usb_serial

DFU = "usb-RAKWireless_WisBlock_RAK4631_4631000000000002-if00"
RUN = "usb-RAKwireless_WisBlock_RAK4631_4631000000000002-if00"
JONESEY = "usb-Espressif_USB_JTAG_serial_debug_unit_A1:B2:C3:D4:E5:F6-if00"


def test_the_serial_survives_the_flash_even_though_the_name_does_not():
    assert by_id_serial(DFU) == "4631000000000002"
    assert by_id_serial(RUN) == "4631000000000002"
    assert DFU != RUN, "the vendor string really does change case"


def test_a_mac_style_serial_is_read_whole():
    """Jonesey's serial contains colons; the tail must not be truncated."""
    assert by_id_serial(JONESEY) == "A1:B2:C3:D4:E5:F6"


def test_nonsense_yields_no_serial_rather_than_a_guess():
    assert by_id_serial("") is None
    assert by_id_serial(None) is None
    assert by_id_serial("/dev/ttyACM0") is None


class _Conn:
    """Serves a by-id listing that changes after N polls, like a real re-enum."""

    def __init__(self, listings):
        self.listings = list(listings)
        self.polls = 0

    def run(self, cmd, timeout=None):
        out = self.listings[min(self.polls, len(self.listings) - 1)]
        self.polls += 1
        return (0, out, "")


def test_the_board_is_found_on_its_NEW_port():
    c = _Conn([f"/dev/serial/by-id/{RUN} /dev/ttyACM2"])
    assert find_port_by_usb_serial(c, "4631000000000002") == "/dev/ttyACM2"


def test_it_waits_for_udev_rather_than_giving_up_on_the_first_look():
    """The gap between the write finishing and the symlink appearing is not
    fixed, so a single glance would flake."""
    c = _Conn(["", "", f"/dev/serial/by-id/{RUN} /dev/ttyACM2"])
    naps = []
    got = find_port_by_usb_serial(c, "4631000000000002", tries=5, delay=0.01,
                                  sleep=naps.append)
    assert got == "/dev/ttyACM2"
    assert naps, "it must actually wait between polls"


def test_it_never_returns_a_DIFFERENT_board():
    """The failure that matters: grabbing whatever is on the bus. Jonesey is
    present and must never be mistaken for the board we just flashed."""
    c = _Conn([f"/dev/serial/by-id/{JONESEY} /dev/ttyACM0"])
    assert find_port_by_usb_serial(c, "4631000000000002", tries=2,
                                   delay=0.01, sleep=lambda _s: None) is None


def test_no_serial_means_no_search():
    """Without a fingerprint there is nothing to match, and picking a port by
    position is the original sin."""
    c = _Conn(["boom"])
    assert find_port_by_usb_serial(c, None) is None
    assert c.polls == 0, "it should not even look"


# -- the workflow uses it -----------------------------------------------------

class _WF:
    """Just enough RNodeFlashWorkflow to exercise _reacquire_port."""

    def __init__(self, conn, port, serial):
        from workflows.rnode_flash import RNodeFlashWorkflow
        self.connection = conn
        self.port = port
        self._usb_serial = serial
        self._reacquire_port = RNodeFlashWorkflow._reacquire_port.__get__(self)


def test_a_board_that_stayed_put_is_left_alone():
    class C:
        def run(self, cmd, timeout=None):
            return (0, "", "") if cmd.startswith("test -e") else (0, "", "")
    wf = _WF(C(), "/dev/ttyACM2", "4631000000000002")
    assert wf._reacquire_port() == "/dev/ttyACM2"


def test_a_vanished_port_is_re_pointed_at_the_same_board():
    class C:
        def run(self, cmd, timeout=None):
            if cmd.startswith("test -e"):
                return (1, "", "")                      # ttyACM1 is gone
            return (0, f"/dev/serial/by-id/{RUN} /dev/ttyACM2", "")
    wf = _WF(C(), "/dev/ttyACM1", "4631000000000002")
    assert wf._reacquire_port() == "/dev/ttyACM2"
    assert wf.port == "/dev/ttyACM2"
