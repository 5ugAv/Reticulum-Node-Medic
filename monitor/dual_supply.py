"""Dual-supply tripwire: HAT power + USB-C power at the same time = danger.

A HAT-powered medic (battery pack feeding the GPIO 5V pins) must NEVER also
receive power on its USB-C socket: two supplies fight over one rail and
backfeed into whichever sits lower — the exact hazard of plugging a birth
cable into a running medic. The Pi 5's PMIC exposes the USB-C input voltage
as ADC channel ``EXT5V_V`` (verified live 2026-08-25: reads ~5.02 V on a
USB-C-powered medic), so software can SEE the second supply arrive.

Arming rule (stateless, no config): danger = **UPS HAT present** AND
**EXT5V above threshold**. A medic without the HAT (USB-C is its one true
supply, e.g. Medic 1) reads ups_present=False and the guard is inert. A
HAT-powered medic normally reads EXT5V ~0 V; the moment a live USB-C lands,
the guard trips. Desk-charging through the HAT's 12.6 V barrel jack does not
touch EXT5V and cannot false-trip.

Debounce: two consecutive hot reads before tripping (one ADC glitch must not
start a shutdown countdown); a single cool read clears (pulling the cable
must cancel instantly).
"""

from __future__ import annotations

import subprocess
from typing import Optional

#: Volts on the USB-C input that count as "a second supply is present".
#: A real supply reads ~5 V; a floating input reads near 0.
EXT5V_DANGER_V = 3.0

#: Seconds the keeper gets to pull the cable before the medic saves itself.
GRACE_SECONDS = 15


def parse_ext5v(text: str) -> Optional[float]:
    """Extract volts from ``vcgencmd pmic_read_adc EXT5V_V`` output, e.g.
    ``EXT5V_V volt(24)=5.01830000V`` -> 5.0183. None if unparsable."""
    if "=" not in text:
        return None
    try:
        value = text.split("=", 1)[1].strip().rstrip("Vv \n")
        return float(value)
    except (ValueError, IndexError):
        return None


def read_ext5v() -> Optional[float]:
    """USB-C input volts from the PMIC, or None when unreadable (no vcgencmd,
    not a Pi 5, permission problem). None NEVER trips the guard."""
    try:
        out = subprocess.run(
            ["vcgencmd", "pmic_read_adc", "EXT5V_V"],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_ext5v(out or "")


class DualSupplyGuard:
    """Debounced tripwire. Feed it (ups_present, ext5v) each monitor lap:
    returns "danger" on the lap it trips, "clear" on the lap the second
    supply vanishes after a trip, else None."""

    def __init__(self, threshold: float = EXT5V_DANGER_V):
        self.threshold = threshold
        self._hot_laps = 0
        self.tripped = False

    def evaluate(self, ups_present: bool, ext5v: Optional[float]):
        hot = bool(ups_present) and ext5v is not None and ext5v > self.threshold
        if hot:
            self._hot_laps += 1
            if self._hot_laps >= 2 and not self.tripped:
                self.tripped = True
                return "danger"
            return None
        self._hot_laps = 0
        if self.tripped:
            self.tripped = False
            return "clear"
        return None
