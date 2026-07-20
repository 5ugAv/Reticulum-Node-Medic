"""GPIO UART login-console enablement — the wired medic link for 3A+/Zero nodes
that can't be a USB gadget while hosting their radio."""

from transport.connection import EmulatedConnection
from provisioning.uart_console import (
    config_txt_with_uart, cmdline_with_uart, enable_uart_console,
    CONSOLE_BAUD, _ENABLE_UART, _CONSOLE_TOKEN,
)


# ---- config.txt (enable_uart) --------------------------------------------

def test_config_adds_enable_uart():
    out = config_txt_with_uart("dtparam=audio=on\n")
    assert _ENABLE_UART in out
    assert "dtparam=audio=on" in out                 # keeps existing content


def test_config_is_idempotent():
    once = config_txt_with_uart("dtparam=audio=on\n")
    assert config_txt_with_uart(once) == once        # no duplicate enable_uart
    assert once.count(_ENABLE_UART) == 1


def test_config_noop_when_already_present():
    text = "enable_uart=1\n"
    assert config_txt_with_uart(text) == text


# ---- cmdline.txt (console=serial0) ---------------------------------------

def test_cmdline_adds_serial_console():
    out = cmdline_with_uart("console=tty1 root=PARTUUID=abc rootwait\n")
    assert _CONSOLE_TOKEN in out.split()
    assert "console=tty1" in out.split()             # HDMI console left intact
    assert "root=PARTUUID=abc" in out


def test_cmdline_fixes_wrong_baud():
    out = cmdline_with_uart("console=serial0,9600 console=tty1 rootwait")
    toks = out.split()
    assert f"console=serial0,{CONSOLE_BAUD}" in toks
    assert "console=serial0,9600" not in toks         # replaced, not duplicated
    assert sum(t.startswith("console=serial0,") for t in toks) == 1


def test_cmdline_is_idempotent():
    once = cmdline_with_uart("console=tty1 rootwait")
    assert cmdline_with_uart(once) == once


def test_cmdline_preserves_trailing_newline():
    assert cmdline_with_uart("console=tty1 rootwait\n").endswith("\n")
    assert not cmdline_with_uart("console=tty1 rootwait").endswith("\n")


# ---- apply over a Connection ---------------------------------------------

def _boot_conn(config="dtparam=audio=on\n", cmdline="console=tty1 rootwait\n"):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("cat /boot/firmware/config.txt", code=0, stdout=config)
    c.rule("cat /boot/firmware/cmdline.txt", code=0, stdout=cmdline)
    return c


def test_enable_uart_writes_both_files_and_getty():
    conn = _boot_conn()
    res = enable_uart_console(conn)
    assert res.ok and res.changed
    h = conn.history
    assert any("config.txt" in c and "enable_uart=1" in c for c in h)
    assert any("cmdline.txt" in c and "console=serial0" in c for c in h)
    assert any("serial-getty@ttyS0" in c for c in h)
    # writes go through the passwordless-sudo priv wrapper
    assert all("sudo -n" in c for c in h if "tee " in c)


def test_enable_uart_is_idempotent_no_rewrite():
    conn = _boot_conn(config="enable_uart=1\n",
                      cmdline=f"console=serial0,{CONSOLE_BAUD} console=tty1 rootwait\n")
    res = enable_uart_console(conn)
    assert res.ok and res.changed is False
    # already-enabled: neither boot file is rewritten
    assert not any("tee /boot/firmware/config.txt" in c for c in conn.history)
    assert not any("tee /boot/firmware/cmdline.txt" in c for c in conn.history)


def test_enable_uart_reports_failure():
    conn = _boot_conn()
    conn.rules.insert(0, ("tee /boot/firmware/config.txt", 1, "", "read-only fs"))
    res = enable_uart_console(conn)
    assert res.ok is False and "read-only fs" in res.message
