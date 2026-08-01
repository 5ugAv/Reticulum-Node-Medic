"""USB-gadget ethernet enablement — boot-file transforms + the privileged apply
uses discrete, whitelistable `sudo -n` commands (no `sudo bash -c`)."""

from transport.connection import EmulatedConnection
from provisioning.gadget import (
    cmdline_with_gadget, config_txt_with_gadget, enable_gadget,
    GADGET_USB_IP, _GADGET_MODULES, _DWC2_OVERLAY,
)


# ---- pure transforms ------------------------------------------------------

def test_config_adds_dwc2_overlay():
    out = config_txt_with_gadget("dtparam=audio=on\n")
    assert _DWC2_OVERLAY in out
    assert "dtparam=audio=on" in out


def test_config_is_idempotent():
    once = config_txt_with_gadget("dtparam=audio=on\n")
    assert config_txt_with_gadget(once) == once


def test_cmdline_inserts_modules_after_rootwait():
    out = cmdline_with_gadget("console=tty1 root=PARTUUID=abc rootwait\n")
    toks = out.split()
    assert _GADGET_MODULES in toks
    assert toks.index(_GADGET_MODULES) == toks.index("rootwait") + 1


def test_cmdline_is_idempotent():
    once = cmdline_with_gadget("console=tty1 rootwait")
    assert cmdline_with_gadget(once) == once


# ---- apply over a Connection ----------------------------------------------

def _boot_conn(config="dtparam=audio=on\n", cmdline="console=tty1 rootwait\n"):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("cat /boot/firmware/config.txt", code=0, stdout=config)
    c.rule("cat /boot/firmware/cmdline.txt", code=0, stdout=cmdline)
    return c


def test_enable_gadget_uses_discrete_sudo_commands_not_bash_c():
    conn = _boot_conn()
    res = enable_gadget(conn)
    assert res.ok and res.changed
    h = conn.history
    # Boot files + the static-IP unit are written, and the unit enabled.
    assert any("config.txt" in c and _DWC2_OVERLAY in c for c in h)
    assert any("cmdline.txt" in c and _GADGET_MODULES in c for c in h)
    assert any("nodemedic-gadget-ip.service" in c for c in h)
    # THE hardening guarantee: the privileged INVOCATION is `sudo -n tee` /
    # `sudo -n systemctl` — never `sudo -n bash -c` / `sudo -n sh -c`. (The unit
    # file written by tee legitimately CONTAINS a `/bin/sh -c` ExecStartPre; that
    # is file content, not a sudo target, so we check the invocation prefix only.)
    priv = [c for c in h if "sudo -n" in c]
    assert priv, "no privileged commands issued"
    assert all("sudo -n bash -c" not in c and "sudo -n sh -c" not in c for c in priv)
    assert all(c.lstrip().startswith("sudo -n tee ")
               or c.lstrip().startswith("sudo -n systemctl ") for c in priv)
    assert GADGET_USB_IP in res.message


def test_enable_gadget_idempotent_no_rewrite():
    conn = _boot_conn(config=f"{_DWC2_OVERLAY}\n",
                      cmdline=f"console=tty1 rootwait {_GADGET_MODULES}\n")
    res = enable_gadget(conn)
    assert res.ok and res.changed is False
    assert not any("tee /boot/firmware/config.txt" in c for c in conn.history)
    assert not any("tee /boot/firmware/cmdline.txt" in c for c in conn.history)


def test_overlay_is_pinned_to_all_not_inherited_from_a_board_section():
    """config.txt is SECTIONED. A file ending in [pi5] would swallow a bare
    append into that filter — dwc2 absent on every other board, with nothing
    wrong-looking in the file. Real stock Pi OS ships [cm4]/[cm5]/[pi5] blocks
    (seen on a card imaged 2026-08-01), so the append must re-open [all]."""
    from provisioning.gadget import config_txt_with_gadget
    risky = "dtparam=audio=on\n\n[pi5]\ndtoverlay=nospi10\n"
    out = config_txt_with_gadget(risky)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    i = lines.index("dtoverlay=dwc2")
    assert "[all]" in lines[:i], "overlay is not under an [all] section"
    # and the last section header before our overlay must BE [all]
    headers = [l for l in lines[:i] if l.startswith("[") and l.endswith("]")]
    assert headers[-1] == "[all]"


def test_pinning_stays_idempotent():
    from provisioning.gadget import config_txt_with_gadget
    once = config_txt_with_gadget("[pi5]\ndtoverlay=nospi10\n")
    assert config_txt_with_gadget(once) == once
    assert once.count("dtoverlay=dwc2") == 1
    assert once.count("[all]") == 1
