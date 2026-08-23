"""Optional second-tier alerting — push an escalation to the operator's own
Reticulum address (their Sideband / Columba / any LXMF client), so they hear about
a down node even away from the medic.

Entirely OPTIONAL: the medic's on-screen alert always fires; this only *also* sends
an LXMF message when the operator has saved their address in Settings. Blank address
= medic-only, no push.

The actual LXMF send runs in a short-lived SUBPROCESS against the running rnsd
shared instance — isolated so mesh code can never wedge or crash the Kivy app, and
time-limited so a missing path can't hang the monitor loop. It is best-effort: a
failure (no path yet, offline) just means the on-medic alert stands and we retry on
the next escalation.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Optional, Tuple

OPERATOR_ADDR_FILE = os.path.expanduser("~/.reticulum-node-medic/operator_address")

#: A Reticulum/LXMF destination hash is 16 bytes = 32 hex chars.
_ADDR_RE = re.compile(r"^[0-9a-f]{32}$")


def normalize_address(addr: str) -> str:
    """Strip common decorations (``<...>``, ``lxmf@``, spaces) and lowercase, so a
    pasted Sideband address matches the bare-hex form we validate/store."""
    if not addr:
        return ""
    a = addr.strip().lower()
    a = a.replace("lxmf@", "").strip("<>").replace(" ", "").replace(":", "")
    return a


def valid_address(addr: str) -> bool:
    return bool(_ADDR_RE.match(normalize_address(addr)))


def load_operator_address() -> str:
    try:
        with open(OPERATOR_ADDR_FILE, encoding="utf-8") as f:
            return normalize_address(f.read())
    except OSError:
        return ""


def save_operator_address(addr: str) -> str:
    """Persist a (normalized) address, or clear it when blank/invalid. Returns what
    was stored ("" if cleared)."""
    norm = normalize_address(addr)
    if norm and not valid_address(norm):
        norm = norm            # keep as-typed only if valid; else store nothing
    to_store = norm if valid_address(norm) else ""
    # Atomic write (temp + fsync + os.replace) so a field power-cut can't truncate
    # the address file — a half-written address is unparseable and the operator
    # would believe alerts are configured and hear nothing (2026-08-01 bug hunt).
    from monitor.atomic_json import write_text
    if not write_text(OPERATOR_ADDR_FILE, to_store, mode=0o644):
        # Don't pretend it saved — fail loud, as before.
        raise RuntimeError("Couldn't save the operator address.")
    return to_store


#: Inline sender script — runs in its own process so RNS/LXMF never touch the app.
_LXMF_SEND = r'''
import sys, os, time
addr_hex, text = sys.argv[1], sys.argv[2]
try:
    import RNS, LXMF
except Exception as e:
    print("no RNS/LXMF:", e); sys.exit(2)
try:
    RNS.Reticulum()                       # attach to the running rnsd shared instance
    idp = os.path.expanduser("~/.reticulum-node-medic/lxmf_identity")
    if os.path.exists(idp):
        ident = RNS.Identity.from_file(idp)
    else:
        ident = RNS.Identity(); os.makedirs(os.path.dirname(idp), exist_ok=True); ident.to_file(idp)
    # The class is LXMRouter (verified against LXMF 1.0.1 on the medic
    # 2026-08-01) — the old LXMFRouter name does not exist, so EVERY operator
    # push died with AttributeError before it ever reached the mesh. Accept
    # either name so a future rename can't silence alerts again.
    _Router = getattr(LXMF, "LXMRouter", None) or getattr(LXMF, "LXMFRouter")
    router = _Router(storagepath=os.path.expanduser("~/.reticulum-node-medic/lxmf"))
    source = router.register_delivery_identity(ident, display_name="Node Medic")
    dh = bytes.fromhex(addr_hex)
    if not RNS.Transport.has_path(dh):
        RNS.Transport.request_path(dh)
        t = time.time()
        while not RNS.Transport.has_path(dh) and time.time() - t < 15:
            time.sleep(0.5)
    dest_identity = RNS.Identity.recall(dh)
    if dest_identity is None:
        print("no path to destination"); sys.exit(3)
    dest = RNS.Destination(dest_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    msg = LXMF.LXMessage(dest, source, text, "Node Medic")
    router.handle_outbound(msg)
    time.sleep(4)                          # let it hand off to the propagation net
    print("sent"); sys.exit(0)
except Exception as e:
    print("send failed:", e); sys.exit(1)
'''


def _lxmf_send(address: str, text: str) -> bool:
    """Best-effort LXMF send via the isolated subprocess. True if it reported sent.
    NOTE: real mesh delivery must be VERIFIED on the medic — it can't be tested off
    the device."""
    try:
        p = subprocess.run(["python3", "-c", _LXMF_SEND, address, text],
                           capture_output=True, text=True, timeout=40)
        return p.returncode == 0
    except Exception:
        return False


def send_operator_alert(text: str, address: Optional[str] = None,
                        sender: Optional[Callable[[str, str], bool]] = None
                        ) -> Tuple[bool, str]:
    """Push *text* to the operator's Reticulum address, if one is set + valid.
    Returns (ok, message). No-ops cleanly when no address is configured — the
    on-medic alert is the always-on path; this is the optional phone push.
    ``sender`` is injectable for tests."""
    addr = normalize_address(address if address is not None else load_operator_address())
    if not addr:
        return (False, "no operator address set — medic-only alert")
    if not valid_address(addr):
        return (False, "operator address isn't a valid Reticulum/LXMF address")
    send = sender or _lxmf_send
    try:
        ok = bool(send(addr, text))
    except Exception:
        ok = False
    return (ok, "pushed to your Reticulum address" if ok
            else "couldn't reach your address (medic alert stands; will retry)")
