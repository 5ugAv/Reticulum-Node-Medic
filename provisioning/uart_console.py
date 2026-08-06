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

import base64
from dataclasses import dataclass
from typing import List

from transport.connection import Connection

#: Console baud. 115200 is the Pi default and stable on the mini-UART once
#: enable_uart pins the core clock; the medic's serial link must match.
CONSOLE_BAUD = 115200

#: config.txt line that routes a usable UART to GPIO14/15.
_ENABLE_UART = "enable_uart=1"

#: RELEASE THE GOOD UART TO THE HEADER. This is NOT optional garnish on a
#: BCM2837 board (Pi 3A+, 3B+, AND the Zero 2 W — i.e. exactly the boards
#: ``reachability`` sends down the UART path because their single USB port is
#: taken by the radio).
#:
#: On those boards Bluetooth owns the PL011, so GPIO14/15 are served by the
#: MINI-uart, whose baud is derived from the VPU core clock. The core clock
#: moves with load and temperature, so the effective baud drifts and the stream
#: turns to noise under any activity. Measured on a 3A+ 2026-08-06: the login
#: prompt arrived clean at an idle 115200 (100% printable), then degraded to
#: garbage the moment the CPU did anything — a login could not be driven at all,
#: not by hand and not by ``uart_link.connect_uart``. Adding this line fixed it
#: outright and the console has been solid since.
#:
#: The comment that used to sit above ``enable_uart`` claimed it pinned the core
#: clock and that this was "the whole reason a stock mini-UART console is flaky".
#: That is not sufficient on BCM2837 in practice — the observed behaviour
#: contradicted it, which is why this exists.
#:
#: THE TRADE, stated plainly because it is a real one: this turns OFF onboard
#: Bluetooth. For a mesh node that is almost certainly right — it needs a
#: reliable wired console far more than it needs BT, and the console is its only
#: way in once the radio occupies the USB port — but it must be a decision, not
#: a silent side effect.
_DISABLE_BT = "dtoverlay=disable-bt"

#: The kernel/login console on the primary UART. ``serial0`` is the Pi's stable
#: alias for whichever UART is on GPIO14/15 (ttyS0 on Bluetooth boards like the
#: 3A+). ``console=serial0,BAUD`` gives BOTH kernel messages and, via systemd's
#: serial-getty generator, a login prompt — which is what the medic drives.
_CONSOLE_TOKEN = f"console=serial0,{CONSOLE_BAUD}"


def config_txt_with_uart(text: str) -> str:
    """Return *text* (a Pi ``config.txt``) with a USABLE GPIO UART console —
    ``enable_uart=1`` AND ``dtoverlay=disable-bt``. Idempotent per line, so a
    file that already has one gains only the other."""
    have = {ln.strip() for ln in text.splitlines()}
    missing = [ln for ln in (_ENABLE_UART, _DISABLE_BT) if ln not in have]
    if not missing:
        return text
    sep = "" if text.endswith("\n") or text == "" else "\n"
    return (f"{text}{sep}\n# GPIO UART login console (Node Medic wired link).\n"
            f"# disable-bt hands GPIO14/15 the real PL011 instead of the\n"
            f"# mini-uart, whose baud drifts with the core clock and turns the\n"
            f"# console to garbage under load. Costs onboard Bluetooth.\n"
            + "".join(f"{ln}\n" for ln in missing))


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
    # usually spawns it, but enabling the unit makes it deterministic). Each step
    # is a single argument-pinned privileged command (`sudo -n tee <path>` /
    # `sudo -n systemctl enable <unit>`), NOT a `sudo bash -c "..."`, so a scoped
    # sudoers can whitelist each exact command with no arbitrary shell surface.
    steps.append("sudo -n systemctl enable serial-getty@ttyS0.service")

    for step in steps:
        res = conn.run(step)
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
    """A privileged write of *content* to *path*. The privileged token is exactly
    ``sudo -n tee <path>`` (whitelistable, no ``bash -c``).

    base64 rather than a heredoc — see the note in sd_edit._tee: a quoted marker
    does not prevent a line in the content from terminating the heredoc early
    and turning the remainder into shell."""
    b64 = base64.b64encode(content.encode()).decode()
    return f"echo {b64} | base64 -d | sudo -n tee {path} > /dev/null"
