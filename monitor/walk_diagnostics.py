"""What the signal readings of a boundary walk SAY — slope or cliff, and
what would actually help (operator, 2026-09-21, after the first walk came
back as distance-to-silence with no way to tell an antenna from a house).

Pure: samples in, numbers and a verdict out. The screen turns the verdict
into sentences (monitor/ speaks no language); the reasoning lives here so
it can be tested against synthetic walks.

The physics is rule-of-thumb and says so:
  * free-space path loss at 915 MHz sets what "open air" would deliver at a
    given distance — the DEFICIT against it at the node's doorstep is the
    antenna-or-obstruction number;
  * a LoRa modem decodes down to a per-SF SNR limit (SF9 ≈ -12.5 dB); the
    last answer's SNR above that limit is the MARGIN the link died with;
  * a link that died with healthy margin hit a CLIFF (something between
    the two radios) — no spreading factor fixes that. One that died with
    the margin gone ran down a SLOPE, and more budget (SF, TX power) buys
    range in proportion to the fitted path-loss exponent.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

from monitor.link_margin import SF9_SNR_LIMIT

#: Approximate LoRa demodulation SNR limits by spreading factor (dB).
SNR_LIMIT_BY_SF: Dict[int, float] = {7: -7.5, 8: -10.0, 9: SF9_SNR_LIMIT,
                                     10: -15.0, 11: -17.5, 12: -20.0}
#: A quiet 915 MHz channel at 125 kHz, dBm — anything well above it is
#: budget spent on somebody else's noise before the walk starts.
QUIET_FLOOR_DBM = -115
#: "At the node's doorstep": hits inside this radius calibrate the antennas.
NEAR_M = 60.0
#: A doorstep reading this far under open-air is an antenna/obstruction flag.
DOORSTEP_DEFICIT_DB = 30.0
#: Margin the last answer must still have had for silence to be a cliff.
CLIFF_MARGIN_DB = 12.0
#: Fallback path-loss exponent when the walk cannot fit one (suburban).
DEFAULT_EXPONENT = 3.5
#: Fewest hits, and the distance ratio they must span, to fit an exponent.
FIT_MIN_POINTS = 4
FIT_MIN_SPAN = 3.0
#: What each change buys, dB, over the SF9 / 17 dBm canonical set.
EXTRA_DB = {"sf10": 2.5, "sf11": 5.0, "sf12": 7.5, "txp22_both": 5.0}


def fspl_db(distance_m: float, freq_mhz: float = 915.125) -> float:
    """Free-space path loss, dB (isotropic both ends)."""
    d_km = max(distance_m, 1.0) / 1000.0
    return 20 * math.log10(d_km) + 20 * math.log10(freq_mhz) + 32.44


def open_air_rssi(distance_m: float, tx_dbm: float,
                  freq_mhz: float = 915.125) -> float:
    """What a 0 dBi antenna would hear in open air at *distance_m*."""
    return tx_dbm - fspl_db(distance_m, freq_mhz)


def fit_path_loss(points: Sequence[Tuple[float, float]]
                  ) -> Optional[Tuple[float, float]]:
    """Least squares of RSSI against 10·log10(distance): returns
    (exponent n, intercept A) for RSSI = A − n·10·log10(d_m), or None when
    the walk does not carry enough spread to say."""
    pts = [(d, r) for d, r in points if d >= 1.0 and r is not None]
    if len(pts) < FIT_MIN_POINTS:
        return None
    ds = [d for d, _ in pts]
    if max(ds) / min(ds) < FIT_MIN_SPAN:
        return None
    xs = [10 * math.log10(d) for d, _ in pts]
    ys = [r for _, r in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    n = -slope
    a = my + n * mx
    return (round(n, 2), round(a, 1))


def medic_radio_params(config_text: str) -> Dict[str, float]:
    """The RNode stanza of the medic's own rnsd config → {sf, txp, bw_khz,
    freq_mhz}; each key missing when the stanza does not say."""
    out: Dict[str, float] = {}
    in_rnode = False
    for line in (config_text or "").splitlines():
        if re.match(r"^\s*\[+[^\]]+\]+\s*$", line):
            in_rnode = False
            continue
        m = re.match(r"^\s*(\w+)\s*=\s*(\S+)", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if k == "type":
            in_rnode = (v == "RNodeInterface")
            continue
        if not in_rnode:
            continue
        try:
            if k == "spreadingfactor":
                out["sf"] = int(v)
            elif k == "txpower":
                out["txp"] = int(v)
            elif k == "bandwidth":
                out["bw_khz"] = int(v) / 1000.0
            elif k == "frequency":
                out["freq_mhz"] = int(v) / 1e6
        except ValueError:
            continue
    return out


def diagnose(samples: Sequence[dict], sf: int = 9, tx_dbm: float = 17,
             freq_mhz: float = 915.125,
             noise_floor_dbm: Optional[float] = None) -> dict:
    """The walk's signal story as numbers. ``verdict`` is one of
    ``no_signal`` (no answered ping carried a reading), ``cliff``,
    ``slope``, or ``open`` (never went silent — no edge to judge)."""
    hits = [s for s in samples if s.get("connected") and s.get("km") is not None
            and s.get("rssi_dbm") is not None and s.get("direct") is not False]
    out: dict = {"readings": len(hits), "sf": sf, "tx_dbm": tx_dbm,
                 "verdict": "no_signal", "exponent": None, "last": None,
                 "margin_db": None, "doorstep": None, "noise_penalty_db": None,
                 "predicted_extra_m": {}}
    if noise_floor_dbm is not None:
        pen = round(noise_floor_dbm - QUIET_FLOOR_DBM, 1)
        out["noise_penalty_db"] = pen if pen > 3 else 0.0
    if not hits:
        return out

    limit = SNR_LIMIT_BY_SF.get(int(sf), SF9_SNR_LIMIT)
    last = max(hits, key=lambda s: s["t"])
    last_m = last["km"] * 1000.0
    out["last"] = {"m": round(last_m), "rssi_dbm": last["rssi_dbm"],
                   "snr_db": last.get("snr_db")}
    if last.get("snr_db") is not None:
        out["margin_db"] = round(last["snr_db"] - limit, 1)
    elif noise_floor_dbm is not None:
        out["margin_db"] = round(last["rssi_dbm"] - (noise_floor_dbm + limit), 1)

    # The doorstep: what the antennas deliver before any obstruction.
    near = [s for s in hits if s["km"] * 1000.0 <= NEAR_M
            and s["km"] * 1000.0 >= 5.0]
    if near:
        near.sort(key=lambda s: s["rssi_dbm"])
        med = near[len(near) // 2]
        d_m = med["km"] * 1000.0
        expect = open_air_rssi(d_m, tx_dbm, freq_mhz)
        deficit = round(expect - med["rssi_dbm"], 1)
        out["doorstep"] = {"m": round(d_m), "rssi_dbm": med["rssi_dbm"],
                           "open_air_dbm": round(expect, 1),
                           "deficit_db": deficit,
                           "flag": deficit >= DOORSTEP_DEFICIT_DB}

    fit = fit_path_loss([(s["km"] * 1000.0, s["rssi_dbm"]) for s in hits])
    out["exponent"] = fit[0] if fit else None

    # Did the walk find an edge? Misses placed beyond the last hit.
    misses_beyond = [s for s in samples if not s.get("connected")
                     and s.get("km") is not None
                     and s["km"] * 1000.0 > last_m]
    if not misses_beyond:
        out["verdict"] = "open"
        return out
    margin = out["margin_db"]
    if margin is not None and margin >= CLIFF_MARGIN_DB:
        out["verdict"] = "cliff"
    else:
        out["verdict"] = "slope"
        n = out["exponent"] or DEFAULT_EXPONENT
        for key, extra in EXTRA_DB.items():
            factor = 10 ** (extra / (10.0 * n))
            out["predicted_extra_m"][key] = round(last_m * (factor - 1))
    return out


# -- persistence: the diagnosis outlives the popup ---------------------------

_WALK_DIR = "~/.reticulum-node-medic"
_DIAG_FILE = "walk_diagnoses.jsonl"


def append_diagnosis(node_key: str, diagnosis: dict,
                     base_dir: str = _WALK_DIR, now=time.time,
                     name: str = "") -> None:
    base = os.path.expanduser(base_dir)
    os.makedirs(base, exist_ok=True)
    rec = {"t": now(), "node": node_key, "name": name, **diagnosis}
    with open(os.path.join(base, _DIAG_FILE), "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def latest_diagnosis(node_key: str, name: str = "",
                     base_dir: str = _WALK_DIR) -> Optional[dict]:
    """The newest diagnosis for a node, matched by mesh key or by name (a
    VITALS row can be keyed rtnode:<name> while the walk banked the hash)."""
    best = None
    for d in load_diagnoses(base_dir):
        if d.get("node") == node_key or (name and d.get("name") == name):
            if best is None or d.get("t", 0) >= best.get("t", 0):
                best = d
    return best


def load_diagnoses(base_dir: str = _WALK_DIR) -> List[dict]:
    path = os.path.join(os.path.expanduser(base_dir), _DIAG_FILE)
    out: List[dict] = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out
