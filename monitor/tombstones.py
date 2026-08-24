"""Tombstones for deleted node identities — the one shared reader/writer.

A forgotten node's hashes are tombstoned for the RNS path table's own 7-day
lifetime. Written by the operator's Forget action and by BIRTH when a rebirth
retires its predecessor's identities; read by the map (to keep stale rnpath
rows from resurrecting a ghost) and by the registry's ingest paths.

SEMANTICS (changed 2026-08-25, operator request): within its 7-day life a
tombstone suppresses EVERY ingest of that hash — announces included. The mesh
replays a dead identity's cached announces for days (transport nodes re-emit
them), and post-delete the replay guard has no baseline left, so "announce
heard" cannot distinguish a replay from a live node here. The trade is
explicit: a genuinely live node forgotten by mistake stays invisible for at
most 7 days and then honestly returns; a dead identity (the normal case — a
re-imaged machine's old keys can never speak again) stays gone forever.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Iterable

TOMBSTONE_PATH = os.path.expanduser("~/.reticulum-node-medic/forgotten.json")

#: The RNS path table forgets a destination after 7 days; so do we.
LIFETIME_S = 7 * 86400


def load(path: str = TOMBSTONE_PATH,
         now: float = None) -> Dict[str, float]:
    """Unexpired tombstones as {hash: buried_at_epoch}. Missing/corrupt file
    -> {} (never a raise — the caller is an ingest path)."""
    t = time.time() if now is None else now
    try:
        with open(path) as f:
            tombs = json.load(f)
        if not isinstance(tombs, dict):
            return {}
        return {str(h): float(ts) for h, ts in tombs.items()
                if isinstance(ts, (int, float)) and t - ts < LIFETIME_S}
    except Exception:                                          # noqa: BLE001
        return {}


def bury(hashes: Iterable[str], path: str = TOMBSTONE_PATH,
         now: float = None) -> Dict[str, float]:
    """Add *hashes* to the tombstone file (trimming expired ones) and return
    the resulting live set."""
    t = time.time() if now is None else now
    tombs = load(path, now=t)
    for h in hashes:
        h = str(h).strip()
        if h:
            tombs[h] = t
    try:
        from monitor.atomic_json import write_json
        write_json(path, tombs)
    except Exception:                                          # noqa: BLE001
        try:
            with open(path, "w") as f:
                json.dump(tombs, f)
        except Exception:
            pass
    return tombs
