"""Live TRIAGE signal feed — reads the serial splitter's skimmed state.

The splitter (monitor.serial_splitter) records per-packet RSSI/SNR and the
periodic channel stats (noise floor, airtime) as they pass through to rnsd, and
writes them to its JSON state file. This adapts that file into the reader the
TriageScreen polls: ``() -> {"snr", "rssi", "noise", "peers"} | None``.

Per-packet metrics only move when a peer transmits (an announce, a beacon); the
Triage score deliberately HOLDS between packets, so returning the last-heard
values is correct — but if the state file itself goes stale (splitter/radio
down) the feed returns None and the screen freezes rather than lies.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

SPLITTER_STATE = os.path.expanduser("~/gps_state.json")


def live_triage_feed(path: str = SPLITTER_STATE, max_age_s: float = 30.0,
                     now: Callable[[], float] = time.time
                     ) -> Callable[[], Optional[dict]]:
    """A TriageScreen feed sourced from the splitter's state file. Yields a
    sample once at least one packet has been heard; ``None`` while the radio is
    silent-from-birth, the file is missing/stale, or fields are absent."""
    def reader() -> Optional[dict]:
        try:
            with open(path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return None
        upd = st.get("updated")
        if not isinstance(upd, (int, float)) or (now() - upd) > max_age_s:
            return None                              # splitter not feeding
        rssi, snr = st.get("last_rssi"), st.get("last_snr")
        noise = st.get("noise_floor")
        if rssi is None or snr is None or noise is None:
            return None                              # no packet heard yet
        return {"snr": snr, "rssi": rssi, "noise": noise,
                "peers": st.get("peers", 0)}
    return reader
