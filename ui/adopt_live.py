"""Live wiring for node ADOPTION on the medic — reads a connected board's boot
banner + /status, and builds an AdoptWorkflow bound to the real cert store, kin
roster and this medic's builder identity.

main.py runs ON the medic, so these read serial / curl locally (no SSH). The
board's boot banner carries everything adoption needs: the health-beacon dest
(the kin key), the canonical LoRa params, and the node's /status URL — so we
reset the board, capture the fresh banner, then (best-effort) fetch /status for
the node's own name.
"""

from __future__ import annotations

import re
import time
import urllib.request
from typing import Optional

from workflows.adopt_node import AdoptWorkflow

_STATUS_URL_RE = re.compile(r"https?://[\w.\-]+/status")


def read_board_banner(port: Optional[str], seconds: float = 10.0) -> str:
    """Reset the board and capture its fresh boot banner (~10 s). The RTNode
    firmware prints its LoRa config + ``[HealthBeacon] init dst=…`` + its /status
    URL on every boot, so a reset guarantees we see them even for a node that
    booted long ago."""
    if not port:
        return ""
    # HARD GATE: never reset/read the medic's own radio (Jonesey / a clone's).
    from ui.onboard_roster import assert_flashable, guard_is_active
    if guard_is_active():                # enforce on the medic; skip on dev/CI
        assert_flashable(port)           # raises ProtectedBoardError if protected
    try:
        import serial
    except Exception:
        return ""
    try:
        s = serial.Serial(port, 115200, timeout=1)
    except Exception:
        return ""
    try:
        # pulse reset (DTR/RTS) — works on the S3 USB-CDC (verified on FAITH)
        try:
            s.setDTR(False); s.setRTS(True); time.sleep(0.1)
            s.setRTS(False); time.sleep(0.1)
        except Exception:
            pass
        out, t0 = [], time.time()
        while time.time() - t0 < seconds:
            chunk = s.read(256)
            if chunk:
                out.append(chunk.decode(errors="replace"))
        return "".join(out)
    finally:
        try:
            s.close()
        except Exception:
            pass


def read_status_from_banner(banner: str, timeout: float = 4.0) -> Optional[str]:
    """If the banner advertises a ``http://<host>/status`` endpoint, fetch it for
    the node's own name/board/firmware. Best-effort — None if unreachable."""
    m = _STATUS_URL_RE.search(banner or "")
    if not m:
        return None
    try:
        with urllib.request.urlopen(m.group(0), timeout=timeout) as r:
            return r.read().decode(errors="replace")
    except Exception:
        return None


def make_adopt_workflow(board_port: Optional[str] = None,
                        name_override: str = "") -> AdoptWorkflow:
    """An AdoptWorkflow bound to the medic's real serial/HTTP readers and the real
    cert store + kin roster, stamped with this medic's builder identity."""
    if board_port is None:
        try:
            from ui.hw_factories import local_board_ports
            ports = local_board_ports()
            board_port = ports[0] if ports else None
        except Exception:
            board_port = None

    def banner_reader(port):
        return read_board_banner(port)

    def status_reader():
        # reuse the banner captured during identify by re-reading briefly; the
        # workflow calls banner_reader first, so re-derive the URL from a short read
        return read_status_from_banner(read_board_banner(board_port, seconds=6.0))

    def save_cert(cert):
        from ui.cert_store import save_cert as _save
        return _save(cert)

    def register_kin(*a, **kw):
        from monitor import kin_roster
        return kin_roster.register(*a, **kw)

    def gps_reader():
        try:
            from monitor.geo import read_gps
            fix = read_gps()
            return (fix.lat, fix.lon) if fix else None
        except Exception:
            return None

    builder = None
    try:
        from provisioning import tool_identity
        builder = tool_identity.identity_hash()
    except Exception:
        builder = None

    return AdoptWorkflow(
        board_port=board_port, banner_reader=banner_reader,
        status_reader=status_reader, gps_reader=gps_reader,
        node_name_override=name_override, save_cert=save_cert,
        register_kin=register_kin, builder_hash=builder)
