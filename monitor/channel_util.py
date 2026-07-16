"""Channel utilisation estimator — the VITALS stat line (backlog feature 2).

How busy is the LoRa channel? The RNode firmware already measures it: the
splitter records ``channel_load`` / ``airtime`` (short-term and the firmware's
own long-term figure) from the periodic channel-stats frames, and rnstatus
exposes the same numbers. This module turns that fraction into the dashboard
stat line — plain-English label, ASCII bar (the Pi has no block-glyph font),
and a congestion-source hint naming the node whose announces dominate the
observed traffic (from the registry's history timestamps).

Pure functions; no hardware.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

#: (upper bound, label, severity) — per the confirmed thresholds
_LEVELS = [
    (0.25, "Healthy", "ok"),
    (0.50, "Moderate", "ok"),
    (0.75, "Busy", "warn"),
    (0.90, "Congested - consider reducing announce frequency", "warn"),
    (1.01, "Critical - packet loss likely", "alert"),
]

BAR_SLOTS = 10
HINT_THRESHOLD = 0.50            # name the loudest node once Busy or worse


def utilisation_label(frac: float) -> Tuple[str, str]:
    """(label, severity) for a 0..1 channel-utilisation fraction."""
    f = max(0.0, min(1.0, frac))
    for bound, label, severity in _LEVELS:
        if f < bound:
            return label, severity
    return _LEVELS[-1][1], _LEVELS[-1][2]


def utilisation_bar(frac: float) -> str:
    """A 10-slot ASCII bar, e.g. ``[##--------]`` (no block glyphs — the field
    Pi's font can't render them)."""
    f = max(0.0, min(1.0, frac))
    filled = round(f * BAR_SLOTS)
    return "[" + "#" * filled + "-" * (BAR_SLOTS - filled) + "]"


def stat_line(frac: Optional[float]) -> str:
    """The dashboard line, e.g. ``Channel utilisation: 12% [#---------] Healthy``.
    ``None`` (no radio data) reads honestly as unknown."""
    if frac is None:
        return "Channel utilisation: unknown (radio not reporting)"
    label, _sev = utilisation_label(frac)
    return f"Channel utilisation: {frac * 100:.0f}% {utilisation_bar(frac)} {label}"


def busiest_announcer(registry, now: float, window_s: float = 3600.0
                      ) -> Optional[Tuple[str, int]]:
    """(name-or-hash, count) of the node heard most often in the last hour,
    from the registry's history timestamps. None if nothing was heard."""
    best: Optional[Tuple[str, int]] = None
    for dst, rec in registry.nodes.items():
        count = len(registry.history.series(dst, since=now - window_s))
        if count > 0 and (best is None or count > best[1]):
            best = (rec.name or dst, count)
    return best


def congestion_hint(frac: Optional[float], registry, now: float) -> Optional[str]:
    """A plain-English pointer at the likely congestion source, once the channel
    is Busy or worse. None while the channel is comfortable."""
    if frac is None or frac < HINT_THRESHOLD:
        return None
    top = busiest_announcer(registry, now)
    if top is None:
        return None
    name, count = top
    return (f"High channel utilisation - {name} is announcing very frequently "
            f"({count} heard in the last hour). Check its announce interval.")
