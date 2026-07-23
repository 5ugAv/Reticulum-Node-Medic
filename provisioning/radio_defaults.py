"""Tool-wide default LoRa radio parameters + regional presets.

These are the five parameters BIRTH pre-fills (frequency, bandwidth, spreading
factor, coding rate, TX power). One canonical default ships (915.125 MHz — AU/NZ/
Americas). Regions that don't use 915 MHz get a preset that fills ALL five with
values sane for that region's licence conditions. Nodes built on different
frequency bands form SEPARATE meshes, so switching region is a deliberate act
(the UI confirms it).

Pure filesystem + JSON, no Kivy — unit-tested. The saved defaults live under the
medic's config dir; a missing/garbled file falls back to the canonical default.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

CONFIG = os.path.expanduser("~/.reticulum-node-medic/radio_defaults.json")

#: The five parameters, in display order, with (key, label, cast).
PARAM_KEYS = ("freq", "bw", "sf", "cr", "txp")

#: Canonical default = the current AU/NZ/Americas 915 MHz config.
DEFAULT_PARAMS: Dict[str, float] = {
    "freq": 915.125, "bw": 125.0, "sf": 9, "cr": 5, "txp": 17,
}

#: Regional presets: (key, label, params, note). Each fills all five params.
#: BW/SF/CR are the standard modem config; frequency (and TX cap) vary by region.
REGIONAL_PRESETS: List[Tuple[str, str, Dict[str, float], str]] = [
    ("au_nz_americas", "Australia / New Zealand / Americas",
     {"freq": 915.125, "bw": 125.0, "sf": 9, "cr": 5, "txp": 17},
     "915 MHz ISM (AU LIPD / US FCC Part 15). The current default."),
    ("eu868", "Europe (EU868)",
     {"freq": 869.525, "bw": 125.0, "sf": 9, "cr": 5, "txp": 14},
     "EU868 869.525 MHz — duty-cycle limits apply (typically 10% on this band)."),
    ("india", "India",
     {"freq": 866.0, "bw": 125.0, "sf": 9, "cr": 5, "txp": 17},
     "865–867 MHz band."),
    ("asia923", "Asia (varies)",
     {"freq": 923.0, "bw": 125.0, "sf": 9, "cr": 5, "txp": 16},
     "AS923 923 MHz band — confirm your local allocation before deploying."),
]

_PRESET_BY_KEY = {k: (label, params, note)
                  for k, label, params, note in REGIONAL_PRESETS}


def _coerce(params: Dict) -> Dict[str, float]:
    """Return a full 5-key param dict: valid numeric values from *params*, each
    missing/garbled key filled from the canonical default. freq/bw are floats;
    sf/cr/txp are ints."""
    out: Dict[str, float] = {}
    for k in PARAM_KEYS:
        cast = float if k in ("freq", "bw") else int
        try:
            out[k] = cast(params[k])
        except (KeyError, TypeError, ValueError):
            out[k] = DEFAULT_PARAMS[k]
    return out


def preset_keys() -> List[str]:
    return [k for k, *_ in REGIONAL_PRESETS]


def preset_label(key: str) -> Optional[str]:
    p = _PRESET_BY_KEY.get(key)
    return p[0] if p else None


def preset_note(key: str) -> Optional[str]:
    p = _PRESET_BY_KEY.get(key)
    return p[2] if p else None


def preset_params(key: str) -> Optional[Dict[str, float]]:
    """The five parameters for a regional preset (a fresh copy), or None if the
    key is unknown."""
    p = _PRESET_BY_KEY.get(key)
    return _coerce(p[1]) if p else None


def load_defaults(path: str = CONFIG) -> Dict[str, float]:
    """The saved tool-wide defaults, or the canonical default if none/garbled.
    Always returns a full, validated 5-key dict."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT_PARAMS)
    return _coerce(data if isinstance(data, dict) else {})


def save_defaults(params: Dict, path: str = CONFIG) -> Dict[str, float]:
    """Persist *params* (coerced to a full valid set) as the tool-wide defaults.
    Returns the stored dict."""
    coerced = _coerce(params)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(coerced, f, indent=2)
    return coerced


def summary(params: Dict) -> str:
    """A one-line human summary, matching the birth-cert style."""
    p = _coerce(params)
    return (f"{p['freq']:g} MHz / BW{p['bw']:g} / SF{p['sf']} / "
            f"CR{p['cr']} / {p['txp']} dBm")
