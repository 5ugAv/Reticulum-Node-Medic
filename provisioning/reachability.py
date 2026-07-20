"""Bake the RIGHT wired medic-link into a node at BIRTH, by board class.

Node Medic must be able to reach every node it births over the WIRE, offline —
no WiFi, no internet. Which wire depends on the board:

* **Pi 4/5** have a separate USB controller (USB-C OTG for the gadget link, USB-A
  free for the radio), so they get **USB-gadget ethernet** — plug into a medic
  USB-A port and the node appears at a fixed IP (provisioning.gadget).
* **Pi 3A+ / Zero** have a SINGLE USB controller, occupied hosting the RNode radio
  on USB-A, so dwc2 can't also be a gadget — they get a **GPIO UART login
  console** instead (provisioning.uart_console): three wires to the medic.

So birth bakes gadget OR uart per the detected hardware, and the node comes up
medic-reachable on its next boot. This module is the router; the two mechanisms
live in their own modules.
"""

from __future__ import annotations

from typing import Optional, Tuple

from transport.connection import Connection
from node_profile import NodeHardware
from provisioning.gadget import enable_gadget, GADGET_USB_IP
from provisioning.uart_console import enable_uart_console, CONSOLE_BAUD

#: Pi 4/5 — separate USB controller / USB-C OTG. USB-gadget link; radio on USB-A.
_GADGET_BOARDS = {NodeHardware.PI_5}
#: 3A+/Zero — single USB controller, busy hosting the radio. GPIO UART console.
_UART_BOARDS = {NodeHardware.PI_3A_PLUS, NodeHardware.PI_ZERO_2W}


def link_kind(hardware: NodeHardware) -> Optional[str]:
    """``'gadget'`` (Pi 4/5), ``'uart'`` (3A+/Zero), or ``None`` (unknown board —
    no wired-link profile, so the caller shouldn't assume the medic can reach it
    over the wire without adding one)."""
    if hardware in _GADGET_BOARDS:
        return "gadget"
    if hardware in _UART_BOARDS:
        return "uart"
    return None


def bake_reachability(conn: Connection, hardware: NodeHardware,
                      boot_dir: str = "/boot/firmware") -> Tuple[Optional[str], bool, str]:
    """Enable the medic's wired link appropriate to *hardware* on the node behind
    *conn* (idempotent). Returns ``(kind, ok, message)`` — kind is 'gadget'/'uart'
    /None. Takes effect on the node's next reboot."""
    kind = link_kind(hardware)
    if kind == "gadget":
        r = enable_gadget(conn, boot_dir)
        return "gadget", r.ok, r.message
    if kind == "uart":
        r = enable_uart_console(conn, boot_dir)
        return "uart", r.ok, r.message
    name = getattr(hardware, "value", hardware)
    return (None, True,
            f"No wired-reachability profile for {name} — skipped. Add one before "
            f"the medic can reach this board class over the wire.")
