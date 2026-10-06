"""This medic's EEPROM signing key — the thing that lets it NAME a board.

``rnodeconf -r`` (and every provisioning road) signs the board's EEPROM with
the local signing key at ``~/.config/rnodeconf/firmware/signing.key``. A
fresh clone has none: its parent's key rightly never travels (each medic
signs its own boards), so the clone's first naming stopped at "No signing
key found" — the Tracker sat wiped, red square pulsing (Node Medic 2,
2026-10-06). The tool makes one with ``rnodeconf -k``, non-interactively.

Kin trust: a board signed by the parent validates on the child only if the
child trusts the parent's PUBLIC key. ``rnodeconf --trust-key <hex>`` writes
``trusted_keys/<sha256 of DER public bytes>.pubkey``; the clone writes that
same file for its child from the parent's private key, so the parent's
boards read as trusted on the clone.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional, Tuple

SIGNING_KEY = "~/.config/rnodeconf/firmware/signing.key"
TRUSTED_KEYS_DIR = "~/.config/rnodeconf/trusted_keys"


def has_signing_key(connection) -> bool:
    return connection.run(f"test -f {SIGNING_KEY}")[0] == 0


def ensure_signing_key(connection) -> Tuple[bool, str]:
    """Make this medic's signing key if it has none. Returns ``(ok, note)``;
    the note is "" when the key was already there."""
    if has_signing_key(connection):
        return True, ""
    code, out, err = connection.run(
        f"mkdir -p {os.path.dirname(SIGNING_KEY)} && rnodeconf -k", timeout=120)
    if has_signing_key(connection):
        return True, "Made this medic's own signing key (first time only)."
    tail = " ".join(((out or "") + (err or "")).split())[-200:]
    return False, ("This medic could not make its signing key, so it cannot name "
                   f"a board yet: {tail}")


def public_key_file(private_key_path: str = SIGNING_KEY) -> Optional[Tuple[str, bytes]]:
    """``(filename, DER public bytes)`` for the trusted_keys entry that
    ``rnodeconf --trust-key`` would write for THIS medic's key — or None when
    there is no key (a medic that never named a board)."""
    path = os.path.expanduser(private_key_path)
    if not os.path.isfile(path):
        return None
    from cryptography.hazmat.primitives import serialization
    with open(path, "rb") as fh:
        private = serialization.load_der_private_key(fh.read(), password=None)
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(public_bytes).hexdigest() + ".pubkey", public_bytes
