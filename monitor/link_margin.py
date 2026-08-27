"""Per-link SNR margin — the number that matters most for a node's antenna.

The 2026-08-27 antenna campaign proved the noise floor alone can't rank a
link (a better ear hears more noise; a matched sponge hears nothing). The
metric that rolls everything up is the SNR margin of an ACTUAL packet from
the node in question: how far its signal lands above what the radio can
decode. This module attributes that packet honestly.

Attribution works because the health-poll flow stamps ``sent_at`` before the
0x01 request leaves, and the medic's own radio (via monitor/serial_splitter)
records ``last_rssi``/``last_snr``/``packet_heard_at`` for every packet it
hears. A packet heard AFTER the request went out, on a mesh this quiet, is
the node's reply (or at worst its relay's last hop — which is still the RF
the medic actually receives from that direction, the honest thing to show).
No packet in the window -> None, never a stale number.

LoRa context for the verdicts: at the canonical SF9 the modem decodes down
to roughly -12 dB SNR — so a reply at +10 dB SNR carries ~22 dB of headroom
before the link dies, while one at -8 dB is living on the edge.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from monitor.geo import SPLITTER_STATE

#: SF9's approximate demodulation floor, dB SNR (canonical radio params).
SF9_SNR_LIMIT = -12.5

#: verdict thresholds on the reply's SNR (dB)
STRONG_SNR = 7.0
OK_SNR = 0.0
THIN_SNR = -7.0


@dataclass(frozen=True)
class LinkMargin:
    rssi_dbm: int
    snr_db: Optional[float]
    floor_dbm: Optional[int]
    heard_at: float

    @property
    def headroom_db(self) -> Optional[float]:
        """dB between the reply's SNR and the SF9 decode limit — how much
        the link can degrade before it stops existing."""
        if self.snr_db is None:
            return None
        return round(self.snr_db - SF9_SNR_LIMIT, 1)

    @property
    def over_floor_db(self) -> Optional[int]:
        if self.floor_dbm is None:
            return None
        return self.rssi_dbm - self.floor_dbm

    @property
    def verdict(self) -> str:
        """"strong" | "ok" | "thin" | "edge" — by the reply's SNR; falls
        back to over-floor when the radio gave no SNR."""
        s = self.snr_db
        if s is None:
            of = self.over_floor_db
            if of is None:
                return "ok"
            s = float(of) + SF9_SNR_LIMIT  # rough: floor ~ decode threshold ref
        if s >= STRONG_SNR:
            return "strong"
        if s >= OK_SNR:
            return "ok"
        if s >= THIN_SNR:
            return "thin"
        return "edge"


def link_margin(sent_at: float, path: str = SPLITTER_STATE,
                now=time.time) -> Optional[LinkMargin]:
    """The RF stats of the packet heard SINCE *sent_at*, or None.

    None whenever the claim can't be honest: no splitter state, no packet
    heard after the send, a stale snapshot, or an unbelievable RSSI. A
    missing margin is shown as nothing — never a guess.
    """
    try:
        with open(path) as f:
            st = json.load(f)
    except Exception:                       # noqa: BLE001 — no state = no claim
        return None
    heard = st.get("packet_heard_at")
    rssi = st.get("last_rssi")
    if heard is None or rssi is None:
        return None
    if sent_at is None or heard < sent_at:
        return None                         # nothing heard since the request
    if heard > now() + 2:
        return None                         # clock nonsense — refuse
    if not (-150 <= rssi <= -10):
        return None
    snr = st.get("last_snr")
    floor = st.get("noise_floor")
    if floor is not None and not (-150 <= floor <= -40):
        floor = None
    return LinkMargin(rssi_dbm=int(rssi),
                      snr_db=float(snr) if snr is not None else None,
                      floor_dbm=int(floor) if floor is not None else None,
                      heard_at=float(heard))
