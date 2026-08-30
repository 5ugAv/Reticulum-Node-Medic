"""Dual-supply tripwire: HAT power + USB-C power at the same time = danger.

A HAT-powered medic (battery pack feeding the GPIO 5V pins) must NEVER also
receive power on its USB-C socket: two supplies fight over one rail and
backfeed into whichever sits lower — the exact hazard of plugging a birth
cable into a running medic. The Pi 5's PMIC exposes the USB-C input voltage
as ADC channel ``EXT5V_V`` (verified live 2026-08-25: reads ~5.02 V on a
USB-C-powered medic), so software can SEE the second supply arrive.

Arming rule (REDESIGNED 2026-08-30 after a live false positive): danger =
**UPS HAT present** AND **a WITNESSED ARRIVAL** on the USB-C sense pin —
EXT5V must first be read BELOW threshold at least once (proof this
hardware's sense line can read low at all), and only a later low->high
transition trips the guard. Why: the original rule trusted the premise
"a HAT-powered medic reads EXT5V ~0 V", and HAWKEYE disproved it on
glass — its HAT BACKFEEDS the sense line, so a battery-only boot read
~5 V with an EMPTY socket and the guard shut a healthy medic down in a
15-second loop. A board whose sense line never reads low is exactly that
backfeed case: the guard stays PERMANENTLY INERT there and says so
("uncalibratable") rather than guessing. A medic without the HAT
(ups_present=False, e.g. Medic 1) is inert as before. Desk-charging via
the HAT's 12.6 V barrel jack does not touch EXT5V and cannot false-trip.

Debounce: two consecutive hot reads before tripping (one ADC glitch must
not start a shutdown countdown); a single cool read clears (pulling the
cable must cancel instantly) and re-arms for the next arrival.
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
    """Debounced, ARRIVAL-witnessing tripwire. Feed it (ups_present, ext5v)
    each monitor lap: returns "danger" on the lap it trips, "clear" on the
    lap the second supply vanishes after a trip, else None.

    The guard is DISARMED until it has seen EXT5V below threshold once —
    the calibration proof that this hardware's sense line reads low when
    the socket is empty. Hardware whose HAT backfeeds the line never
    passes that proof, and the guard stays inert instead of shutting a
    healthy medic down (HAWKEYE, 2026-08-30). ``why_inert()`` names the
    state for diagnostics."""

    def __init__(self, threshold: float = EXT5V_DANGER_V):
        self.threshold = threshold
        self._hot_laps = 0
        self.tripped = False
        #: True once EXT5V has been read BELOW threshold — the witnessed
        #: baseline that makes a later high reading an ARRIVAL, not a
        #: backfed constant.
        self.armed = False
        self._reads = 0

    def why_inert(self):
        """None when armed; else a short reason for diagnostics."""
        if self.armed:
            return None
        if self._reads == 0:
            return "no reading yet"
        return ("sense line has never read low — likely HAT backfeed; "
                "guard stays inert on this hardware")

    def evaluate(self, ups_present: bool, ext5v: Optional[float]):
        if ext5v is not None:
            self._reads += 1
            if ext5v <= self.threshold:
                self.armed = True
        hot = (self.armed and bool(ups_present)
               and ext5v is not None and ext5v > self.threshold)
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
