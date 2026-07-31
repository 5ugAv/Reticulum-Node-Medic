"""Share a birth certificate as an on-screen QR code.

The medic has no phone tethered and is usually offline in the field, so the
zero-setup way to get a certificate off it is a QR code on the touchscreen that
any phone camera can scan (no pairing, no network). This module is split so the
*payload* — what actually gets encoded — is pure and unit-tested, while the QR
matrix generation leans on ``segno`` (a pure-Python, dependency-free encoder)
imported lazily so this module still imports without it (and the core test suite
stays third-party-free, like the rest of the tool). The Kivy drawing lives in the
build screen; here we only produce data.

ANONYMOUS BY DESIGN (operator ethos decision 2026-08-01, following Reticulum's
own): a QR is the one artifact that LEAVES the medic — photographed, printed,
maybe stuck to the node's case in the wild. So the payload carries only the
identity-lite tier: name, type, board + its hardware ID, radio parameters,
born date. Everything that could trace a wild node back to a person or place —
location, who built it, LAN details (hostnames/IPs/MACs/SSH), the mesh
identity hash, free-text notes — lives ONLY in the certificate stored on the
medic itself. Each omitted field is innocent alone; together on a label they
deanonymize the mesh (identity + place + operator). The medic keeps the whole
truth; the label names nobody.
"""

from __future__ import annotations

import re
from typing import List, Optional

#: node_type -> the human label the scanned record shows.
_TYPES = {
    "rnode": "RNode (radio for a host)",
    "rtnode2400": "RTNode-2400 (standalone transport)",
    "pi_rnode": "Pi + RNode (propagation)",
    "pi_propagation": "Pi + RNode (propagation)",
}

_MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")


def birth_cert_payload(cert: dict) -> str:
    """Render a birth-certificate dict into the compact, ANONYMOUS text a
    scanned QR reveals — what the node is, never whose or where (see module
    docstring). The full certificate stays on the medic."""
    lines: List[str] = ["RETICULUM NODE — BIRTH CERTIFICATE"]

    if cert.get("node_name"):
        lines.append(f"Name: {cert['node_name']}")
    if cert.get("node_type"):
        lines.append("Type: " + _TYPES.get(cert["node_type"],
                                           str(cert["node_type"])))

    board = cert.get("board") or ""
    if board:
        fw = cert.get("rnode_firmware") or cert.get("firmware")
        board_line = f"Board: {board}" + (f" (fw {fw})" if fw else "")
        if cert.get("rgb_led_pin") is not None:
            board_line += f", RGB pin {cert['rgb_led_pin']}"
        lines.append(board_line)

    usb = cert.get("usb_serial") or ""
    if usb:
        # the hardware MAC's last two bytes are the 4-hex ID an RNode SHOWS
        # ON ITS OWN SCREEN — the paper record matches the glass (CDBE-style).
        # A hardware serial identifies the BOARD, not a person or place.
        macs = _MAC_RE.findall(usb)
        if macs:
            mac = macs[-1].upper()
            shows = mac.replace(":", "")[-4:]
            lines.append(f"Board ID: {mac}  (screen ID {shows})")
        else:
            lines.append(f"Board ID: {usb}")

    freq = cert.get("frequency_mhz")
    if freq:
        lines.append(
            f"Radio: {freq:g} MHz BW{cert.get('bandwidth_khz'):g} "
            f"SF{cert.get('spreading_factor')} CR{cert.get('coding_rate')} "
            f"{cert.get('tx_power_dbm')}dBm")
    elif cert.get("radio"):
        lines.append(f"Radio: {cert['radio']}")
    if cert.get("born"):
        lines.append(f"Born: {cert['born']}")
    return "\n".join(lines)


def qr_matrix(data: str, error: str = "m") -> Optional[List[List[bool]]]:
    """Encode *data* into a QR module matrix (rows of booleans, True = dark).

    Returns ``None`` when ``segno`` is not installed so callers can fall back to
    showing the text instead of crashing — the QR is a convenience layered over
    the certificate the screen already displays.
    """
    try:
        import segno
    except ImportError:
        return None
    qr = segno.make(data, error=error)
    return [[bool(cell) for cell in row] for row in qr.matrix]
