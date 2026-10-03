"""Is this node name already taken by something in the family?

Two nodes with the same name is not a cosmetic problem. The name is what the
operator sees on the SCAN map, in VITALS, on the birth certificate, and in a
repair conversation months later when the person who built it has moved away —
which is the whole point of the thing (see the founding principle: networks
outlast their builders). Two "solarlove"s on a map send a repair crew to the
wrong roof.

The family is TWO registers, and a name can be taken in either:

* **certificates** — every node this medic has birthed (``ui.cert_store``);
* **the kin roster** — every node the medic knows, which includes nodes
  *adopted* rather than built here (``monitor.kin_roster``).

Checking only the certificates would miss exactly the nodes someone else built,
which are the ones a name clash is most likely to confuse.

This module only REPORTS. Deciding what to do about a clash belongs to the
screen — the operator is allowed to reuse a name deliberately (rebuilding a node
that died, keeping its place on the map), and a tool that refuses would be
wrong. See ``ui/screens/birth_guide_screen.py``.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional


def _norm(name) -> str:
    """Names compare case- and space-insensitively. "Solar Love", "solarlove"
    and "SOLARLOVE" are the same name to a human reading a map, so they are the
    same name here."""
    return "".join(str(name or "").split()).lower()


def existing_names(cert_dir: Optional[str] = None,
                   roster_path: Optional[str] = None) -> Dict[str, List[str]]:
    """``{normalised name: [where it is used, …]}`` across both registers.

    Never raises: a missing or unreadable register means "nothing known from
    that source", not a failed birth. A name check must not be able to block
    the walkthrough it is only advising."""
    found: Dict[str, List[str]] = {}

    def _add(name, where):
        key = _norm(name)
        if not key:
            return
        found.setdefault(key, [])
        if where not in found[key]:
            found[key].append(where)

    try:
        from ui.cert_store import load_certs
        certs = load_certs(cert_dir) if cert_dir else load_certs()
        for c in certs:
            _add(c.get("node_name") or c.get("hostname"), "born here")
    except Exception:
        pass

    try:
        from monitor.kin_roster import load_roster
        roster = load_roster(roster_path) if roster_path else load_roster()
        for entry in (roster or {}).values():
            if isinstance(entry, dict):
                _add(entry.get("name"), "on the map")
    except Exception:
        pass

    return found


def is_hash_tail_name(name) -> bool:
    """True when *name* is a board's OWN hash tail — the four hex digits an
    RNode prints on its screen (5A59, 5AC3). That is an identity, not a
    choice: a board rebirthed under it is the same board under the same
    name, and bumping it to 5A60 made a node whose screen and certificate
    disagreed (operator, 2026-10-03)."""
    return bool(re.fullmatch(r"[0-9A-Fa-f]{4}", str(name or "").strip()))


def clash(name, cert_dir: Optional[str] = None,
          roster_path: Optional[str] = None) -> str:
    """A sentence naming the collision, or "" if the name is free.

    Phrased as a fact plus a consequence, not a scolding — the operator may
    well mean it."""
    key = _norm(name)
    if not key:
        return ""
    where = existing_names(cert_dir, roster_path).get(key)
    if not where:
        return ""
    return (f"There is already a node called “{str(name).strip()}” "
            f"({' and '.join(where)}). Two nodes with one name are hard to tell "
            f"apart on the map and in a repair months from now.")
