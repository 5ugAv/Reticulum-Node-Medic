import pytest

from transport.connection import EmulatedConnection
from workflows.rnode_boards import get_board
from workflows.rnode_flash import (
    RNodeFlashWorkflow,
    flash_command,
    birth_flash,
    autoinstall_interactions,
    SUCCESS_MARKER,
    FIRMWARE_VERSION,
)

V4 = get_board("heltec32_v4")


class _PtyConn(EmulatedConnection):
    """An emulated connection that ALSO exposes run_interactive (like the real
    LocalConnection), so birth_flash drives it through the PTY path. Each call
    returns the next queued (code, out) and records the (cmd, interactions)."""

    def __init__(self, replies):
        super().__init__(default_code=0, default_stdout="ok")
        self._replies = list(replies)
        self.interactive_calls = []

    def run_interactive(self, command, interactions, timeout=400):
        self.interactive_calls.append((command, interactions))
        code, out = self._replies.pop(0)
        return (code, out, "")


# ---- birth_flash: the fresh-board two-pass ------------------------------


def _autoinstall_count(history):
    return sum(1 for c in history if "--autoinstall" in c)


def test_birth_flash_runs_twice_for_a_brand_new_board():
    # a blank board's first autoinstall flashes but re-enumerates before the
    # EEPROM is written; nodemedic makes the second pass part of the birth
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("--autoinstall", 0, "RNode Firmware autoinstallation complete!")
    ok, msg, already = birth_flash(c, V4, "/dev/ttyACM0")
    assert ok is True and already is False
    assert _autoinstall_count(c.history) == 2          # two passes
    assert "two passes" in msg


def test_birth_flash_single_pass_for_already_provisioned_board():
    # an already-flashed board births in one pass — no needless reflash
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("--autoinstall", 0,
           "This device is already installed and provisioned.")
    ok, msg, already = birth_flash(c, V4, "/dev/ttyACM0")
    assert ok is True and already is True
    assert _autoinstall_count(c.history) == 1          # only one pass


def test_birth_flash_reports_failure_from_the_second_pass():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("--autoinstall", 97, "Flash error")         # never completes
    ok, msg, already = birth_flash(c, V4, "/dev/ttyACM0")
    assert ok is False and already is False
    assert _autoinstall_count(c.history) == 2           # still tried twice
    assert "flash failed" in msg


# ---- birth_flash: PTY path for a real local board -----------------------


def test_autoinstall_interactions_maps_prompts_to_answers():
    # device menu -> 9, blurb -> enter, band -> 915 index, confirm -> y
    ix = autoinstall_interactions(V4, 915)
    assert [resp for _pat, resp in ix] == V4.autoinstall_answers(915)
    assert ix[0][0] == "matches your device type"      # first prompt pattern
    assert ix[-1][1] == "y"                             # final confirm answer


def test_birth_flash_uses_pty_when_connection_supports_it():
    # a real local board (run_interactive present) is driven through the PTY,
    # NOT a printf|rnodeconf pipe (which hangs on the terminal-read confirm).
    conn = _PtyConn([(0, "RNode Firmware autoinstallation complete!")])
    ok, msg, already = birth_flash(conn, V4, "/dev/ttyACM1")
    assert ok is True and already is False
    assert len(conn.interactive_calls) == 1            # one pass sufficed
    cmd, interactions = conn.interactive_calls[0]
    assert "--autoinstall" in cmd
    assert [r for _p, r in interactions] == V4.autoinstall_answers(915)
    assert "printf" not in cmd                          # never the stdin pipe
    assert not any("--autoinstall" in h for h in conn.history)  # no piped run


def test_birth_flash_pty_second_pass_for_fresh_board():
    # first PTY pass only flashes; the confirming second pass finishes the EEPROM
    conn = _PtyConn([(0, "flashing..."),
                     (0, "RNode Firmware autoinstallation complete!")])
    ok, msg, already = birth_flash(conn, V4, "/dev/ttyACM1")
    assert ok is True
    assert len(conn.interactive_calls) == 2


# ---- flash_command (the hardware-verified sequence) ---------------------


def test_flash_command_matches_verified_heltec_v4_sequence():
    cmd = flash_command(V4, "/dev/ttyACM0", band_mhz=915, version="1.86")
    # verified live: device 9 -> enter -> band 2 (915) -> confirm y
    assert cmd.startswith("printf '%s\\n' 9 '' 2 y | ")
    assert "rnodeconf /dev/ttyACM0 --autoinstall" in cmd
    assert "--nocheck" in cmd                    # offline, from the cache
    assert "--fw-version 1.86" in cmd


def test_flash_command_refuses_unverified_band():
    # Heltec V4 has no 433 MHz option -> must not guess
    with pytest.raises(ValueError):
        flash_command(V4, "/dev/ttyACM0", band_mhz=433)


def test_flash_command_refuses_board_without_verified_sequence():
    tbeam = get_board("tbeam")               # band map intentionally left blank
    with pytest.raises(ValueError):
        flash_command(tbeam, "/dev/ttyACM0", band_mhz=915)


# ---- workflow -----------------------------------------------------------


def flash_conn(ports="/dev/ttyACM0", online=False, flash_ok=True,
               provisioned=True):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0 if online else 7, "")             # connectivity
    c.rule("ls /dev/ttyACM", 0, ports)                      # single/multi board
    c.rules.insert(0, ("ls ~/.config/rnodeconf/update/1.86/*.zip",
                       0, "rnode_firmware_heltec32v4pa.zip", ""))
    c.rules.insert(0, ("--autoinstall", 0 if flash_ok else 97,
                       ("...\nRNode Firmware autoinstallation complete!"
                        if flash_ok else "Flash error"), ""))
    info = ("Device signature   : Validated\nFirmware version   : 1.86"
            if provisioned else "No answer from device")
    c.rules.insert(0, ("--info", 0, info, ""))
    return c


def test_workflow_happy_path_offline_flash():
    conn = flash_conn()
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert [r.name for r in results] == [
        "detect_port", "ensure_single_board", "ensure_firmware", "flash",
        "set_params", "verify"]
    assert all(r.success for r in results)
    # a brand-new board is flashed in two passes (fresh-ESP32 re-enumeration)
    h = conn.history
    assert _autoinstall_count(h) == 2
    # the canonical params are baked in at birth, then the board is left
    # host-controlled so a Pi's rnsd never aborts on a stale 250/SF11 default
    assert any("--tnc" in c and "--freq 915125000" in c and "--sf 9" in c
               for c in h)
    assert any(c.rstrip().endswith("-N") for c in h)


def test_workflow_refuses_multiple_boards():
    conn = flash_conn(ports="/dev/ttyACM0 /dev/ttyACM1")
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "ensure_single_board"
    assert results[-1].success is False


def test_workflow_offline_without_cache_fails():
    conn = flash_conn()
    # no cached firmware zip
    conn.rules.insert(0, ("ls ~/.config/rnodeconf/update/1.86/*.zip", 2, "", ""))
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "ensure_firmware"
    assert results[-1].success is False


def test_workflow_flash_failure_stops_before_verify():
    conn = flash_conn(flash_ok=False)
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "flash"
    assert results[-1].success is False
    assert "verify" not in [r.name for r in results]


def test_workflow_verify_detects_unprovisioned_board():
    conn = flash_conn(provisioned=False)
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    assert results[-1].name == "verify"
    assert results[-1].success is False


def test_workflow_already_provisioned_board_is_a_skip_not_a_failure():
    conn = flash_conn()
    # rnodeconf refuses to re-flash a provisioned board (real behaviour)
    conn.rules.insert(0, ("--autoinstall", 0,
                          "Device connected\nThis device is already installed "
                          "and provisioned. No further action will be taken.", ""))
    wf = RNodeFlashWorkflow(conn, V4, port="/dev/ttyACM0")
    results = wf.run_all()
    flash = next(r for r in results if r.name == "flash")
    assert flash.success is True
    assert flash.skipped is True
    assert all(r.success for r in results)          # continues to verify
