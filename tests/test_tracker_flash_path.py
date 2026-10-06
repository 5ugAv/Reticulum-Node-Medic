import pytest
"""The Tracker-as-RNode custom-fork path — full emulated dry-run.

Written before the first live Tracker lap (2026-08-01) to prove, without
touching hardware, that the pipeline: runs every step in order, sends the
esptool write to THE WORK PORT ONLY (never the medic's own radio), uses the
proven fork build tree, provisions with the Tracker's codes, and emits a
fingerprinted birth certificate."""

from transport.connection import EmulatedConnection
from workflows.rnode_boards import get_board
from workflows.rnode_flash import RNodeFlashWorkflow, TRACKER_BUILD_DIR

WORK_PORT = "/dev/ttyACM1"      # the plugged work Tracker
JONESEY_PORT = "/dev/ttyACM0"   # the medic's own radio — must never appear


def tracker_conn():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rules.insert(0, ("esptool", 0, "Hash of data verified.", ""))
    conn.rules.insert(0, ("rnodeconf " + WORK_PORT + " -r", 0,
                          "Bootstrapping successful", ""))
    conn.rules.insert(0, ("-H", 0, "Firmware hash set", ""))
    conn.rules.insert(0, ("--tnc", 0, "Radio parameters set", ""))
    conn.rules.insert(0, ("--info", 0,
                          "Device signature: Validated\nFirmware version 1.78",
                          ""))
    return conn


def wf(conn):
    return RNodeFlashWorkflow(conn, get_board("heltec_wireless_tracker"),
                              port=WORK_PORT,
                              work_ports_fn=lambda: [WORK_PORT])


def test_full_run_all_steps_green_and_ordered():
    conn = tracker_conn()
    results = wf(conn).run_all()
    assert [r.name for r in results] == [
        "detect_port", "ensure_single_board", "ensure_firmware", "flash",
        "set_params", "verify"]
    assert all(r.success for r in results), [
        (r.name, r.message) for r in results if not r.success]


def test_flash_targets_the_work_port_and_never_jonesey():
    conn = tracker_conn()
    wf(conn).run_all()
    cmds = [c for c, *_ in getattr(conn, "commands", [])] \
        if hasattr(conn, "commands") else [c[0] for c in conn.calls] \
        if hasattr(conn, "calls") else []
    if not cmds:                      # fall back to the emulator's history attr
        cmds = [str(c) for c in getattr(conn, "history", [])]
    assert cmds, "emulated connection exposes no command history"
    esptool_cmds = [c for c in cmds if "esptool" in c]
    assert esptool_cmds, "no esptool write was issued"
    for c in esptool_cmds:
        assert WORK_PORT in c
        assert JONESEY_PORT not in c
        assert TRACKER_BUILD_DIR in c            # the PROVEN fork build tree
        assert "--no-stub" in c and "115200" in c  # USB-JTAG constraints
    # Jonesey's port appears in NO command of the entire run
    assert not [c for c in cmds if JONESEY_PORT in c]


def test_flash_provisions_with_tracker_codes():
    conn = tracker_conn()
    results = wf(conn).run_all()
    cmds = [str(c) for c in (getattr(conn, "commands", None)
                             or getattr(conn, "calls", None)
                             or getattr(conn, "history", []))]
    prov = [c for c in cmds if " -r " in c or c.rstrip().endswith("-r")]
    assert any("--product cb" in c and "--model ca" in c for c in cmds), cmds
    flash = next(r for r in results if r.name == "flash")
    assert "provisioned" in flash.message.lower()


def test_certificate_carries_the_usb_fingerprint():
    conn = tracker_conn()
    conn.rules.insert(0, ("for l in /dev/serial/by-id", 0,
                          "usb-Espressif_USB_JTAG_serial_debug_unit_"
                          "02:00:00:02:00:06-if00", ""))
    w = wf(conn)
    w.run_all()
    cert = w.birth_certificate
    assert cert["node_type"] == "rnode"
    assert cert["board"] == "Heltec Wireless Tracker"
    assert "02:00:00:02:00:06" in (cert.get("usb_serial") or "")


@pytest.mark.onboard_guard   # drives the guard; needs the real lookups
def test_gate_would_block_the_medics_own_radio():
    """assert_flashable inside _flash refuses a rostered port outright."""
    import ui.onboard_roster as ob
    conn = tracker_conn()
    w = RNodeFlashWorkflow(conn, get_board("heltec_wireless_tracker"),
                           port=JONESEY_PORT,
                           work_ports_fn=lambda: [JONESEY_PORT])
    orig = (ob.serial_for_port, ob.onboard_serials, ob.guard_is_active)
    ob.serial_for_port = lambda p: "A1:B2:C3:D4:E5:F6"
    ob.onboard_serials = lambda path=None: {"A1:B2:C3:D4:E5:F6"}
    ob.guard_is_active = lambda path=None: True      # medic conditions
    try:
        r = w._flash()
    finally:
        ob.serial_for_port, ob.onboard_serials, ob.guard_is_active = orig
    assert not r.success
    assert "refusing" in r.message.lower()


def test_pieces_end_with_a_real_reset_not_esptools_ignored_one():
    """esptool's --after hard_reset pulses RTS with DTR asserted; a native-USB
    ESP32-S3 (USB-Serial/JTAG) ignores that and stays in download mode — the
    Tracker sat dark and "not responding" through every attempt of
    2026-10-06. The medic drops DTR first, then pulses RTS, then waits for
    the board to speak RNode before naming it."""
    from tests.srcutil import func_source
    pieces = func_source("workflows/rnode_flash.py", "_write_app_in_pieces")
    assert "--after hard_reset read_mac" not in pieces   # the ignored reset is gone
    assert "_reset_into_app(" in pieces
    reset = func_source("workflows/rnode_flash.py", "_reset_into_app")
    assert "s.dtr=False; s.rts=True" in reset and "s.rts=False" in reset
    fork = func_source("workflows/rnode_flash.py", "_flash_custom_fork")
    assert fork.index("_wait_for_rnode(") < fork.index("sleep 4 && rnodeconf {self.port} --eeprom-wipe")
    assert "--after hard_reset " not in fork


def test_the_software_stamp_is_checked_not_fired_and_forgotten():
    """After the naming the board resets; the hash step re-finds it by USB
    serial and reads the tool's "Firmware hash set" — a skipped stamp shows
    FIRMWARE CORRUPT on the board while the medic would have said done."""
    from tests.srcutil import func_source
    fork = func_source("workflows/rnode_flash.py", "_flash_custom_fork")
    naming = fork.index("_identity_ok(")
    hashing = fork.index("embedded_hash_command(self.port")
    assert naming < fork.index("find_port_by_usb_serial(self.connection, pre_serial, tries=15", naming) < hashing
    assert "firmware hash set" in fork
    assert "stamping its software didn't" in fork
