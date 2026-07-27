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
    # O_CREAT with mode 0600 so the secret is never briefly world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key
