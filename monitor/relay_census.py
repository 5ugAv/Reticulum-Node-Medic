"""Count relayed announces the medic already receives — a zero-airtime census.

WHY THIS EXISTS
The medic can only infer a node-to-node link from RNS's path table: a row
saying "I reach Y via X" implies X<->Y (monitor/topology.py, the `elif via:`
branch). RNS holds no topology by design, so any node inside the medic's own
radio range routes straight to the medic, no relayed row appears, and the medic
cannot tell that two such nodes hear each other even when they plainly do.

But the evidence may already be arriving. When a node relays an announce, RNS
stamps the RELAYING node's transport identity into the packet header
(Transport.mangle_hops(..., transport_insert=True)) and increments hops. A copy
of Y's announce carrying transport_id X is proof that X heard Y — stated in the
packet, not inferred from timing or signal strength. Those transmissions happen
whether or not we look, so reading them costs no airtime and leaks nothing new.

The catch is that RNS's app-level announce handlers are dispatched only for a
BETTER path, so the medic's own handler never sees the relayed copy. The bytes
still pass the radio port, and the splitter already owns that port.

THIS MODULE ONLY COUNTS. It builds no edges and changes no behaviour. The
question it answers is the cheap one that must come first: does this fleet relay
at all? If the census stays at zero, the whole approach is dead and we have
learned that for free, instead of designing a wire format for evidence that
never arrives.

SAFETY
Every entry point is wrapped by the caller so that a fault here can never break
the byte stream to rnsd. This sits in the medic's live radio path; counting is
never worth a dropped packet. See KissGpsSplitter.feed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Optional

#: KISS command for a data frame carrying an RNS packet (RNodeInterface).
CMD_DATA = 0x00

#: RNS header field widths, from RNS.Packet.unpack / RNS.Reticulum.
_DST_LEN = 16                     # TRUNCATED_HASHLENGTH // 8
_HEADER_2 = 1                     # flags bit 6 set -> a transport_id is present

CENSUS_PATH = os.path.expanduser("~/.reticulum-node-medic/relay_census.json")

#: Announce packet type, from RNS.Packet (PACKET_TYPES: DATA/ANNOUNCE/LINK/PROOF).
_PT_ANNOUNCE = 0x01


class RelayCensus:
    """Tally of relayed packets seen on the wire. Counts only."""

    def __init__(self, path: str = CENSUS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.frames = 0               # CMD_DATA frames examined
        self.header2 = 0              # ...carrying a transport_id at all
        self.relayed_announces = 0    # ...that are announces with hops >= 1
        #: relay identity (hex) -> count. WHO is relaying, which is the datum
        #: that would later make an edge attributable.
        self.relays: Dict[str, int] = {}
        #: relay -> set of destination hashes it was seen carrying, as counts.
        self.pairs: Dict[str, Dict[str, int]] = {}
        self._last_save = 0.0

    def observe(self, frame: bytes) -> None:
        """Examine ONE unescaped KISS frame. Never raises to the caller."""
        try:
            if not frame or frame[0] != CMD_DATA:
                return
            pkt = frame[1:]
            if len(pkt) < 2 + 2 * _DST_LEN + 1:
                return
            self.frames += 1
            flags, hops = pkt[0], pkt[1]
            if ((flags & 0b01000000) >> 6) != _HEADER_2:
                return                       # no transport_id -> nothing to learn
            self.header2 += 1
            if (flags & 0b00000011) != _PT_ANNOUNCE:
                return                       # only announces name a destination
                                             # we can attribute this way
            if hops < 1:
                return
            transport_id = pkt[2:2 + _DST_LEN].hex()
            dst = pkt[2 + _DST_LEN:2 + 2 * _DST_LEN].hex()
            self.relayed_announces += 1
            self.relays[transport_id] = self.relays.get(transport_id, 0) + 1
            seen = self.pairs.setdefault(transport_id, {})
            seen[dst] = seen.get(dst, 0) + 1
            self._maybe_save()
        except Exception:
            return                           # counting must never cost a packet

    def snapshot(self) -> dict:
        return {
            "started_at": self.started_at,
            "updated_at": time.time(),
            "hours": round((time.time() - self.started_at) / 3600.0, 2),
            "data_frames": self.frames,
            "with_transport_id": self.header2,
            "relayed_announces": self.relayed_announces,
            "relays": dict(self.relays),
            "pairs": {k: dict(v) for k, v in self.pairs.items()},
        }

    def _maybe_save(self, every_s: float = 60.0) -> None:
        now = time.time()
        if now - self._last_save < every_s:
            return
        self._last_save = now
        self.save()

    def save(self) -> None:
        try:
            with self._lock:
                tmp = self.path + ".tmp"
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(tmp, "w") as f:
                    json.dump(self.snapshot(), f, indent=2)
                os.replace(tmp, self.path)
        except Exception:
            return


def load(path: str = CENSUS_PATH) -> Optional[dict]:
    """Read the census written by a running splitter, or None."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None
