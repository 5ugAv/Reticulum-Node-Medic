"""Triage — the site-survey / antenna-aiming scoring engine.

The pure logic behind the Node Medic's Triage screen: turn live RSSI / SNR /
noise floor into one 0..1 quality score that drives the thermal bullseye (1.0 =
dead-centre "hot").

Two ideas from the field testing shape it:

* **On-site adaptive calibration.** Absolute datasheet thresholds don't match a
  given node or site (we measured our node's noise floor sitting ~10 dB off a
  handover's numbers). So the score is normalised against the range the medic
  *actually observes* as the operator moves the antenna — "hot" means the best
  achievable *here*, not an ideal. SNR leads the weighting because it's the
  stable, decode-relevant metric (RSSI swings ±10 dB on multipath at a fixed
  spot; a dead antenna shows the "cleanest" noise floor).
* **A fixed decode-floor sanity check.** Purely relative scoring would call the
  least-bad spot at a hopeless site "hot". Below the SF9 decode floor the reading
  is flagged unusable regardless of the relative score.

No hardware, no UI — this is the testable core the Kivy widget renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# -- weights: SNR leads (stable + decode-relevant), margin second, noise last --
W_SNR, W_MARGIN, W_NOISE = 0.5, 0.3, 0.2
DECODE_FLOOR_SNR = -12.5           # dB, SF9: below this, packets don't decode at all
MIN_CAL_SAMPLES = 8                # observations before the adaptive range takes over

# absolute fallback ranges (low_value, high_value) used until the site calibrates
SNR_DEFAULT = (-12.5, 12.0)        # dB
MARGIN_DEFAULT = (5.0, 40.0)       # dB  (rssi - noise floor)
NOISE_DEFAULT = (-118.0, -95.0)    # dBm (quiet .. noisy — lower is better)

RING_THRESHOLDS = [                # (min composite score, ring name)
    (0.85, "bullseye"),
    (0.65, "warm"),
    (0.45, "warming"),
    (0.25, "cold"),
]

DEBOUNCE_S = 3.0                   # rolling window for the dot/guidance (pole readings are noisy)
LOCK_STABILITY = 0.05             # score must stay within +-this to be "stable"
LOCK_HOLD_S = 3.0                 # stable in the bullseye this long -> locked


def _percentile(sorted_vals: List[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


class MetricRange:
    """Learns an achievable range for one metric from observed samples (robust
    10th/90th percentiles once there are enough; a fixed default before that) and
    normalises a value to 0..1 where 1 = best. ``invert`` for lower-is-better."""

    def __init__(self, default: Tuple[float, float], invert: bool = False):
        self.default = default          # (low, high), low < high
        self.invert = invert
        self.samples: List[float] = []

    def add(self, v: float) -> None:
        self.samples.append(v)

    def bounds(self) -> Tuple[float, float]:
        if len(self.samples) < MIN_CAL_SAMPLES:
            return self.default
        s = sorted(self.samples)
        return _percentile(s, 10), _percentile(s, 90)

    def normalize(self, v: float) -> float:
        lo, hi = self.bounds()
        if hi == lo:
            return 0.5
        n = max(0.0, min(1.0, (v - lo) / (hi - lo)))
        return (1.0 - n) if self.invert else n


@dataclass
class TriageCalibration:
    snr: MetricRange = field(default_factory=lambda: MetricRange(SNR_DEFAULT))
    margin: MetricRange = field(default_factory=lambda: MetricRange(MARGIN_DEFAULT))
    noise: MetricRange = field(default_factory=lambda: MetricRange(NOISE_DEFAULT, invert=True))

    def observe(self, snr: float, rssi: float, noise: float) -> None:
        self.snr.add(snr)
        self.margin.add(rssi - noise)
        self.noise.add(noise)

    @property
    def calibrated(self) -> bool:
        return len(self.snr.samples) >= MIN_CAL_SAMPLES


def composite_score(snr: float, rssi: float, noise: float,
                    calib: Optional[TriageCalibration] = None) -> Tuple[float, bool]:
    """Return ``(score, usable)``. ``score`` is 0..1 (1.0 = dead-centre bullseye);
    ``usable`` is False when SNR is below the SF9 decode floor, regardless of the
    relative score — a below-floor spot can't masquerade as warm."""
    calib = calib or TriageCalibration()
    snr_n = calib.snr.normalize(snr)
    margin_n = calib.margin.normalize(rssi - noise)
    noise_n = calib.noise.normalize(noise)
    score = W_SNR * snr_n + W_MARGIN * margin_n + W_NOISE * noise_n
    usable = snr >= DECODE_FLOOR_SNR
    if not usable:
        score = min(score, 0.15)
    return max(0.0, min(1.0, score)), usable


def score_to_ring(score: float) -> str:
    for threshold, name in RING_THRESHOLDS:
        if score >= threshold:
            return name
    return "freezing"


# Plain ASCII text only — the field Pi carries no emoji font. The thermal COLOUR
# of the bullseye (see thermal_color) carries the hot/cold metaphor, not glyphs.
_GUIDANCE = {
    "freezing": "Freezing - this spot won't work, try a different location",
    "cold": "Cold - poor signal here, consider repositioning the node",
    "warming": "Getting warmer - keep adjusting the antenna slowly",
    "warm": "Warm - good signal, fine-tune the antenna angle",
    "bullseye": "Hot - hold that position, locking in...",
}


def guidance_text(ring: str, colder: bool = False, locked: bool = False,
                  usable: bool = True) -> str:
    if locked:
        return "Locked! Secure the antenna now"
    if colder:
        return "Colder - go back to where it was"
    if not usable:
        return "Below the decode floor here - packets won't get through"
    return _GUIDANCE.get(ring, _GUIDANCE["freezing"])


# Infrared-style thermal ramp (cold dark violet -> hot yellow-white) for the
# bullseye rings and dot. Deliberately NOT the theme's traffic-light green/amber/
# red, so the placement metaphor reads as temperature, not pass/fail.
_THERMAL_STOPS = [
    (0.00, (0.08, 0.02, 0.22)),     # freezing  — near-black violet
    (0.25, (0.30, 0.05, 0.45)),     # cold      — purple
    (0.50, (0.75, 0.18, 0.28)),     # warming   — deep red
    (0.75, (0.96, 0.50, 0.10)),     # warm      — orange
    (1.00, (1.00, 0.93, 0.60)),     # hot       — yellow-white
]


def thermal_color(t: float) -> tuple:
    """Map 0..1 (cold..hot) to an (r, g, b) triple on the infrared ramp."""
    t = max(0.0, min(1.0, t))
    for i in range(len(_THERMAL_STOPS) - 1):
        t0, c0 = _THERMAL_STOPS[i]
        t1, c1 = _THERMAL_STOPS[i + 1]
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(c0[k] + (c1[k] - c0[k]) * f for k in range(3))
    return _THERMAL_STOPS[-1][1]


@dataclass
class _Stamped:
    t: float
    score: float


class TriageSession:
    """Stateful Triage run: feed live (snr, rssi, noise, t) samples; each returns
    a debounced dot/guidance snapshot. Tracks the best reading and auto-lock."""

    def __init__(self, calib: Optional[TriageCalibration] = None):
        self.calib = calib or TriageCalibration()
        self._window: List[_Stamped] = []     # raw scores within DEBOUNCE_S (smoothing)
        self._history: List[_Stamped] = []     # smoothed scores (direction detection)
        self.best_score: float = 0.0
        self.best_reading: Optional[Dict] = None
        self.locked: bool = False
        self._lock_since: Optional[float] = None

    def feed(self, snr: float, rssi: float, noise: float, t: float) -> Dict:
        self.calib.observe(snr, rssi, noise)
        raw, usable = composite_score(snr, rssi, noise, self.calib)

        self._window.append(_Stamped(t, raw))
        self._window = [s for s in self._window if t - s.t <= DEBOUNCE_S]
        smoothed = sum(s.score for s in self._window) / len(self._window)

        self._history.append(_Stamped(t, smoothed))
        self._history = [s for s in self._history if t - s.t <= 30.0]

        if smoothed > self.best_score:
            self.best_score = smoothed
            self.best_reading = {"snr": snr, "rssi": rssi, "noise": noise,
                                 "score": round(smoothed, 3), "t": t}

        ring = score_to_ring(smoothed)
        stable = all(abs(s.score - smoothed) <= LOCK_STABILITY for s in self._window)
        if ring == "bullseye" and stable:
            if self._lock_since is None:
                self._lock_since = t
            if t - self._lock_since >= LOCK_HOLD_S:
                self.locked = True
        else:
            self._lock_since = None
            self.locked = False

        colder = self._is_colder(t, smoothed)
        return {
            "score": smoothed,
            "ring": ring,
            "usable": usable,
            "colder": colder,
            "locked": self.locked,
            "dot_radius": 1.0 - smoothed,      # 0.0 = centre (hot), 1.0 = outer edge
            "guidance": guidance_text(ring, colder, self.locked, usable),
        }

    def _is_colder(self, t: float, smoothed: float) -> bool:
        past = [s for s in self._history if t - s.t >= DEBOUNCE_S]
        if not past:
            return False
        return smoothed < past[-1].score - LOCK_STABILITY
