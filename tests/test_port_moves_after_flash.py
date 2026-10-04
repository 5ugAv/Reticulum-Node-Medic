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


# The regression the shared reader exists to prevent: a USB-UART BRIDGE board
# enumerates with a trailing ``-port0``, and the old regex anchored its serial
# match with ``$`` right after ``-if00`` — the ``-port0`` shoved the anchor off
# the end, the match failed, and the serial silently read back as nothing. That
# is the exact board family (Heltec V3, T-Beam) whose reflash re-enumeration the
# reader was written to follow, so the bug was both silent and worst-targeted.
CP2102_V3 = ("usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_"
             "0001-if00-port0")
FTDI = "usb-FTDI_FT232R_USB_UART_A50285BI-if00-port0"


def test_a_bridge_boards_port_suffix_does_not_defeat_the_read():
    assert by_id_serial(CP2102_V3) == "0001"          # Heltec V3's CP2102
    assert by_id_serial(FTDI) == "A50285BI"           # FTDI adapter


def test_nonsense_yields_no_serial_rather_than_a_guess():
    # Sentinel is "" (the shared reader's contract), never a guess.
    assert by_id_serial("") == ""
    assert by_id_serial(None) == ""
    assert by_id_serial("/dev/ttyACM0") == ""


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


# -- the port can move BEFORE the flash too ----------------------------------

def test_an_already_birthed_board_that_moved_is_re_found_before_the_write():
    """Lap 2 of the acceptance matrix, live 2026-08-05:

        [birth] detect_port: ok - Board on /dev/ttyACM2.
        [birth] flash: FAIL - autoinstall did not complete:
                Could not find specified port /dev/ttyACM2, exiting now

    An already-provisioned RNode re-enumerates whenever something opens its
    port, so the tty found by detect_port was stale by the time autoinstall ran.
    Re-acquiring only AFTER the flash was not enough.
    """
    class C:
        def __init__(self):
            self.checked = []

        def run(self, cmd, timeout=None):
            if cmd.startswith("test -e"):
                self.checked.append(cmd)
                return (1, "", "")                    # ttyACM2 is gone
            return (0, f"/dev/serial/by-id/{RUN} /dev/ttyACM1", "")

    wf = _WF(C(), "/dev/ttyACM2", "4631000000000002")
    assert wf._reacquire_port() == "/dev/ttyACM1"


def test_detect_port_records_the_fingerprint_so_later_steps_can_re_find_it():
    """The serial must be captured when the board is FIRST seen — capturing it
    inside _flash left nothing to search with if the board moved before that."""
    import workflows.rnode_flash as rf

    class C:
        def run(self, cmd, timeout=None):
            return (0, RUN, "")

    from workflows.rnode_boards import get_board
    wf = rf.RNodeFlashWorkflow.__new__(rf.RNodeFlashWorkflow)
    wf.connection = C()
    wf.port = "/dev/ttyACM2"
    wf.board, wf.band_mhz = get_board("heltec32_v4"), 915
    res = rf.RNodeFlashWorkflow._detect_port(wf)
    assert res.success
    assert wf._usb_serial == "4631000000000002"
