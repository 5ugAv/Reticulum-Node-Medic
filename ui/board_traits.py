"""What the medic has LEARNED about each board model, from boards it has read.

The board picker still shows a gallery when several models share one chip —
a Tracker, a XIAO, a T3S3 and a T-Deck are all "ESP32-S3" to esptool
(operator, 2026-08-30: "can node medic thin this selection... more
information gatherable to distinguish it?").

There IS more information — PSRAM presence and size, flash size — and
esptool prints it on every read. What was missing is a place to keep the
answer. This module is that place, and it fills itself: whenever the
operator confirms which board a chip is, the medic files the traits it
measured on that board under that model. The next board of the same model
narrows on its own, and the one after that.

ONE MEASUREMENT METHOD, ALWAYS. Traits are only comparable when read the
same way: esptool with its stub reports PSRAM differently from a --no-stub
read, so a trait learned one way and compared the other would invent a
contradiction and drop the right board. Everything here is fed by
``board_detect._default_reader`` (stub, ``flash_id``) on both sides —
learning and narrowing — and anything teaching this store from another
command must record that method first. (Checked live 2026-08-30 on a XIAO
S3: both methods agreed, psram "none", flash 8MB — agreement is not
guaranteed on every board.)

THE HONESTY RULE, which is why this never guesses: a trait is recorded ONLY
when the medic measured it on a board the operator confirmed. A model with
nothing recorded is never filtered out — an unknown trait can only ever
fail to narrow, never wrongly exclude the board in someone's hand. That is
also why the store is per-model (not per-chip): it describes a model of
board, learned from real ones, and a wrong confirmation is corrected by the
next.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

STORE = os.path.join(os.path.expanduser("~"), ".reticulum-node-medic",
                     "board_traits.json")


def _load(path: Optional[str] = None) -> dict:
    try:
        with open(path or STORE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:                      # noqa: BLE001 — no store = nothing learned
        return {}


def traits_for(board_key: str, path: Optional[str] = None) -> Dict[str, str]:
    """What we've measured on this model, or {} when we've never seen one."""
    got = _load(path).get(board_key)
    return got if isinstance(got, dict) else {}


def learn(board_key: str, traits: Dict[str, Optional[str]],
          path: Optional[str] = None) -> bool:
    """File measured traits under *board_key* (the operator just confirmed it).

    Only non-empty values are stored, and a later reading overwrites an
    earlier one — the newest confirmed board is the better witness. Never
    raises: failing to learn costs one extra question, while a crash here
    would cost the birth.
    """
    if not board_key:
        return False
    keep = {k: str(v) for k, v in (traits or {}).items() if v}
    if not keep:
        return False
    try:
        store = _load(path)
        entry = store.get(board_key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry.update(keep)
        store[board_key] = entry
        target = path or STORE
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w") as f:
            json.dump(store, f, indent=1, sort_keys=True)
        os.replace(tmp, target)
        return True
    except Exception:                      # noqa: BLE001
        return False


def narrow_by_traits(shortlist, measured: Dict[str, Optional[str]],
                     path: Optional[str] = None):
    """Drop models whose LEARNED traits contradict what we just measured.

    A model survives unless a trait we have recorded for it disagrees with
    the same trait on the board in hand. Models we know nothing about always
    survive. If every model would be dropped, the shortlist is returned
    untouched — a contradiction means our learning is wrong, and the
    operator must still be able to pick the board they are holding.
    """
    measured = {k: v for k, v in (measured or {}).items() if v}
    if not shortlist or not measured:
        return shortlist
    kept = []
    for b in shortlist:
        known = traits_for(getattr(b, "key", ""), path)
        if any(k in known and str(known[k]) != str(v)
               for k, v in measured.items()):
            continue
        kept.append(b)
    return kept or shortlist


# ---- MAC prefixes: evidence, never proof ------------------------------------
#
# Espressif ships its chips in OUI blocks, and a board maker's production runs
# cluster inside one: on this bench both Heltec Trackers read 3C:0F:02:EB:*
# (four bytes shared) while the XIAO S3 reads 68:EE:8F:* (2026-08-30). That is
# real evidence about which model a chip came from — but it is NOT proof: the
# blocks belong to Espressif, not to Heltec or Seeed, so another vendor's board
# can legitimately land inside a prefix we have learned.
#
# So prefixes RANK, they never eliminate. The likely board is offered first
# (and named as a suggestion), every other candidate stays one tap away, and
# the operator's answer — who can see the board — always wins and is what
# gets learned.

PREFIX_BYTES = 4          # bytes of MAC to remember; 4 was the observed cluster


def _prefix(mac: str) -> str:
    parts = [p for p in (mac or "").replace("-", ":").split(":") if p]
    return ":".join(p.lower() for p in parts[:PREFIX_BYTES])


def learn_mac_prefix(board_key: str, mac: str,
                     path: Optional[str] = None) -> bool:
    """Record that a confirmed *board_key* had a chip in this MAC prefix."""
    pre = _prefix(mac)
    if not board_key or not pre:
        return False
    try:
        store = _load(path)
        entry = store.get(board_key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        seen = entry.get("mac_prefixes")
        seen = list(seen) if isinstance(seen, list) else []
        if pre not in seen:
            seen.append(pre)
            seen = seen[-8:]          # a model's runs, not a lifetime log
        entry["mac_prefixes"] = seen
        store[board_key] = entry
        target = path or STORE
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w") as f:
            json.dump(store, f, indent=1, sort_keys=True)
        os.replace(tmp, target)
        return True
    except Exception:                  # noqa: BLE001
        return False


def rank_by_mac(shortlist, mac: str, path: Optional[str] = None):
    """Reorder so models seen at this MAC prefix come first. Same members,
    same length — ranking is a suggestion, not a filter."""
    pre = _prefix(mac)
    if not shortlist or not pre:
        return shortlist
    def seen(b) -> int:
        known = traits_for(getattr(b, "key", ""), path).get("mac_prefixes")
        return 1 if isinstance(known, list) and pre in known else 0
    return sorted(shortlist, key=lambda b: -seen(b))


def likely_from_mac(shortlist, mac: str, path: Optional[str] = None):
    """The single model this MAC prefix points at, or None when the evidence
    is absent or ambiguous (two models sharing a prefix suggests nothing)."""
    pre = _prefix(mac)
    if not pre:
        return None
    hits = [b for b in (shortlist or [])
            if pre in (traits_for(getattr(b, "key", ""), path)
                       .get("mac_prefixes") or [])]
    return hits[0] if len(hits) == 1 else None
