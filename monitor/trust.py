"""Trusted operators — trust between cloned Node Medic units.

When you clone this medic to a friend, their unit births its own nodes. Those
nodes should appear as kin/kindred on YOUR VITALS/SCAN only while you trust their
unit. Trust is:

  * PER-UNIT and EXPLICIT — granted to one unit's identity hash, never inferred.
  * NON-TRANSITIVE — a clone-of-a-clone (a unit your friend cloned onward to a
    stranger) is NOT trusted just because its parent is. It shows as
    "untrusted — descended from [friend's unit]" and needs manual approval.
  * REVOCABLE — revoking a unit demotes its birthed nodes from kin to neighbour.

This module is the pure trust store + decisions (no Kivy); the Settings screen and
the registry read it. ``is_trusted`` NEVER walks the parent chain — that's what
keeps trust non-transitive.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

CONFIG = os.path.expanduser("~/.reticulum-node-medic/trust.json")


def load(path: str = CONFIG) -> Dict:
    try:
        with open(path) as f:
            d = json.load(f)
        units = d.get("units") if isinstance(d, dict) else None
        return {"units": units if isinstance(units, dict) else {}}
    except (OSError, ValueError):
        return {"units": {}}


def save(store: Dict, path: str = CONFIG) -> Dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(store, f, indent=2, sort_keys=True)
    return store


def _now(now: Optional[float]) -> float:
    return now if now is not None else time.time()


def set_self(unit_hash: str, name: str, parent: Optional[str] = None,
             now: Optional[float] = None, path: str = CONFIG) -> Dict:
    """Register THIS medic's own unit — always trusted, flagged self. Idempotent."""
    store = load(path)
    u = store["units"].get(unit_hash, {})
    u.update({"name": name or u.get("name") or "This Node Medic",
              "parent": parent if parent is not None else u.get("parent"),
              "via": "this unit", "trusted": True, "self": True,
              "established_at": u.get("established_at") or _now(now)})
    store["units"][unit_hash] = u
    return save(store, path)


def record_child_clone(unit_hash: str, name: str, parent_hash: str,
                       now: Optional[float] = None, path: str = CONFIG) -> Dict:
    """A DIRECT clone this medic made — trusted (you made it), via 'cloned from
    this unit'. Its own future clones are NOT covered (non-transitive)."""
    store = load(path)
    u = store["units"].get(unit_hash, {})
    u.update({"name": name or u.get("name") or unit_hash[:12],
              "parent": parent_hash, "via": "cloned from this unit",
              "trusted": True, "established_at": u.get("established_at") or _now(now)})
    store["units"][unit_hash] = u
    return save(store, path)


def note_descendant(unit_hash: str, name: str, parent_hash: str,
                    path: str = CONFIG) -> Dict:
    """A DISCOVERED unit descended from a known one — recorded UNTRUSTED by default
    (awaiting manual approval). No-op if already known (won't downgrade)."""
    store = load(path)
    if unit_hash in store["units"]:
        return store
    store["units"][unit_hash] = {
        "name": name or unit_hash[:12], "parent": parent_hash,
        "via": "descended from a trusted unit", "trusted": False}
    return save(store, path)


def trust(unit_hash: str, now: Optional[float] = None, path: str = CONFIG) -> Dict:
    """Manually grant trust to a known unit (approve a descendant)."""
    store = load(path)
    u = store["units"].get(unit_hash)
    if u is None:
        return store
    u["trusted"] = True
    if u.get("via", "").startswith("descended"):
        u["via"] = "manually trusted"
    u.setdefault("established_at", _now(now))
    return save(store, path)


def revoke(unit_hash: str, path: str = CONFIG) -> Dict:
    """Revoke trust — its birthed nodes drop from kin to neighbour. Never the self
    unit. The record stays (shown as untrusted)."""
    store = load(path)
    u = store["units"].get(unit_hash)
    if u is None or u.get("self"):
        return store
    u["trusted"] = False
    return save(store, path)


def is_trusted(unit_hash: Optional[str], path: str = CONFIG) -> bool:
    """Is this exact unit trusted? NEVER walks the parent chain (non-transitive)."""
    if not unit_hash:
        return False
    return bool(load(path)["units"].get(unit_hash, {}).get("trusted"))


def classify(unit_hash: str, path: str = CONFIG) -> str:
    """'self' | 'trusted' | 'untrusted' | 'unknown'."""
    u = load(path)["units"].get(unit_hash)
    if u is None:
        return "unknown"
    if u.get("self"):
        return "self"
    return "trusted" if u.get("trusted") else "untrusted"


def node_provenance(builder_hash: Optional[str], path: str = CONFIG) -> str:
    """A node's kin/neighbour status FROM its birthing unit's trust: 'kin' if that
    unit is trusted (incl. this medic's own), else 'neighbour'. Revoking the unit
    flips its nodes to neighbour."""
    return "kin" if is_trusted(builder_hash, path) else "neighbour"


def units(path: str = CONFIG) -> List[Dict]:
    """All known units for the Settings family-tree list, each with its computed
    status and its parent's display name. Self first, then trusted, then untrusted."""
    store = load(path)["units"]
    out = []
    for h, u in store.items():
        parent_h = u.get("parent")
        out.append({
            "hash": h,
            "name": u.get("name") or h[:12],
            "parent": parent_h,
            "parent_name": (store.get(parent_h, {}).get("name") if parent_h else None),
            "via": u.get("via", ""),
            "established_at": u.get("established_at"),
            "status": ("self" if u.get("self")
                       else "trusted" if u.get("trusted") else "untrusted"),
        })
    rank = {"self": 0, "trusted": 1, "untrusted": 2}
    out.sort(key=lambda u: (rank.get(u["status"], 3), (u["name"] or "").lower()))
    return out
