"""The node page's one line about the node's clock (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23). Kivy-free so it is tested as text; the
screen only places it. Reads monitor.time_ledger's entry and says exactly
what the ledger can back: a set that was ACKED, a check that came back
"already right", or nothing yet. A TIME that was sent and never answered
is "not yet given" — the node did not confirm, so the medic does not claim."""
from __future__ import annotations

import time
from typing import Callable, Optional

from monitor.formatting import format_age_fine
from monitor.time_ledger import clock_state
from ui.i18n import tr


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def clock_line(entry: Optional[dict], strftime: Callable[[float], str] = _hhmm) -> str:
    state, acked_at, delta_s = clock_state(entry)
    if state == "set":
        off = format_age_fine(abs(float(delta_s or 0.0)) / 3600.0)
        return tr("Clock: set by Node Medic over the mesh at {time} (was {off} off)").format(
            time=strftime(acked_at), off=off)
    if state == "checked":
        return tr("Clock: checked by Node Medic at {time} — already right").format(
            time=strftime(acked_at))
    return tr("Clock: not yet given by Node Medic")
