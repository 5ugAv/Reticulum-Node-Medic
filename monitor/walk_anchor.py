"""The boundary walk's anchor, locked per node (operator, 2026-09-23).

Mid-walk on the roof node the operator backed out of the walk by accident
and could not start again: "I'm not next to the node to set the GPS
coordinates where I started." The anchor — WHERE THE OPERATOR STOOD when
they pressed start, a live fix, never the registry's remembered node
position (the 2026-09-21 rule: fuzzed and old positions lie) — is now kept
on disk per node, so a second walk against the same node can begin from
anywhere, measured from the same spot.

One JSON file, ``~/.reticulum-node-medic/walk_anchors.json``: a map of
node key -> ``{lat, lon, at, sats, accuracy_m}``. Written atomically (a
power cut mid-write leaves the old file, never half a new one); read
tolerantly (a corrupt file reads as empty, never raises — the operator is
standing outside in the weather).

KEYED BY THE DEVICE, NEVER BY A NAME (review, 2026-09-23). The first cut
matched a case-folded name as a second road because the two doors into a
walk key one device differently (VITALS by ``probe_hash_for``, ANTENNA's
picker by the freshest destination; a T114 has sat under four). But the
registry's own law (monitor/registry.py, "NO NAME FOLD ACROSS
IDENTITIES", 2026-08-22) is that the operator reuses names across
DIFFERENT boards — a spare birthed "A2" after the first "A2" was deployed
— so a name road would have measured the new A2's first walk from the old
A2's doorstep. The key is ``anchor_key``: the roster's device id, else the
announced identity, else the destination; and ``load_anchor`` takes every
key the device has ever been known by (``candidate_keys``, read off the
registry's one device fold) so both doors find the same anchor without a
name anywhere in the file.

Pure: the clock is handed in (``at``), the file path is injectable, no Kivy.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List, Optional, Sequence, Union

from monitor.geo import valid_position

_WALK_DIR = "~/.reticulum-node-medic"
_ANCHOR_FILE = "walk_anchors.json"


def _dir(base_dir: Optional[str]) -> str:
    """Resolved at CALL time, not at def time, so a test (or a deploy) can
    point the whole module elsewhere by rebinding ``_WALK_DIR``."""
    return base_dir or _WALK_DIR


def _path(base_dir: Optional[str] = None) -> str:
    return os.path.join(os.path.expanduser(_dir(base_dir)), _ANCHOR_FILE)


# -- keys ------------------------------------------------------------------------

def anchor_key(rec) -> str:
    """The DEVICE a record belongs to: ``device_id`` (what birth saw with its
    own hands), else ``identity_hash`` (what it announces), else
    ``dst_hash``. Empty string for a record with none — the caller refuses
    to save under an empty key (save_anchor raises)."""
    for attr in ("device_id", "identity_hash", "dst_hash"):
        v = getattr(rec, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _keys_of(rec) -> List[str]:
    out = []
    for attr in ("device_id", "identity_hash", "dst_hash"):
        v = getattr(rec, attr, None)
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return out


def candidate_keys(rec, registry=None, now: float = 0.0) -> List[str]:
    """Every key the record's device has been known by, primary first: the
    record's own three, then every ``dst_hash`` / ``identity_hash`` /
    ``device_id`` of every member of its device group in
    ``registry.consolidated_records`` — the ONE fold every screen shares
    (2026-09-23), so the VITALS door and the ANTENNA door, keying the same
    machine by different destinations, land on one anchor. A registry that
    cannot answer (None, raises) leaves the record's own keys; an anchor is
    then still found by whichever of them it was saved under."""
    primary = anchor_key(rec)
    keys = [primary] if primary else []
    for k in _keys_of(rec):
        if k not in keys:
            keys.append(k)
    own = set(keys)
    if registry is None or not own:
        return keys
    try:
        groups = registry.consolidated_records(now)
    except Exception:                                              # noqa: BLE001
        return keys
    for _consolidated, members in groups:
        member_keys = []
        for m in members:
            member_keys.extend(_keys_of(m))
        if own & set(member_keys):
            for k in member_keys:
                if k not in keys:
                    keys.append(k)
            break
    return keys


# -- the file --------------------------------------------------------------------

def _clean(rec) -> Optional[dict]:
    """One record as stored, or None if it is not one the walk may trust:
    lat/lon/at must parse and the position must be somewhere on Earth
    (monitor.geo.valid_position — NaN, inf, (0,0) and ±91 all read as NO
    anchor, said in the log by the caller). ``sats``/``accuracy_m`` are
    kept when present and numeric, else None: they describe the fix the
    anchor was taken on and are shown, never used to decide."""
    if not isinstance(rec, dict):
        return None
    lat, lon, at = rec.get("lat"), rec.get("lon"), rec.get("at")
    # Numbers only — a "12.3" in the file is a record something else wrote,
    # and parsing it would be guessing at what that something meant.
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           for v in (lat, lon, at)):
        return None
    lat, lon, at = float(lat), float(lon), float(at)
    if not valid_position(lat, lon) or at != at or at in (float("inf"),
                                                           float("-inf")):
        return None
    sats = rec.get("sats")
    acc = rec.get("accuracy_m")
    return {"lat": lat, "lon": lon, "at": at,
            "sats": sats if isinstance(sats, int) and not isinstance(sats, bool)
            else None,
            "accuracy_m": (float(acc) if isinstance(acc, (int, float))
                           and not isinstance(acc, bool) and acc == acc
                           else None)}


def _read_all(base_dir: Optional[str] = None) -> Dict[str, dict]:
    """Every USABLE anchor on disk. Missing, unreadable or corrupt -> ``{}``.
    A record that fails validation is dropped and named in the log, so a
    node that "has no anchor" on the gate can be traced to the file."""
    try:
        with open(_path(base_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, dict] = {}
    for key, rec in data.items():
        if not isinstance(key, str):
            continue
        clean = _clean(rec)
        if clean is None:
            print(f"[walk] anchor for {key[:8]} is not a usable position; "
                  "ignored", flush=True)
            continue                      # one bad record, not a lost file
        out[key] = clean
    return out


def _write_all(anchors: Dict[str, dict], base_dir: Optional[str] = None) -> None:
    """Atomic: write beside, fsync, rename over. A half-written file would
    read as corrupt and forget EVERY node's anchor at once. ``allow_nan``
    off: json would otherwise write ``NaN``, which is not JSON, and the
    whole file would read back as corrupt on the next walk."""
    base = os.path.expanduser(_dir(base_dir))
    os.makedirs(base, exist_ok=True)
    final = _path(base_dir)
    tmp = final + ".tmp"
    text = json.dumps(anchors, sort_keys=True, indent=2, allow_nan=False)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)


def save_anchor(node_key: str, lat: float, lon: float, at: float,
                sats: Optional[int] = None,
                accuracy_m: Optional[float] = None,
                base_dir: Optional[str] = None) -> dict:
    """Lock *node_key*'s anchor at (*lat*, *lon*), stood on at *at* (epoch
    seconds), on a fix of *sats* satellites and an estimated *accuracy_m*.
    Replaces any earlier anchor for the key. Refuses (ValueError) an empty
    key or a position that is not on Earth — a bad anchor saved is a bad
    distance on every sample of the next walk."""
    node_key = (node_key or "").strip()
    if not node_key:
        raise ValueError("an anchor needs a node key")
    if not valid_position(lat, lon):
        raise ValueError(f"not a usable position: {lat!r}, {lon!r}")
    anchors = _read_all(base_dir)
    rec = _clean({"lat": lat, "lon": lon, "at": at, "sats": sats,
                  "accuracy_m": accuracy_m})
    if rec is None:
        raise ValueError(f"not a usable anchor: at={at!r}")
    anchors[node_key] = rec
    _write_all(anchors, base_dir)
    return dict(rec)


def load_anchor(keys: Union[str, Sequence[str]],
                base_dir: Optional[str] = None) -> Optional[dict]:
    """``{lat, lon, at, sats, accuracy_m}`` for the node, or None. *keys* is
    one key or the device's candidate list (``candidate_keys``); the FIRST
    key with an anchor wins, so the primary (device id) is tried before an
    older destination-keyed one. No name road — see the module docstring."""
    if isinstance(keys, str):
        keys = [keys]
    anchors = _read_all(base_dir)
    for k in keys or ():
        rec = anchors.get((k or "").strip())
        if rec is not None:
            return dict(rec)
    return None


def forget_anchor(keys: Union[str, Iterable[str]],
                  base_dir: Optional[str] = None) -> int:
    """Drop the anchor under every key given. Returns how many went; 0 is an
    honest answer for an unknown node, never an error — and nothing is
    written when nothing matched. Rebirth and Delete-this-node both pass
    every key the machine was ever known by."""
    if isinstance(keys, str):
        keys = [keys]
    wanted = {(k or "").strip() for k in keys or ()} - {""}
    if not wanted:
        return 0
    anchors = _read_all(base_dir)
    doomed = [k for k in anchors if k in wanted]
    for k in doomed:
        del anchors[k]
    if doomed:
        _write_all(anchors, base_dir)
    return len(doomed)


# -- what the screen says about an anchor -------------------------------------
# (Its AGE is monitor.formatting.format_age_fine on (now - at) / 3600 — the
# house scale VITALS already speaks; no second one here.)

def anchor_gap_m(a, b) -> Optional[float]:
    """Metres between two ``(lat, lon)`` pairs, or None when either is not
    a usable position. ONE helper for both the overwrite guard (live fix vs
    saved anchor) and the disagreement line (saved anchor vs the position
    the node reports), so the two cannot measure differently."""
    from monitor.movement import haversine_m
    try:
        (lat1, lon1), (lat2, lon2) = a, b
    except (TypeError, ValueError):
        return None
    if not (valid_position(lat1, lon1) and valid_position(lat2, lon2)):
        return None
    return haversine_m(lat1, lon1, lat2, lon2)
