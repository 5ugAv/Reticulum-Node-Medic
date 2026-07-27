"""SSH host-key pinning for medic -> node connections (audit C1).

The medic historically connected to nodes with ``StrictHostKeyChecking=accept-new``
— trust-on-first-use: whatever key the node presents on first contact is trusted
forever, and a later swap (MITM, a tampered node between visits) is never noticed.

This module lets the medic PIN a node's host key when trust is first established
(at birth/bootstrap) into its own known_hosts file, so subsequent connections can
VERIFY it (``StrictHostKeyChecking=yes``) and refuse a changed key. It also computes
the human-readable SHA256 fingerprint to show the operator at first contact.

Everything here is PURE (parsing / formatting / fingerprint) except the small file
writer and the ssh-keyscan argv builder — capture itself runs through an injected
runner, so it's unit-testable without a network.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import List, Optional, Tuple

#: The medic's own pinned known_hosts — separate from ~/.ssh/known_hosts so pins are
#: explicit and under our control.
PINNED_KNOWN_HOSTS = os.path.expanduser("~/.reticulum-node-medic/known_hosts")

#: Key types we accept, best first (ed25519 preferred — modern, short).
_KEY_PREFIXES = ("ssh-ed25519", "sk-ssh-ed25519", "ecdsa-", "sk-ecdsa-", "ssh-rsa")


#: OpenSSH's stderr when a pinned host presents a DIFFERENT key than known_hosts —
#: i.e. the key CHANGED since we pinned it. That's the signal C1 exists to catch.
_CHANGED_MARKERS = (
    "REMOTE HOST IDENTIFICATION HAS CHANGED",
    "Host key verification failed",
    "POSSIBLE DNS SPOOFING",
    "WARNING: POSSIBLE DNS SPOOFING",
)

#: Operator-facing message when a pinned key mismatches (no emoji — the Pi font
#: renders those as tofu).
TAMPER_HINT = ("SECURITY WARNING: this node's SSH host key has CHANGED since it was "
               "pinned. That can mean the node was tampered with or something is "
               "impersonating it. Do NOT trust it until you've checked the node in "
               "person; only then re-pin it.")


def host_key_changed(text: str) -> bool:
    """True if SSH output/stderr indicates a pinned host key MISMATCH (possible
    tamper / MITM) — as opposed to a plain unreachable/auth failure."""
    if not text:
        return False
    up = text.upper()
    return any(m.upper() in up for m in _CHANGED_MARKERS)


def _hostpart(host: str, port: int = 22) -> str:
    """known_hosts host field — bare host on port 22, else the ``[host]:port`` form."""
    return host if port == 22 else f"[{host}]:{port}"


def key_blob(line: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract ``(keytype, base64_blob)`` from an ssh-keyscan / known_hosts line
    (with or without a leading host field), or ``(None, None)``."""
    parts = line.split()
    for i, tok in enumerate(parts):
        if tok.startswith(_KEY_PREFIXES) and i + 1 < len(parts):
            return tok, parts[i + 1]
    return None, None


def fingerprint(line: str) -> Optional[str]:
    """The OpenSSH ``SHA256:...`` fingerprint of a key line (matches
    ``ssh-keygen -lf``), or None if the line has no parseable key."""
    _keytype, blob = key_blob(line)
    if not blob:
        return None
    try:
        raw = base64.b64decode(blob)
    except Exception:
        return None
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def matches(line_a: str, line_b: str) -> bool:
    """True if both lines carry the SAME key (type + blob), ignoring host/comment."""
    ta, ba = key_blob(line_a)
    tb, bb = key_blob(line_b)
    return bool(ba) and ta == tb and ba == bb


def pick_key(scan_output: str) -> Optional[str]:
    """From ssh-keyscan output (several key lines) pick the best single key line,
    preferring ed25519. Ignores comments/blank lines. None if nothing parseable."""
    lines = [ln for ln in scan_output.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    for prefix in _KEY_PREFIXES:
        for ln in lines:
            keytype, blob = key_blob(ln)
            if keytype and blob and keytype.startswith(prefix):
                return ln
    return None


def known_hosts_entry(host: str, line: str, port: int = 22) -> Optional[str]:
    """A canonical ``<host> <keytype> <blob>`` known_hosts line for *host*, or None."""
    keytype, blob = key_blob(line)
    if not blob:
        return None
    return f"{_hostpart(host, port)} {keytype} {blob}"


def scan_argv(host: str, port: int = 22) -> List[str]:
    """argv to capture a host's key (run via the injected runner). ``-T 5`` bounds
    the wait; ssh-keyscan prints key lines to stdout."""
    return ["ssh-keyscan", "-T", "5", "-p", str(port), host]


def is_pinned(host: str, path: str = PINNED_KNOWN_HOSTS, port: int = 22) -> bool:
    """True if *host* already has a pinned entry in the known_hosts *path*."""
    want = _hostpart(host, port)
    try:
        with open(path) as f:
            for ln in f:
                if ln.strip() and ln.split()[0] == want:
                    return True
    except OSError:
        pass
    return False


def pinned_line(host: str, path: str = PINNED_KNOWN_HOSTS, port: int = 22) -> Optional[str]:
    """The pinned known_hosts line for *host*, or None."""
    want = _hostpart(host, port)
    try:
        with open(path) as f:
            for ln in f:
                if ln.strip() and ln.split()[0] == want:
                    return ln.strip()
    except OSError:
        pass
    return None


def write_known_hosts(host: str, line: str, path: str = PINNED_KNOWN_HOSTS,
                      port: int = 22) -> bool:
    """PIN *host*'s key into the known_hosts *path*, replacing any prior entry for
    that host. Returns True on success. File is 0600."""
    entry = known_hosts_entry(host, line, port)
    if not entry:
        return False
    want = entry.split()[0]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    kept: List[str] = []
    try:
        with open(path) as f:
            kept = [ln for ln in f.read().splitlines()
                    if ln.strip() and ln.split()[0] != want]
    except OSError:
        pass
    kept.append(entry)
    try:
        with open(path, "w") as f:
            f.write("\n".join(kept) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        return False
    return True
