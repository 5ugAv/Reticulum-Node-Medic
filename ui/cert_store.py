"""On-medic store of birth certificates.

Every node the medic births is saved here as a JSON file, so the operator can:
  * search for a node they birthed earlier (to mount it in the wild -> Triage),
  * re-open a node's certificate and its notes from the map/VITALS later,
  * still export it off-device via the on-screen QR (that path is unchanged).

Pure filesystem + JSON, no Kivy — unit-tested against a temp dir. Runtime code
passes the default CERT_DIR; tests inject their own. A certificate is a plain
dict (the birth-certificate the build produced, plus operator name/notes/location);
each stored file also carries an ``_id`` (stable, so notes update in place) and a
``_saved_at`` epoch for ordering.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

CERT_DIR = os.path.expanduser("~/.reticulum-node-medic/certificates")


def _slug(text: str) -> str:
    """A filesystem-safe, lowercase slug — letters/digits/dashes only."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "")).strip("-").lower()
    return s or "node"


def cert_id(cert: Dict) -> str:
    """A STABLE id for a certificate, so saving the same node twice (e.g. after
    adding notes) overwrites rather than duplicates. Prefers the build session id
    / Reticulum address (unique per build); falls back to the node name."""
    base = _slug(cert.get("node_name") or cert.get("hostname") or "node")
    uniq = cert.get("session_id") or cert.get("reticulum_address") or ""
    return f"{base}-{_slug(uniq)}" if uniq else base


def save_cert(cert: Dict, cert_dir: str = CERT_DIR, now: Optional[float] = None) -> str:
    """Persist *cert* and return its id. Idempotent on the id (re-save overwrites).
    ``now`` is injectable for tests; defaults to wall-clock (runtime only)."""
    os.makedirs(cert_dir, exist_ok=True)
    cid = cert.get("_id") or cert_id(cert)
    stored = dict(cert)
    stored["_id"] = cid
    stored.setdefault("_saved_at", now if now is not None else time.time())
    # atomic: a power cut mid-write must not leave an empty certificate
    from monitor.atomic_json import write_json
    write_json(os.path.join(cert_dir, f"{cid}.json"), stored, indent=2)
    return cid


def load_certs(cert_dir: str = CERT_DIR) -> List[Dict]:
    """Every stored certificate, newest first. Empty list if the dir is absent."""
    if not os.path.isdir(cert_dir):
        return []
    out: List[Dict] = []
    for name in os.listdir(cert_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(cert_dir, name)) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda c: c.get("_saved_at", 0), reverse=True)
    return out


def usb_serial_key(usb_serial: str) -> str:
    """The comparable identity of a board, out of a /dev/serial/by-id name.

    THE ONE WAY to ask "is this the same board?". Never compare the whole by-id
    basename: the vendor part of it is not stable across a reset. A RAK4631
    announces itself as ``RAKWireless`` from its bootloader and ``RAKwireless``
    once running firmware — one capital letter apart — so an exact compare
    silently fails on every nRF52 board.

    That single letter broke four separate things on 2026-08-05: the post-flash
    port re-acquire, the DFU pre-touch, retiring an old certificate on rebirth,
    and recognising a board as kin. Only the hardware serial survives, so key on
    that and fold case for the rest.

    A rebirth is usually a REPAIR, not a replacement (operator, 2026-08-05): the
    node keeps its name and must still be kin afterwards. That only works if the
    board's identity is stable across the reflash — which is this function.
    """
    if not usb_serial:
        return ""
    try:
        from workflows.rnode_flash import by_id_serial
        return (by_id_serial(usb_serial) or usb_serial).lower()
    except Exception:                                             # noqa: BLE001
        return usb_serial.lower()


def same_board(a: str, b: str) -> bool:
    """True when two by-id names denote the same physical board."""
    ka = usb_serial_key(a)
    return bool(ka) and ka == usb_serial_key(b)


def cert_for_usb_serial(usb_serial: str, cert_dir: str = CERT_DIR):
    """The stored certificate for this board, or None — matched by serial, so a
    board recognises whether it is in its bootloader or running firmware."""
    if not usb_serial:
        return None
    for cert in load_certs(cert_dir):
        if same_board(cert.get("usb_serial"), usb_serial):
            return cert
    return None


def delete_by_usb_serial(usb_serial: str, cert_dir: str = CERT_DIR) -> int:
    """Remove stored certificates whose board fingerprint is *usb_serial*.
    Called when a board is WIPED (rebirth): leaving the old certificate behind
    made the now-blank board recognise as 'already flashed' on the next plug-in
    (2026-08-01 bug hunt). Returns how many were removed."""
    if not usb_serial:
        return 0
    want = usb_serial_key(usb_serial)
    removed = 0
    for cert in load_certs(cert_dir):
        if not want or usb_serial_key(cert.get("usb_serial")) != want:
            continue
        cid = cert.get("_id")
        if not cid:
            continue
        try:
            os.remove(os.path.join(cert_dir, f"{cid}.json"))
            removed += 1
        except OSError:
            pass
    return removed


def search_certs(query: str, cert_dir: str = CERT_DIR) -> List[Dict]:
    """Certificates whose name/hostname/address contains *query* (case-insensitive).
    A blank query returns them all (newest first) — the natural 'browse' state."""
    q = (query or "").strip().lower()
    certs = load_certs(cert_dir)
    if not q:
        return certs
    fields = ("node_name", "hostname", "ssh_address", "reticulum_address")
    return [c for c in certs
            if any(q in str(c.get(f, "")).lower() for f in fields)]


def update_notes(cid: str, notes: str, cert_dir: str = CERT_DIR) -> bool:
    """Set the notes on a stored certificate (by id) and re-save. Returns False if
    it isn't found."""
    path = os.path.join(cert_dir, f"{cid}.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            cert = json.load(f)
    except (OSError, ValueError):
        return False
    cert["notes"] = notes
    from monitor.atomic_json import write_json
    write_json(path, cert, indent=2)
    return True
