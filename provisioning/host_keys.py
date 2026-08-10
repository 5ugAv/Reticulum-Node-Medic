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


#: Every cable-born node answers on the SAME address. That is the point of the
#: /29 — no network, no names, no operator input — and it is also why a stale key
#: here does not break the birth that made it, but the NEXT one.
CABLE_ADDRESS = "10.55.0.1"

#: Both files that can refuse a connection: the medic's own pinned file, and the
#: ordinary OpenSSH one that ``accept-new`` writes to behind our backs. The bug
#: on 2026-08-10 was in the SECOND: nothing in this project had ever written to
#: it deliberately, so nothing thought to clean it.
def known_hosts_files(pinned: str = PINNED_KNOWN_HOSTS) -> List[str]:
    return [pinned, os.path.expanduser("~/.ssh/known_hosts")]


def forget_host_key(host: str, paths: Optional[List[str]] = None,
                    port: int = 22, runner=None) -> int:
    """Drop every stored key for *host*. Returns how many entries went.

    THIS IS NOT "TRUST A CHANGED KEY". C1 exists because a key that changes
    under you may mean the node was swapped or is being impersonated, and that
    warning must keep its teeth. This is the one case that is provably not
    that: the medic has just written a new operating system onto the card
    itself, so the identity it pinned no longer exists anywhere. Refusing to
    talk to the node you just created is not caution, it is a dead end.

    And it was one. Y2K8's build died at its first command with "Could not read
    /proc/cpuinfo" while the Pi sat there answering perfectly — the medic held
    the previous node's key for 10.55.0.1, ``accept-new`` accepts a new host but
    NOT a changed one, and every cable birth after the first hit it. It reads
    exactly like a dead cable or a brown-out, which is where the evening went.

    ``ssh-keygen -R`` DOES THE REMOVING, not a line scan of our own. OpenSSH
    hashes known_hosts by default — the real file on the medic is all
    ``|1|...`` — so the obvious `line.split()[0] == host` test matches nothing
    and reports a confident zero. That is exactly what it did the first time
    this was written, half an hour after the same class of bug (a `test -s` on
    a directory) was fixed elsewhere. A plain-text pass still runs afterwards,
    for the un-hashed files we write ourselves.
    """
    if paths is None:
        paths = known_hosts_files()
    want = _hostpart(host, port)
    gone = 0
    for path in paths:
        try:
            before = len(open(path).read().splitlines())
        except OSError:
            continue
        if runner is None:
            import subprocess
            try:
                subprocess.run(["ssh-keygen", "-f", path, "-R", host],
                               capture_output=True, text=True, timeout=20)
            except Exception:                                  # noqa: BLE001
                pass
        else:
            runner(["ssh-keygen", "-f", path, "-R", host])
        try:
            lines = open(path).read().splitlines()
        except OSError:
            continue
        # and the plain-text form, for files we write ourselves (never hashed)
        kept = [ln for ln in lines
                if not (ln.strip() and ln.split()[0] == want)]
        if len(kept) != len(lines):
            try:
                with open(path, "w") as f:
                    f.write("\n".join(kept) + ("\n" if kept else ""))
                os.chmod(path, 0o600)
            except OSError:
                pass
        gone += max(0, before - len(kept))
    return gone


def addresses_of(name: str) -> List[str]:
    """Every IP *name* currently resolves to. Empty if it resolves to nothing.

    Used to forget a re-imaged node's keys by ADDRESS as well as by name. ssh
    keeps host keys under both, and clearing one does not clear the other.
    """
    if not name:
        return []
    out = []
    try:
        import socket
        for _f, _t, _p, _c, sa in socket.getaddrinfo(name, 22,
                                                     proto=socket.IPPROTO_TCP):
            if sa[0] and sa[0] not in out:
                out.append(sa[0])
    except Exception:                                          # noqa: BLE001
        pass
    return out


def forget_reimaged_node(hostname: str = "", paths: Optional[List[str]] = None,
                         resolver=addresses_of) -> int:
    """Forget every identity a freshly written card has just invalidated.

    The cable address always, because every node inherits it; the node's own
    name too, since a rebirth reuses that as well.

    AND THE ADDRESSES THAT NAME CURRENTLY RESOLVES TO. ssh stores a host key
    under the name AND under the address, and clearing one leaves the other.
    That is not hypothetical: a rebuilt skyfinger cleared cleanly by name and
    still failed, because the PREVIOUS node's key was sitting under
    192.168.1.2 — three entries, found only by asking what the name resolved to
    (2026-08-11). At card-write time the name usually still points at the node
    being replaced, which is exactly the one whose key must go.
    """
    hosts = [CABLE_ADDRESS]
    if hostname:
        hosts += [hostname, f"{hostname}.local"]
        for nm in (hostname, f"{hostname}.local"):
            for ip in resolver(nm):
                if ip not in hosts:
                    hosts.append(ip)
    return sum(forget_host_key(h, paths) for h in hosts)
