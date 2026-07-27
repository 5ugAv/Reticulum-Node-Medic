"""Autonomous outage watch — escalate a node that stays unreachable, but give a
solar node time to recharge before we send anyone out to it.

This runs on the medic by itself, 24/7, no user babysitting. It's PASSIVE: the
medic already hears each node's announces and tracks ``last_seen``, so "monitoring"
is just timing the SILENCE. The logic:

* A node re-announcing RESETS its clock — an intermittent solar node (awake at
  midday, asleep at night) is NOT dead and must never escalate.
* Escalate ONCE per outage, only after continuous silence exceeds a grace window.
  The default window is **3 days** — long enough to ride out a cloudy spell so we
  don't dispatch someone to "fix" a node that just needed sun.
* Re-arm only after the node recovers, so a future outage can warn again.

Grace is power-source-aware (a dead MAINS node won't recharge, so it can escalate
sooner) — but the registry doesn't record real power source yet (every node reports
a placeholder "battery"), so today everything gets the safe 3-day default. The map
is ready: once BIRTH stamps the true source, per-source windows activate for free.

Pure + unit-testable; the app feeds it the registry's device dicts each cycle and
persists ``_notified`` so a restart doesn't re-warn or forget an active outage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

#: Default grace before escalating a continuously-silent node — 3 days (solar
#: recharge grace). The safe default applied to every node today.
DEFAULT_GRACE_H = 72.0

#: Per-power-source grace (hours). Activates once real power source is recorded;
#: until then every real node reads "battery" -> the safe 3-day default. A mains
#: node can't recharge from sun, so it's flagged sooner when the source is known.
GRACE_BY_POWER = {"solar": 72.0, "battery": 72.0, "mains": 24.0}


def grace_hours(powered_by: Optional[str], override_h: Optional[float] = None) -> float:
    """The escalation window for a node. ``override_h`` (a Settings value) wins;
    else by power source; else the 3-day default."""
    if override_h:
        return override_h
    return GRACE_BY_POWER.get((powered_by or "").lower(), DEFAULT_GRACE_H)


@dataclass
class NodeWatcher:
    """Tracks which nodes have already been escalated this outage. Feed the current
    device dicts to :meth:`tick`; it returns the nodes that JUST crossed the grace
    line (to alert on). ``grace_override_h`` comes from Settings (None = defaults)."""
    grace_override_h: Optional[float] = None
    _notified: Set[str] = field(default_factory=set)

    def tick(self, devices: List[dict]) -> List[dict]:
        """Evaluate every device; return those that just escalated (continuous
        silence past the grace window, not yet warned). Auto-resolves: a node no
        longer red is dropped from the warned set, so recovery re-arms it."""
        escalated: List[dict] = []
        for d in devices:
            nid = d.get("identity") or d.get("id")
            if not nid:
                continue
            if d.get("status") != "alert":
                self._notified.discard(nid)      # healthy/known/recovered -> re-arm
                continue
            lsh = d.get("last_seen_hours")
            if lsh is None:
                continue                          # never heard -> no outage to time
            grace = grace_hours(d.get("powered_by"), self.grace_override_h)
            if lsh >= grace and nid not in self._notified:
                self._notified.add(nid)
                escalated.append(d)
        return escalated

    def is_watching(self, device: dict) -> bool:
        """True if this node is red but still inside its grace window — i.e. the
        medic is watching it and hasn't escalated yet. Drives the 'unreachable —
        waiting, will warn in N days' message when the operator taps a red node."""
        if device.get("status") != "alert":
            return False
        lsh = device.get("last_seen_hours")
        if lsh is None:
            return False
        return lsh < grace_hours(device.get("powered_by"), self.grace_override_h)

    def watch_remaining_hours(self, device: dict) -> Optional[float]:
        """Hours left before this red node escalates (for the tap message). None if
        it's not a timeable outage."""
        lsh = device.get("last_seen_hours")
        if device.get("status") != "alert" or lsh is None:
            return None
        return max(0.0, grace_hours(device.get("powered_by"), self.grace_override_h) - lsh)

    # -- persistence (app saves alongside the registry) ---------------------
    def to_state(self) -> dict:
        return {"notified": sorted(self._notified)}

    def load_state(self, state: dict) -> None:
        try:
            self._notified = set(state.get("notified", []))
        except (AttributeError, TypeError):
            self._notified = set()
