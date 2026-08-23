"""This Node Medic's own identity + lineage (Settings item 2, read-only display).

Holds the tool's self-knowledge:
  * its own Reticulum identity hash (read off disk via RNS — no networking, no
    clash with the running rnsd),
  * a tool name (operator-set, else the hostname),
  * a born date (when this unit first got its identity), and
  * if this unit was CLONED (MITOSIS), which parent unit it descended from.

The name/born/parent live in a small JSON store; the identity hash is derived
live from the RNS identity file. Runner + paths are injectable so it's unit-
testable off-hardware.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Callable, Dict, Optional, Tuple

import safe_shell

CONFIG = os.path.expanduser("~/.reticulum-node-medic/tool_identity.json")

#: Candidate RNS identity files (client, then transport-only).
_IDENTITY_CANDS = [
    os.path.expanduser("~/.reticulum/storage/identity"),
    os.path.expanduser("~/.reticulum/storage/transport_identity"),
]

#: Reads the identity hash straight off disk (matches workflows.build).
_HASH_CMD = (
    "python3 -c \"import RNS, os; "
    "cands=['~/.reticulum/storage/identity', "
    "'~/.reticulum/storage/transport_identity']; "
    "p=next((os.path.expanduser(x) for x in cands "
    "if os.path.exists(os.path.expanduser(x))), None); "
    "i=RNS.Identity.from_file(p) if p else None; "
    "print(RNS.hexrep(i.hash, delimit=False) if i else '')\" 2>/dev/null")

#: THIS medic's OWN identity files, in the order the audit lists them. The
#: rnsd TRANSPORT identity and the LXMD (LXMF propagation) identity are the two
#: named in the 2026-08-22 finding; the client ``identity`` is included because
#: when present it is equally the medic's own. An announce from ANY of these is
#: the medic talking to itself, never a neighbour — see ``own_identity_hashes``.
_OWN_IDENTITY_FILES = [
    os.path.expanduser("~/.reticulum/storage/transport_identity"),
    os.path.expanduser("~/.reticulum/storage/identity"),
    os.path.expanduser("~/.lxmd/identity"),
]


def own_identity_hashes(paths=None) -> set:
    """The set of THIS medic's own RNS identity hashes (full lowercase hex).

    Covers the two identities the 2026-08-22 live finding names — the rnsd
    TRANSPORT identity (``~/.reticulum/storage/transport_identity``) and the
    LXMD / LXMF-propagation identity (``~/.lxmd/identity``) — plus the client
    identity if this unit keeps one. Read straight off disk via
    ``RNS.Identity.from_file`` (no networking, no clash with the running rnsd).

    Why it exists: VITALS was showing the medic's OWN lxmd destination
    (destination 5a0a000a, identity 5a180018) as an anonymous "Propagation
    relay" neighbour. An announce whose identity is one of these is the medic
    hearing itself — it must be neither recorded as a node nor displayed. The
    hashes are resolved at runtime, never hardcoded.

    Defensive by contract: no RNS library, a missing file, or an unreadable
    identity each contribute NOTHING rather than raising — a dev box with no
    Reticulum simply yields an empty set, and an empty set filters nobody.
    """
    files = paths if paths is not None else _OWN_IDENTITY_FILES
    try:
        import RNS
    except Exception:
        return set()
    out = set()
    for p in files:
        try:
            if not p or not os.path.exists(p):
                continue
            ident = RNS.Identity.from_file(p)
            h = getattr(ident, "hash", None) if ident is not None else None
            if h is not None:
                out.add(h.hex().lower())
        except Exception:
            continue
    return out


ShellRunner = Callable[[str], Tuple[int, str]]


def _default_run(cmd: str) -> Tuple[int, str]:
    try:
        return safe_shell.run(cmd, timeout=15)      # no shell (audit C5)
    except Exception as e:
        return 1, str(e)


def identity_hash(run: Optional[ShellRunner] = None) -> str:
    """The medic's own Reticulum identity hash (hex), or "" if unavailable."""
    run = run or _default_run
    code, out = run(_HASH_CMD)
    if code != 0 or not out.strip():
        return ""
    return out.strip().splitlines()[-1].strip()


def load(path: str = CONFIG) -> Dict:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d: Dict, path: str = CONFIG) -> Dict:
    # Atomic write so a field power-cut can't truncate the tool identity (see
    # monitor.atomic_json).
    from monitor.atomic_json import write_json
    write_json(path, d, indent=2, sort_keys=True)
    return d


def tool_name(path: str = CONFIG) -> str:
    """Operator-set tool name, else the hostname, else 'Node Medic'."""
    n = load(path).get("name")
    if n:
        return str(n)
    try:
        h = socket.gethostname()
    except Exception:
        h = ""
    return h or "Node Medic"


def set_name(name: str, path: str = CONFIG) -> Dict:
    d = load(path)
    d["name"] = str(name).strip()
    return save(d, path)


def _identity_mtime() -> Optional[float]:
    for p in _IDENTITY_CANDS:
        try:
            return os.path.getmtime(p)
        except OSError:
            continue
    return None


def born(path: str = CONFIG) -> Optional[float]:
    """Epoch when this unit was born (got its identity), or None if unknown."""
    b = load(path).get("born")
    return float(b) if isinstance(b, (int, float)) else None


def ensure_born(now: float, path: str = CONFIG,
                identity_mtime: Optional[float] = "unset") -> float:
    """Stamp the born date once, if not already set. Prefers the identity file's
    mtime (when the unit first got its identity) over *now*. Idempotent."""
    d = load(path)
    if not isinstance(d.get("born"), (int, float)):
        mt = _identity_mtime() if identity_mtime == "unset" else identity_mtime
        d["born"] = float(mt) if mt else float(now)
        save(d, path)
    return float(d["born"])


def parent(path: str = CONFIG) -> Optional[Dict]:
    """The parent unit this was cloned from ({'hash','name','via'}), or None if
    this is an original (not a clone)."""
    p = load(path).get("parent")
    return p if isinstance(p, dict) else None


def set_parent(parent_hash: str, parent_name: str, via: str = "cloned from this unit",
               path: str = CONFIG) -> Dict:
    """Record the parent unit (called on a CLONE during MITOSIS)."""
    d = load(path)
    d["parent"] = {"hash": parent_hash, "name": parent_name, "via": via}
    return save(d, path)


def summary(run: Optional[ShellRunner] = None, path: str = CONFIG) -> Dict:
    """Everything the Tool-identity screen shows, in one call."""
    return {
        "identity_hash": identity_hash(run),
        "name": tool_name(path),
        "born": born(path),
        "parent": parent(path),
    }
