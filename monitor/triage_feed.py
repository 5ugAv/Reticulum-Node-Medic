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

# ONE definition, in monitor.geo. This file used to carry its own copy of the
# path, which meant the tmpfs migration (2026-08-07) would have left Triage
# reading a file that had stopped being written.
from monitor.geo import SPLITTER_STATE  # noqa: F401  (re-exported)


def feed_choice(mode: str, demo_ok: bool, state_present: bool) -> str:
    """Which feed the ANTENNA bullseye reads: ``"live"`` or ``"demo"``.

    On the deployed medic (``demo_ok`` False — see ui.hw_factories.demo_allowed)
    the answer is live, full stop: the live feed yields None while the radio
    is silent, which is what drives the honest NOT READING cover. Until
    2026-10-03 the choice was "live if the splitter's state file exists at
    app start, else demo" — on a medic the file lives on tmpfs, so after
    every reboot ANTENNA opened on a wandering fake signal with "Peers 2
    heard" and stayed on it for the session (readiness sweep). A dev box with
    no radio keeps the demo so the screen is explorable; RNM_TRIAGE=live|demo
    overrides, demo only where demos are allowed.
    """
    if mode == "live":
        return "live"
    if not demo_ok:
        return "live"
    if mode == "demo":
        return "demo"
    return "live" if state_present else "demo"


def live_triage_feed(path: str = SPLITTER_STATE, max_age_s: float = 30.0,
                     now: Callable[[], float] = time.time,
                     max_packet_age_s: float = 120.0,
                     ) -> Callable[[], Optional[dict]]:
    """A TriageScreen feed sourced from the splitter's state file. Yields a
    sample once at least one packet has been heard; ``None`` while the radio is
    silent-from-birth, the file is missing/stale, or fields are absent.

    A full sample HOLDS the last packet's RSSI/SNR only while that packet is
    recent (*max_packet_age_s*). The splitter refreshes ``updated`` on every
    channel-stats frame, so a node that went quiet an hour ago used to keep
    scoring as if it were still transmitting, and the "isn't answering"
    watchdog could never fire once anything had ever been heard (readiness
    sweep, 2026-10-03). Past that age the sample is partial: live noise, no
    packet."""
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
        if noise is None:
            return None                              # radio not reporting at all
        heard = st.get("packet_heard_at")
        stale_packet = (isinstance(heard, (int, float))
                        and (now() - heard) > max_packet_age_s)
        if rssi is None or snr is None or stale_packet:
            # no packet heard yet (or none for a while): the noise floor is
            # still LIVE (it moves as the antenna is handled) — a partial
            # sample keeps the screen alive and honest while it waits to hear
            # another node
            return {"noise": noise, "rssi": None, "snr": None,
                    "peers": 0, "partial": True}
        return {"snr": snr, "rssi": rssi, "noise": noise,
                "peers": st.get("peers", 0),
                # WHEN the packet was heard, so the screen can tell a packet
                # that arrived after it asked for the beacon from one heard
                # before it opened (readiness ledger #26)
                "heard_at": heard if isinstance(heard, (int, float)) else None}
    return reader
