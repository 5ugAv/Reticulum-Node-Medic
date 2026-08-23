"""Keyed-HMAC integrity for the operator trust store (audit C8).

The trust store (``monitor.trust``) is plain JSON that any process on the medic
can rewrite — a hostile edit could silently grant trust to a stranger's unit.
This module adds tamper DETECTION (not prevention): every save also writes a
sidecar HMAC-SHA256 signature over a canonical serialisation of the store, keyed
by a per-medic secret. On load the HMAC is recomputed and compared in constant
time; a mismatch means the file was changed by something that didn't hold the
key, and the caller falls back to an empty, all-untrusted store rather than
honouring possibly-forged records.

Pure + injectable: ``sign`` / ``verify`` take the raw key + bytes so they are
unit-testable without the real home directory. ``canonical_bytes`` fixes ONE
serialisation (``sort_keys`` + tight separators) used for both signing and
verifying, so the digest is stable regardless of key order or whitespace.
"""

from __future__ import annotations

import hmac
import json
import os
from hashlib import sha256

#: Secret-key length in bytes.
KEY_SIZE = 32


def canonical_bytes(obj) -> bytes:
    """Deterministic serialisation used for signing AND verifying.

    Order- and whitespace-invariant so a re-read of the same logical object
    hashes identically to what was signed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(key: bytes, data: bytes) -> str:
    """Hex HMAC-SHA256 of *data* under *key*."""
    return hmac.new(key, data, sha256).hexdigest()


def verify(key: bytes, data: bytes, signature: str) -> bool:
    """Constant-time check that *signature* matches ``sign(key, data)``.

    Uses ``hmac.compare_digest`` (never plain equality) so a bad signature can't
    be distinguished by timing."""
    expected = sign(key, data)
    return hmac.compare_digest(expected, (signature or "").strip())


def load_or_create_key(path: str) -> bytes:
    """Load the per-medic secret key at *path*, creating it once if absent.

    The key is 32 random bytes (``os.urandom``); the file is written 0600 and its
    parent directory tightened to 0700. Returns the key bytes."""
    try:
        with open(path, "rb") as f:
            key = f.read()
        if len(key) >= KEY_SIZE:
            return key
    except OSError:
        pass

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass

    key = os.urandom(KEY_SIZE)
    # Write the secret ATOMICALLY (temp + fsync + os.replace, mode 0600). The old
    # O_CREAT|O_TRUNC + os.write truncated first: a power cut on this field device
    # between truncate and the 32-byte write left a SHORT key, and the next load
    # (len < KEY_SIZE) would silently mint a BRAND-NEW key — under which every
    # existing trust-store signature fails to verify and the whole fleet reads as
    # un-kinned. The atomic swap means the key file is only ever the full old key
    # or the full new one, never a truncated stub. (2026-08-23 field audit.)
    from monitor.atomic_json import write_bytes
    write_bytes(path, key, mode=0o600)
    return key
