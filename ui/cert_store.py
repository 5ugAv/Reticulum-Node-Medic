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
    with open(os.path.join(cert_dir, f"{cid}.json"), "w") as f:
        json.dump(stored, f, indent=2)
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
    with open(path, "w") as f:
        json.dump(cert, f, indent=2)
    return True
