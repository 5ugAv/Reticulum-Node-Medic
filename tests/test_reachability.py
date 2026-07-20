"""Birth bakes the RIGHT wired medic-link per board class: USB-gadget for Pi 4/5,
GPIO UART console for 3A+/Zero."""

from transport.connection import EmulatedConnection
from node_profile import NodeHardware
from provisioning.reachability import link_kind, bake_reachability


def test_link_kind_by_board_class():
    assert link_kind(NodeHardware.PI_5) == "gadget"
    assert link_kind(NodeHardware.PI_3A_PLUS) == "uart"
    assert link_kind(NodeHardware.PI_ZERO_2W) == "uart"


def _boot_conn():
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("cat /boot/firmware/config.txt", code=0, stdout="dtparam=audio=on\n")
    c.rule("cat /boot/firmware/cmdline.txt", code=0, stdout="console=tty1 rootwait\n")
    return c


def test_pi5_gets_usb_gadget():
    conn = _boot_conn()
    kind, ok, _msg = bake_reachability(conn, NodeHardware.PI_5)
    assert kind == "gadget" and ok
    assert any("dtoverlay=dwc2" in c for c in conn.history)       # gadget applied
    assert not any("enable_uart=1" in c for c in conn.history)    # NOT uart


def test_3aplus_gets_uart_console():
    conn = _boot_conn()
    kind, ok, _msg = bake_reachability(conn, NodeHardware.PI_3A_PLUS)
    assert kind == "uart" and ok
    assert any("enable_uart=1" in c for c in conn.history)        # uart applied
    assert any("serial-getty@ttyS0" in c for c in conn.history)
    assert not any("dtoverlay=dwc2" in c for c in conn.history)   # NOT gadget


def test_unknown_board_skips_with_a_clear_note():
    conn = _boot_conn()
    kind, ok, msg = bake_reachability(conn, NodeHardware.UNKNOWN) \
        if hasattr(NodeHardware, "UNKNOWN") else (None, True, "")
    if hasattr(NodeHardware, "UNKNOWN"):
        assert kind is None and ok
        assert "No wired-reachability profile" in msg
        # nothing written to boot files for an unrecognised board
        assert not any("tee /boot" in c for c in conn.history)
