"""Share a birth certificate as an on-screen QR code.

The medic has no phone tethered and is usually offline in the field, so the
zero-setup way to get a certificate off it is a QR code on the touchscreen that
any phone camera can scan (no pairing, no network). This module is split so the
*payload* — what actually gets encoded — is pure and unit-tested, while the QR
matrix generation leans on ``segno`` (a pure-Python, dependency-free encoder)
imported lazily so this module still imports without it (and the core test suite
stays third-party-free, like the rest of the tool). The Kivy drawing lives in the
build screen; here we only produce data.
"""

from __future__ import annotations

from typing import List, Optional


def birth_cert_payload(cert: dict) -> str:
    """Render a birth-certificate dict into the compact, human-readable text a
    scanned QR should reveal. Kept small so the QR stays an easy scan, and
    ordered by what an operator needs first: how to reach the node, then how it
    was built."""
    lines: List[str] = ["RETICULUM NODE — BIRTH CERTIFICATE"]

    if cert.get("node_name"):
        lines.append(f"Name: {cert['node_name']}")
    _TYPES = {"rnode": "RNode (radio for a host)",
              "rtnode2400": "RTNode-2400 (standalone transport)",
              "pi_rnode": "Pi + RNode (propagation)",
              "pi_propagation": "Pi + RNode (propagation)"}
    if cert.get("node_type"):
        lines.append("Type: " + _TYPES.get(cert["node_type"],
                                           str(cert["node_type"])))

    host = cert.get("hostname") or ""
    ssh = cert.get("ssh_address") or ""
    if host or ssh:
        label = host or ssh
        lines.append(f"Host: {label}" + (f" ({ssh})" if ssh and ssh != label
                                          else ""))
    ips = cert.get("ip_addresses") or []
    if ips:
        lines.append("IP: " + ", ".join(ips))
    if cert.get("mac_address"):
        lines.append(f"MAC: {cert['mac_address']}")
    if cert.get("reticulum_address"):
        lines.append(f"Reticulum: {cert['reticulum_address']}")
    if cert.get("identity_hash"):
        lines.append(f"Identity: {cert['identity_hash']}")
    if cert.get("role"):
        lines.append(f"Role: {cert['role']}")

    board = cert.get("board") or ""
    if board:
        fw = cert.get("rnode_firmware")
        board_line = f"Board: {board}" + (f" (fw {fw})" if fw else "")
        if cert.get("rgb_led_pin") is not None:
            board_line += f", RGB pin {cert['rgb_led_pin']}"
        if cert.get("firmware") and not fw:
            board_line += f" (fw {cert['firmware']})"
        lines.append(board_line)

    usb = cert.get("usb_serial") or ""
    if usb:
        # show the MAC (not the noisy by-id basename); its last two bytes are
        # the 4-hex ID an RNode SHOWS ON ITS OWN SCREEN — so the paper record
        # can be matched to the glass (CDBE-style)
        import re as _re
        macs = _re.findall(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", usb)
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
    if cert.get("location"):
        lines.append(f"Location: {cert['location']}")
    if cert.get("born"):
        lines.append(f"Born: {cert['born']}")
    if cert.get("built_by"):
        lines.append(f"Built by: {cert['built_by']}")
    if cert.get("session_id"):
        lines.append(f"Built: {cert['session_id']}")
    if cert.get("notes"):
        lines.append(f"Notes: {cert['notes']}")
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
