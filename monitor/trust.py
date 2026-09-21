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
import logging
import os
import time
from typing import Dict, List, Optional

from monitor import trust_integrity

CONFIG = os.path.expanduser("~/.reticulum-node-medic/trust.json")

log = logging.getLogger(__name__)


def _key_path(path: str) -> str:
    """The per-medic HMAC key, kept beside the trust store (so the default store
    lands the key at ``~/.reticulum-node-medic/trust_hmac_key`` and tests using a
    tmp store keep their key in the same tmp dir — never the real home)."""
    return os.path.join(os.path.dirname(path) or ".", "trust_hmac_key")


def _sig_path(path: str) -> str:
    return path + ".sig"


#: JSON field carrying the store's own HMAC, folded INTO the store file.
_SIG_FIELD = "_integrity"


def _sign_store(store: Dict, key: bytes) -> str:
    """HMAC over the trust payload (the ``{"units": ...}`` content, NEVER
    including the ``_integrity`` field itself — that would be circular)."""
    return trust_integrity.sign(key, trust_integrity.canonical_bytes(store))


def load(path: str = CONFIG) -> Dict:
    """Load the trust store, verifying its HMAC integrity sidecar (audit C8).

    On a TAMPERED or unverifiable store, log a warning and return an EMPTY,
    all-untrusted store rather than honouring possibly-forged records. A legacy
    store with no sidecar (first run after this change) is accepted ONCE and
    immediately re-signed (migration). Never raises."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        d = json.loads(raw)
    except (OSError, ValueError):
        return {"units": {}}

    key = trust_integrity.load_or_create_key(_key_path(path))

    if isinstance(d, dict) and _SIG_FIELD in d:
        # AUTHORITATIVE PATH: the signature is folded into the one store file
        # (written atomically), so store and sig can never be a mismatched pair —
        # the failure that used to un-kin the whole fleet when a power cut landed
        # between the store write and its separate sidecar write.
        signature = d.get(_SIG_FIELD)
        payload = {k: v for k, v in d.items() if k != _SIG_FIELD}
        if not isinstance(signature, str) or not trust_integrity.verify(
                key, trust_integrity.canonical_bytes(payload), signature):
            log.warning("trust store %s failed integrity verification "
                        "(tampered?); ignoring stored trust and treating all "
                        "units as untrusted.", path)
            return {"units": {}}
        units = payload.get("units")
        return {"units": units if isinstance(units, dict) else {}}

    # LEGACY PATH: an older store with a detached .sig sidecar (or none at all).
    canonical = trust_integrity.canonical_bytes(d)
    try:
        with open(_sig_path(path)) as f:
            signature = f.read().strip()
    except OSError:
        signature = None

    if signature is None:
        # MIGRATION: pre-integrity store — accept once, then re-save it in the
        # folded, crash-safe format (store + embedded sig, written atomically).
        units = d.get("units") if isinstance(d, dict) else None
        migrated = {"units": units if isinstance(units, dict) else {}}
        save(migrated, path)
        log.warning("trust store %s had no integrity signature; migrated to the "
                    "folded, crash-safe format.", path)
        return migrated
    elif not trust_integrity.verify(key, canonical, signature):
        log.warning("trust store %s failed integrity verification (tampered?); "
                    "ignoring stored trust and treating all units as untrusted.",
                    path)
        return {"units": {}}

    units = d.get("units") if isinstance(d, dict) else None
    return {"units": units if isinstance(units, dict) else {}}


def save(store: Dict, path: str = CONFIG) -> Dict:
    """Persist the trust store with its HMAC integrity FOLDED IN (audit C8).

    A field power-cut used to be able to un-kin the whole fleet: the old save
    wrote trust.json, then loaded the key, then wrote trust.json.sig as a
    SEPARATE file — three windows in which a crash left a new store paired with
    an old/absent signature, which fails verification on next boot and drops the
    store to ``{"units": {}}`` (every trusted unit silently un-kinned).

    The fix removes the ordering entirely: the signature is computed FIRST, then
    embedded in the store dict (``_integrity`` field) and the whole thing written
    with ONE atomic ``write_json``. A power cut leaves either the complete old
    file or the complete new one — never a mismatched store/sig pair. The
    detached ``.sig`` sidecar is still written (atomically, best-effort) for
    backward compatibility, but ``load`` treats the folded field as the
    authority, so a torn sidecar can no longer un-kin anyone."""
    from monitor.atomic_json import write_json, write_text
    key = trust_integrity.load_or_create_key(_key_path(path))
    signature = _sign_store(store, key)           # sign FIRST, before any write
    folded = {**store, _SIG_FIELD: signature}
    # Trust store at 0600 — its folded signature makes it security-relevant.
    write_json(path, folded, indent=2, sort_keys=True, mode=0o600)
    # Compat sidecar (not the authority for the NEW load). It signs the FULL
    # folded dict — INCLUDING _integrity — precisely so a ROLLBACK to old code is
    # safe: the old load() computes canonical_bytes over the whole parsed dict
    # (which now contains _integrity) and checks it against this sidecar, so its
    # verification PASSES and the fleet stays kinned. Signing only the payload
    # here would guarantee a mismatch under old code and un-kin everyone.
    sidecar_sig = trust_integrity.sign(key, trust_integrity.canonical_bytes(folded))
    write_text(_sig_path(path), sidecar_sig)
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
    u["revoked_at"] = _now(None)
    return save(store, path)


def forget(unit_hash: str, path: str = CONFIG) -> Dict:
    """Remove a unit's record entirely — for a clone that no longer exists (a
    test card written over). Never the self unit. Forgetting is not trusting:
    a forgotten unit heard again comes back as an untrusted descendant needing
    approval (operator, 2026-09-21: revoking left four dead clones on the
    list with only an Approve button)."""
    store = load(path)
    u = store["units"].get(unit_hash)
    if u is None or u.get("self"):
        return store
    del store["units"][unit_hash]
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
            "revoked": bool(u.get("revoked_at")),
            "status": ("self" if u.get("self")
                       else "trusted" if u.get("trusted") else "untrusted"),
        })
    rank = {"self": 0, "trusted": 1, "untrusted": 2}
    out.sort(key=lambda u: (rank.get(u["status"], 3), (u["name"] or "").lower()))
    return out
