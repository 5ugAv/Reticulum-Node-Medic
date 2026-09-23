"""The node page's one line about the node's clock (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23; wording revised after review the same
day). Kivy-free so it is tested as text; the screen only places it. Reads
monitor.time_ledger's entry and says exactly what the ledger can back —
a set the node ACKED, a check that came back "already right", a helper
that failed, a node that keeps NTP time, a confirmation gone stale, or
nothing yet. A TIME that was sent and never answered is "not yet
confirmed" — the node did not say, so the medic does not claim.

Ages are the monitor's own format_age_fine (minutes under an hour, then
format_age's hours/days) — never a wall-clock HH:MM, which reads as a
different time on a medic whose clock has since moved. Never raises on a
malformed row: clock_state wraps every conversion."""
from __future__ import annotations

import time
from typing import Callable, Optional

from monitor.formatting import format_age_fine
from monitor.time_ledger import clock_state
from ui.i18n import tr


def _age(now: float, then: float) -> str:
    try:
        return format_age_fine(max(0.0, float(now) - float(then)) / 3600.0)
    except (TypeError, ValueError):
        return "?"


def clock_line(entry: Optional[dict], now: Callable[[], float] = time.time) -> str:
    try:
        t_now = float(now())
    except Exception:                                              # noqa: BLE001
        t_now = 0.0
    state, acked_at, delta_s, extra = clock_state(entry, now=t_now)
    if state == "set":
        age = _age(t_now, acked_at)
        if delta_s is None:
            return tr("Clock: set by Node Medic over the mesh {age} ago").format(age=age)
        try:
            off = format_age_fine(abs(float(delta_s)) / 3600.0)
        except (TypeError, ValueError):
            return tr("Clock: set by Node Medic over the mesh {age} ago").format(age=age)
        return tr("Clock: set by Node Medic over the mesh {age} ago (was {off} off)").format(
            age=age, off=off)
    if state == "checked":
        return tr("Clock: checked by Node Medic {age} ago — already right").format(
            age=_age(t_now, acked_at))
    if state == "failed":
        return tr("Clock: Node Medic sent the time {age} ago — the node could not set it"
                  ).format(age=_age(t_now, acked_at))
    if state == "ntp":
        return tr("Clock: the node keeps NTP time — left alone")
    if state == "stale":
        if extra.get("tried"):
            return tr("Clock: last confirmed {age} ago; a later try did not reach the node"
                      ).format(age=_age(t_now, acked_at))
        return tr("Clock: last confirmed {age} ago; {n} sends since unanswered").format(
            age=_age(t_now, acked_at), n=int(extra.get("sends") or 0))
    return tr("Clock: not yet confirmed by Node Medic")
