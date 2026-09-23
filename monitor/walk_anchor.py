"""The boundary walk's anchor, locked per node (operator, 2026-09-23).

Mid-walk on the roof node the operator backed out of the walk by accident
and could not start again: "I'm not next to the node to set the GPS
coordinates where I started." The anchor — WHERE THE OPERATOR STOOD when
they pressed start, a live fix, never the registry's remembered node
position (the 2026-09-21 rule: fuzzed and old positions lie) — is now kept
on disk per node, so a second walk against the same node can begin from
anywhere, measured from the same spot.

One JSON file, ``~/.reticulum-node-medic/walk_anchors.json``: a map of
node key -> ``{lat, lon, at, name}``. Written atomically (a power cut
mid-write leaves the old file, never half a new one); read tolerantly (a
corrupt file reads as empty, never raises — the operator is standing
outside in the weather).

The record carries the node's NAME as well as its key because the two
doors into a walk do not key one device identically: VITALS rewrites the
key to ``probe_hash_for`` (the first hex destination), ANTENNA's picker
keeps the freshest destination (``walkable_nodes``). A T114 has sat in the
registry under four (2026-09-19). ``load_anchor`` therefore matches by key
first and by case-folded name second — the same rule ``latest_diagnosis``
already uses for the walk's verdicts.

Pure: injected clock nowhere needed (``at`` is handed in), file path
injectable, no Kivy.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional

_WALK_DIR = "~/.reticulum-node-medic"
_ANCHOR_FILE = "walk_anchors.json"


def _path(base_dir: str) -> str:
    return os.path.join(os.path.expanduser(base_dir), _ANCHOR_FILE)


def _read_all(base_dir: str) -> Dict[str, dict]:
    """Every anchor on disk. Missing, unreadable or corrupt -> ``{}``."""
    try:
        with open(_path(base_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, dict] = {}
    for key, rec in data.items():
        if not isinstance(key, str) or not isinstance(rec, dict):
            continue
        try:
            lat, lon = float(rec["lat"]), float(rec["lon"])
            at = float(rec["at"])
        except (KeyError, TypeError, ValueError):
            continue                      # one bad record, not a lost file
        out[key] = {"lat": lat, "lon": lon, "at": at,
                    "name": str(rec.get("name") or "")}
    return out


def _write_all(anchors: Dict[str, dict], base_dir: str) -> None:
    """Atomic: write beside, fsync, rename over. A half-written file would
    read as corrupt and forget EVERY node's anchor at once."""
    base = os.path.expanduser(base_dir)
    os.makedirs(base, exist_ok=True)
    final = _path(base_dir)
    tmp = final + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(anchors, fh, sort_keys=True, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)


def _fold(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def save_anchor(node_key: str, lat: float, lon: float, at: float,
                name: str = "", base_dir: str = _WALK_DIR) -> dict:
    """Lock *node_key*'s anchor at (*lat*, *lon*), stood on at *at* (epoch
    seconds). Replaces any earlier anchor for the key — and any earlier
    anchor for the same NAME under another key, so a node re-keyed by the
    other door does not keep two."""
    node_key = (node_key or "").strip()
    if not node_key:
        raise ValueError("an anchor needs a node key")
    anchors = _read_all(base_dir)
    folded = _fold(name)
    if folded:
        for k in [k for k, r in anchors.items()
                  if k != node_key and _fold(r.get("name")) == folded]:
            del anchors[k]
    rec = {"lat": float(lat), "lon": float(lon), "at": float(at),
           "name": name or ""}
    anchors[node_key] = rec
    _write_all(anchors, base_dir)
    return dict(rec)


def load_anchor(node_key: str, name: str = "",
                base_dir: str = _WALK_DIR) -> Optional[dict]:
    """``{lat, lon, at, name}`` for the node, or None. Key first; then the
    case-folded name, newest wins (see the module docstring for why a
    name is an acceptable second road here)."""
    anchors = _read_all(base_dir)
    rec = anchors.get((node_key or "").strip())
    if rec is not None:
        return dict(rec)
    folded = _fold(name)
    if not folded:
        return None
    best = None
    for r in anchors.values():
        if _fold(r.get("name")) == folded and (best is None or r["at"] > best["at"]):
            best = r
    return dict(best) if best is not None else None


def forget_anchor(node_key: str = "", name: str = "",
                  base_dir: str = _WALK_DIR) -> int:
    """Drop every anchor matching the key or the case-folded name. Returns
    how many went; 0 is an honest answer for an unknown node, never an
    error — and nothing is written when nothing matched."""
    anchors = _read_all(base_dir)
    key = (node_key or "").strip()
    folded = _fold(name)
    doomed = [k for k, r in anchors.items()
              if (key and k == key) or (folded and _fold(r.get("name")) == folded)]
    for k in doomed:
        del anchors[k]
    if doomed:
        _write_all(anchors, base_dir)
    return len(doomed)


def anchor_stamp(at: float) -> str:
    """The saved-on date as the screen shows it — local time, to the
    minute. One place, so the gate button and the HUD cannot disagree."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(at)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"
