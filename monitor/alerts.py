"""Alerts — visual attention when a node goes orange (warn) or red (alert).

A single on/off toggle (Settings). When on, VITALS surfaces nodes that need
attention: a banner + those nodes pushed to the top. There is no speaker yet, so
this is visual-only — but the settings model and dispatch carry an ``audible``
channel so an audible option drops in later without restructuring.

Pure logic (settings persistence + which nodes alert + transition detection),
unit-tested; the VITALS screen is the presentation over it.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

CONFIG = os.path.expanduser("~/.reticulum-node-medic/alerts.json")

#: Statuses that raise an alert, worst first.
ALERT_STATUSES = ("alert", "warn")

#: Severity rank so "going orange or red" = a rise into a worse level.
_RANK = {"ok": 0, "unknown": 0, "": 0, None: 0, "warn": 1, "alert": 2}


def _rank(status) -> int:
    return _RANK.get(status, 0)


def load_settings(path: str = CONFIG) -> Dict:
    """{'enabled': bool, 'audible': bool}. Alerts default ON; audible off (no
    speaker yet). A missing/garbled file returns the defaults."""
    d = {}
    try:
        with open(path) as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            d = raw
    except (OSError, ValueError):
        pass
    return {"enabled": bool(d.get("enabled", True)),
            "audible": bool(d.get("audible", False))}


def save_settings(settings: Dict, path: str = CONFIG) -> Dict:
    s = load_settings(path)
    s.update({k: bool(v) for k, v in settings.items() if k in ("enabled", "audible")})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(s, f, indent=2)
    return s


def is_enabled(path: str = CONFIG) -> bool:
    return load_settings(path)["enabled"]


def set_enabled(enabled: bool, path: str = CONFIG) -> Dict:
    return save_settings({"enabled": enabled}, path)


def _name(node: Dict) -> str:
    return node.get("name") or node.get("hostname") or "(unnamed)"


def alerting_nodes(nodes: List[Dict]) -> List[Dict]:
    """Nodes currently at warn/alert, worst first (alert before warn), then by name.
    The persistent visual-alert set VITALS shows while a node needs attention."""
    hits = [n for n in (nodes or []) if n.get("status") in ALERT_STATUSES]
    return sorted(hits, key=lambda n: (-_rank(n.get("status")), _name(n).lower()))


def new_alerts(prev_status: Optional[Dict[str, str]], nodes: List[Dict]) -> List[Dict]:
    """Nodes that just ROSE into warn/alert since *prev_status* ({name: status}).
    Used to FIRE an alert (flash/audible later) only on the transition, not every
    poll. A node already at that level (or higher) does not re-fire."""
    prev = prev_status or {}
    out = []
    for n in nodes or []:
        cur = n.get("status")
        if cur in ALERT_STATUSES and _rank(cur) > _rank(prev.get(_name(n))):
            out.append(n)
    return out


def status_map(nodes: List[Dict]) -> Dict[str, str]:
    """{name: status} snapshot, to feed the next poll's new_alerts()."""
    return {_name(n): n.get("status") for n in (nodes or [])}


def banner_text(nodes: List[Dict]) -> str:
    """One-line banner for the current alerting set, or "" when all clear."""
    hits = alerting_nodes(nodes)
    if not hits:
        return ""
    names = ", ".join(_name(n) for n in hits[:4])
    more = f" +{len(hits) - 4} more" if len(hits) > 4 else ""
    n = len(hits)
    noun, verb = ("node", "needs") if n == 1 else ("nodes", "need")
    return f"⚠ {n} {noun} {verb} attention: {names}{more}"
