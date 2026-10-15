"""The front page's Wi-Fi icon, as numbers (keeper, 2026-10-15: "grey if
wifi off, use wifi bars like on a phone to show how strong wifi signal is").
No Kivy here: the widget (ui/widgets/wifi_icon.py) and the offline preview
(scripts/preview_wifi_icon.py) both draw from these, so what was looked at
is what ships."""
from __future__ import annotations

from typing import List, Optional, Tuple

OFF = "off"                 # the radio is switched off: grey
SEARCHING = "searching"     # on, no network joined: dim bars, none lit
CONNECTED = "connected"     # bars lit by strength

#: Signal percent at which each bar lights, phone-style.
BAR_THRESHOLDS = (1, 35, 55, 75)


def state_for(enabled: bool, connected: bool) -> str:
    if not enabled:
        return OFF
    return CONNECTED if connected else SEARCHING


def bars_for(enabled: bool, connected: bool, signal: Optional[int]) -> int:
    """How many of the four bars are lit. Off or not joined: none."""
    if not enabled or not connected or signal is None:
        return 0
    return sum(1 for t in BAR_THRESHOLDS if int(signal) >= t)


def bar_geometry(w: float, h: float) -> List[Tuple[float, float, float, float]]:
    """Four bars inside a w x h box, left to right, rising: each as
    ``(x, y, bw, bh)`` from the box's bottom-left. Gaps of a fifth of a bar."""
    n = 4
    bw = w / (n + (n - 1) * 0.35)
    gap = bw * 0.35
    out = []
    for i in range(n):
        bh = h * (0.34 + 0.22 * i)
        out.append((i * (bw + gap), 0.0, bw, bh))
    return out
