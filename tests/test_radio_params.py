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
    cfg = RadioConfig(frequency_mhz=868.5, bandwidth_khz=250.0,
                      spreading_factor=7, coding_rate=6, tx_power_dbm=22)
    cmd = set_params_command("/dev/ttyACM1", cfg)
    assert "--freq 868500000" in cmd
    assert "--bw 250000" in cmd
    assert "--sf 7" in cmd and "--cr 6" in cmd and "--txp 22" in cmd


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
