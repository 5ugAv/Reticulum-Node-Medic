"""Classify a plugged-in / reachable node as a BIRTH or an ADOPT candidate.

When the operator connects hardware for BIRTH, the medic reads what's already on
it and decides: is this a fresh/foreign board that needs building (BIRTH), or is
it ALREADY one of our nodes — running our firmware, on our canonical radio
settings, and beaconing its health — that just needs to be enrolled as kin
(ADOPT)?

The two inputs are cheap to obtain and either alone is enough:
  * the firmware's serial BOOT BANNER — every RTNode boot prints its LoRa config
    (``[Boundary] LoRa: freq=... bw=... sf=... cr=... txp=...``) and its health
    destination (``[HealthBeacon] init dst=<hash>``). The dst hash IS the kin key.
  * the node's HTTP ``/status`` JSON (over WiFi once it's joined) — carries the
    node name, board, firmware version and radio online state.

This module is PURE (text/JSON in, verdict out) so it unit-tests without a board;
the live serial/HTTP reads live in the caller.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from node_profile import RadioConfig

#: Canonical radio contract every node on this mesh must match (single source of
#: truth = RadioParams defaults). A node running our firmware but OFF these is a
#: reconfigure/BIRTH case, not a silent ADOPT — we never pull an off-spec node in.
_R = RadioConfig()
CANONICAL = {
    "freq": int(round(_R.frequency_mhz * 1_000_000)),   # 915125000 Hz
    "bw": int(round(_R.bandwidth_khz * 1000)),          # 125000 Hz
    "sf": _R.spreading_factor,                          # 9
    "cr": _R.coding_rate,                               # 5
    "txp": _R.tx_power_dbm,                             # 17
}

# Boot-banner patterns (Grey Hat's RTNode-2400 "Boundary" firmware).
_LORA_RE = re.compile(
    r"LoRa:\s*freq=(\d+)\s+bw=(\d+)\s+sf=(\d+)\s+cr=(\d+)\s+txp=(-?\d+)")
_BEACON_DST_RE = re.compile(r"\[HealthBeacon\][^\n]*dst=([0-9a-fA-F]{8,})")
#: Markers that say "this is OUR stack" (vs a blank board or foreign firmware).
_OURS_MARKERS = ("[Boundary]", "[HealthBeacon]", "Starting RNS", "Reticulum",
                 "RTNode", "Transport mode")


def parse_banner(text: str) -> dict:
    """Pull identity + radio params + our-firmware markers out of a serial banner."""
    text = text or ""
    out: dict = {"is_ours": any(m in text for m in _OURS_MARKERS)}
    m = _BEACON_DST_RE.search(text)
    if m:
        out["identity_hash"] = m.group(1).lower()
    m = _LORA_RE.search(text)
    if m:
        out["params"] = {"freq": int(m.group(1)), "bw": int(m.group(2)),
                         "sf": int(m.group(3)), "cr": int(m.group(4)),
                         "txp": int(m.group(5))}
    return out


def parse_status(status_json: Optional[str]) -> dict:
    """Pull name/board/firmware/online out of an RTNode GET /status body."""
    if not status_json:
        return {}
    try:
        j = json.loads(status_json)
    except (ValueError, TypeError):
        return {}
    out: dict = {}
    if j.get("fork") or j.get("rnode_proto"):
        out["is_ours"] = True
    for src, dst in (("node_name", "node_name"), ("board", "board"),
                     ("fw_version", "firmware"), ("lora_online", "lora_online"),
                     ("wifi_ip", "wifi_ip")):
        if src in j:
            out[dst] = j[src]
    return out


def params_match(params: Optional[dict]) -> bool:
    """True only if EVERY canonical field is present and equal."""
    if not params:
        return False
    return all(params.get(k) == v for k, v in CANONICAL.items())


def classify(banner: str = "", status_json: Optional[str] = None) -> dict:
    """Decide BIRTH vs ADOPT from whatever we could read off the board.

    Returns a dict:
      kind          "adopt" | "birth"
      is_ours       running our firmware/stack
      beaconing     a health-beacon identity was found
      params        the node's radio params (or None)
      params_ok     params match the canonical contract
      identity_hash the health-beacon dest = the kin key (or None)
      node_name     the node's own name, if known
      firmware/board detail for display
      reason        one-line human explanation of the verdict
    """
    b = parse_banner(banner)
    s = parse_status(status_json)
    info: dict = {
        "is_ours": bool(b.get("is_ours") or s.get("is_ours")),
        "identity_hash": b.get("identity_hash"),
        "params": b.get("params"),
        "node_name": s.get("node_name"),
        "firmware": s.get("firmware"),
        "board": s.get("board"),
        "wifi_ip": s.get("wifi_ip"),
        "lora_online": s.get("lora_online"),
    }
    info["beaconing"] = bool(info["identity_hash"])
    info["params_ok"] = params_match(info["params"])

    # ADOPT only when it's genuinely one of ours, already beaconing, AND on-spec.
    if info["is_ours"] and info["beaconing"] and info["params_ok"]:
        info["kind"] = "adopt"
        info["reason"] = "Already our node, on canonical settings and beaconing."
    elif info["is_ours"] and info["beaconing"] and not info["params_ok"]:
        info["kind"] = "birth"
        info["reason"] = ("Our firmware but OFF canonical radio settings — "
                          "re-birth to bring it on-spec.")
    elif info["is_ours"]:
        info["kind"] = "birth"
        info["reason"] = "Our firmware but not yet beaconing — finish setup via birth."
    else:
        info["kind"] = "birth"
        info["reason"] = "Fresh or foreign board — build it."
    return info
