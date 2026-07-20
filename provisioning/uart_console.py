"""GPIO UART login-console enablement — the wired link for boards that CAN'T be a
USB gadget.

A Pi 3A+ / Zero has a single USB controller. When it's hosting its RNode radio on
the USB-A port, dwc2 is in HOST mode, so the OTG port can't ALSO be a USB-ethernet
gadget to Node Medic (see provisioning.gadget) — the "single data port conflict".
For those boards the medic reaches the node over the **GPIO UART** instead: a
USB-serial adapter from the medic to the node's GPIO14 (TXD) / GPIO15 (RXD) / GND,
with a login getty on the node's serial console. No USB, no WiFi, no internet.

Stock Raspberry Pi OS does NOT expose a serial login console (``enable_uart`` off,
no ``console=serial0``), so a bone-stock node answers nothing on its UART. This
module bakes a serial console into a node image (idempotently), so any 3A+/Zero
the medic births comes up reachable over three wires. Everything here is pure
string / config transforms (unit-tested); ``enable_uart_console`` applies them
over a Connection. The medic side (drive the login, run commands) is separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from transport.connection import Connection

#: Console baud. 115200 is the Pi default and stable on the mini-UART once
#: enable_uart pins the core clock; the medic's serial link must match.
CONSOLE_BAUD = 115200

#: config.txt line that routes a usable UART to GPIO14/15 AND pins the core clock
#: so the mini-UART's baud is stable (the whole reason a stock mini-UART console
#: is flaky without it).
_ENABLE_UART = "enable_uart=1"

#: The kernel/login console on the primary UART. ``serial0`` is the Pi's stable
#: alias for whichever UART is on GPIO14/15 (ttyS0 on Bluetooth boards like the
#: 3A+). ``console=serial0,BAUD`` gives BOTH kernel messages and, via systemd's
#: serial-getty generator, a login prompt — which is what the medic drives.
_CONSOLE_TOKEN = f"console=serial0,{CONSOLE_BAUD}"


def config_txt_with_uart(text: str) -> str:
    """Return *text* (a Pi ``config.txt``) with ``enable_uart=1`` present.
    Idempotent — a no-op if it's already declared."""
    for line in text.splitlines():
        if line.strip() == _ENABLE_UART:
            return text
    sep = "" if text.endswith("\n") or text == "" else "\n"
    return (f"{text}{sep}\n# GPIO UART login console "
            f"(Node Medic wired link)\n{_ENABLE_UART}\n")


def cmdline_with_uart(text: str) -> str:
    """Return *text* (a Pi ``cmdline.txt`` — one space-separated line) with a
    ``console=serial0,BAUD`` login console. Replaces any existing
    ``console=serial0,...`` (baud fixup) and leaves the ``console=tty1`` (HDMI)
    entry alone. Idempotent."""
    trailing_nl = "\n" if text.endswith("\n") else ""
    tokens = text.split()
    if _CONSOLE_TOKEN in tokens:
        return text
    out: List[str] = []
    replaced = False
    for tok in tokens:
        if tok.startswith("console=serial0,"):
            out.append(_CONSOLE_TOKEN)              # fix the baud on an existing one
            replaced = True
        else:
            out.append(tok)
    if not replaced:
        # Serial console should come BEFORE console=tty1 so it's the primary; but
        # simply prepend — the kernel accepts multiple console= in any order.
        out.insert(0, _CONSOLE_TOKEN)
    return " ".join(out) + trailing_nl


@dataclass
class UartResult:
    ok: bool
    message: str
    changed: bool = False


def enable_uart_console(conn: Connection,
                        boot_dir: str = "/boot/firmware") -> UartResult:
    """Enable a GPIO UART login console on the Pi behind *conn* (idempotent).
    Edits ``<boot_dir>/config.txt`` + ``cmdline.txt`` and enables the serial
    getty. Requires root (the connection's priv wrapper / passwordless sudo).
    Takes effect on the node's next reboot. *boot_dir* is ``/boot/firmware`` on
    Bookworm, ``/boot`` on older images."""
    try:
        cfg = conn.run_checked(f"cat {boot_dir}/config.txt")
        cmd = conn.run_checked(f"cat {boot_dir}/cmdline.txt")
    except Exception as e:  # pragma: no cover - network/hardware failure
        return UartResult(False, f"Could not read boot config: {e}")

    new_cfg = config_txt_with_uart(cfg)
    new_cmd = cmdline_with_uart(cmd)
    changed = new_cfg != cfg or new_cmd != cmd

    steps: List[str] = []
    if new_cfg != cfg:
        steps.append(_tee(f"{boot_dir}/config.txt", new_cfg))
    if new_cmd != cmd:
        steps.append(_tee(f"{boot_dir}/cmdline.txt", new_cmd))
    # Belt-and-braces: explicitly enable the serial getty (the console= param
    # usually spawns it, but enabling the unit makes it deterministic).
    steps.append(f"systemctl enable serial-getty@ttyS0.service")

    for step in steps:
        res = conn.run(_priv(step))
        if res[0] != 0:
            return UartResult(False,
                              f"Failed applying UART console config: {res[2] or res[1]}",
                              changed)
    return UartResult(
        True,
        (f"GPIO UART login console enabled — reboot the node, then it answers on "
         f"GPIO14/15 at {CONSOLE_BAUD} baud." if changed
         else "GPIO UART login console already enabled."),
        changed)


def _tee(path: str, content: str) -> str:
    marker = "NM_UART_EOF"
    return f"tee {path} > /dev/null <<'{marker}'\n{content}\n{marker}"


def _priv(command: str) -> str:
    return f"sudo -n bash -c {_shq(command)}"


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
