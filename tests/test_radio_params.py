"""Bake-radio-params-at-birth helper.

The fix for rnsd's "Radio state mismatch": a freshly provisioned RNode keeps
autoinstall's stale 250/SF11 default unless the deployment params are written in,
so every flash path calls this before handing the board to rnsd.
"""

import pytest

from transport.connection import EmulatedConnection
from node_profile import RadioConfig
from workflows.radio_params import (
    set_params_command, normal_mode_command, set_params_at_birth,
)


# ---- command builders (Hz conversion + required mode flag) ---------------

def test_set_params_command_converts_to_hz_with_tnc_flag():
    cmd = set_params_command("/dev/ttyACM0")
    # rnodeconf only writes the radio flags when a mode flag rides along
    assert "--tnc" in cmd
    # RadioConfig carries MHz/kHz; the device wants Hz
    assert "--freq 915125000" in cmd
    assert "--bw 125000" in cmd
    assert "--sf 9" in cmd and "--cr 5" in cmd and "--txp 17" in cmd
    assert "/dev/ttyACM0" in cmd


def test_set_params_command_honours_a_custom_config():
    # tx_power_dbm here was 22 — which is precisely the value rnodeconf's -T
    # branch refuses into an interactive input() hang; this test was pinning
    # the dangerous input as valid (Columba-review merge, 2026-08-20). The
    # >17 refusal has its own test in test_rtnode_build.py.
    cfg = RadioConfig(frequency_mhz=868.5, bandwidth_khz=250.0,
                      spreading_factor=7, coding_rate=6, tx_power_dbm=14)
    cmd = set_params_command("/dev/ttyACM1", cfg)
    assert "--freq 868500000" in cmd
    assert "--bw 250000" in cmd
    assert "--sf 7" in cmd and "--cr 6" in cmd and "--txp 14" in cmd


def test_normal_mode_command_returns_board_to_host_control():
    assert normal_mode_command("/dev/ttyACM0") == "rnodeconf /dev/ttyACM0 -N"


# ---- the runner (write params, then host-controlled) ---------------------

def test_set_params_at_birth_writes_then_switches_to_host_mode():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    ok, msg = set_params_at_birth(conn, "/dev/ttyACM0")
    assert ok
    # order matters: params first (TNC write), then -N so rnsd drives the radio
    assert conn.history[0] == set_params_command("/dev/ttyACM0")
    assert conn.history[1] == normal_mode_command("/dev/ttyACM0")
    assert "915.125 MHz" in msg and "host-controlled" in msg


def test_set_params_at_birth_fails_loudly_when_write_rejected():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rule("--tnc", code=1, stdout="Could not connect to device")
    ok, msg = set_params_at_birth(conn, "/dev/ttyACM0")
    assert ok is False
    assert "radio params" in msg
    # never left the board half-configured in TNC mode
    assert not any(c.rstrip().endswith("-N") for c in conn.history)


def test_set_params_at_birth_fails_if_host_mode_switch_fails():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rule("-N", code=1, stdout="error")
    ok, msg = set_params_at_birth(conn, "/dev/ttyACM0")
    assert ok is False
    assert "host-controlled mode" in msg


# -- BLE at birth (operator policy, 2026-08-28) ------------------------------

def test_bluetooth_on_command_shape():
    from workflows.radio_params import bluetooth_on_command
    assert bluetooth_on_command("/dev/ttyACM1") == \
        "rnodeconf /dev/ttyACM1 --bluetooth-on"


def test_enable_bluetooth_at_birth_delivers_and_reports_honestly():
    from workflows.radio_params import enable_bluetooth_at_birth

    class Conn:
        def __init__(self, code=0, out="Enabling Bluetooth...\n"):
            self.code, self.out, self.ran = code, out, []
        def run(self, cmd, timeout=None):
            self.ran.append(cmd)
            return self.code, self.out, ""

    c = Conn()
    ok, msg = enable_bluetooth_at_birth(c, "/dev/ttyACM1")
    assert ok and "persisted" in msg
    assert "--bluetooth-on" in c.ran[0]
    # no ack is echoed by rnodeconf, so silence = NOT delivered = honest fail
    ok, msg = enable_bluetooth_at_birth(Conn(out="ok"), "/dev/ttyACM1")
    assert not ok and "did not go through" in msg
    ok, msg = enable_bluetooth_at_birth(Conn(code=1), "/dev/ttyACM1")
    assert not ok


# --- the board must not lie about itself when we hand it over -------------

def _rec_conn():
    """A connection that records every command it is asked to run."""
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.seen = []
    orig = c.run

    def run(command, timeout=None):
        c.seen.append(command)
        return orig(command, timeout)

    c.run = run
    return c


def test_a_host_controlled_birth_puts_the_radio_to_sleep():
    """`--tnc <params>` is CMD_CONF_SAVE, which brings the radio UP. The `-N`
    that follows deletes the saved config but never stops the radio already
    running, so every RNode this tool birthed was left listening with nobody
    owning it — no standby breathe, live waterfall, which is exactly how a
    board looks when a host HAS opened it (operator, bench, 2026-09-11)."""
    from workflows.radio_params import set_params_at_birth
    conn = _rec_conn()
    ok, msg = set_params_at_birth(conn, "/dev/ttyACM9", mode="host")
    assert ok, msg
    joined = " ".join(conn.seen)
    assert "--tnc" in joined and "-N" in joined
    # CMD_RADIO_STATE(0x06) with 0x00 -> stopRadio(), and CMD_LEAVE(0x0A)
    # with 0xFF -> current_rssi back to -292, which empties the waterfall.
    assert "192, 6, 0x00, 192" in joined, "radio never told to stop"
    assert "192, 10, 0xFF, 192" in joined, "host never told the board it left"
    assert "asleep" in msg


def test_a_tnc_birth_leaves_its_radio_running():
    """The opposite case, and it must stay opposite: a pocket RNode in TNC
    mode is SUPPOSED to boot with a live radio."""
    from workflows.radio_params import set_params_at_birth
    conn = _rec_conn()
    ok, msg = set_params_at_birth(conn, "/dev/ttyACM9", mode="tnc")
    assert ok, msg
    joined = " ".join(conn.seen)
    assert "192, 6, 0x00, 192" not in joined, \
        "a TNC board's radio must be left live"
    assert "-N" not in joined


def test_a_board_that_will_not_sleep_still_counts_as_born():
    """Bedtime is best-effort: a board that will not take the command is
    still a correctly flashed board, and the birth must not fail over it —
    but the operator is told, because they will see no pulse."""
    from workflows.radio_params import set_params_at_birth
    conn = _rec_conn()
    conn.rule("import serial", code=1, stdout="no pyserial")
    ok, msg = set_params_at_birth(conn, "/dev/ttyACM9", mode="host")
    assert ok is True
    assert "power-cycled" in msg


def test_the_sleep_command_waits_for_the_board_to_re_enumerate():
    """These boards use the ESP32-S3's native USB-CDC, so OPENING the port
    resets them. Bytes written immediately after open are lost into a
    rebooting board (measured on the bench, 2026-09-11: a read 0.4 s after
    open died with "device reports readiness to read but returned no data")."""
    from workflows.radio_params import rest_radio_command
    cmd = rest_radio_command("/dev/ttyACM9")
    settle = cmd.split("s.write")[0]
    assert "time.sleep(3.0)" in settle, \
        "the command must wait for the board's USB to come back before writing"
