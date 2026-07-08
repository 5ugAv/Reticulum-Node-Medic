"""On-demand health poll — pull a Type B node's FULL health right now.

Complements the periodic push beacon: when an operator is checking a node that
showed disruption in the last 2h/24h, the tool sends a tiny request packet to
the node's ``rtnode.health`` destination. The node replies by emitting an
immediate beacon (the same 14-byte payload as a scheduled one), which the
tool's existing announce handler + ``health_beacon.decode`` already handle. If
the fresh reply is clean, the node's red/orange warning clears to green.

Transport is injected so this is testable without a live mesh:
- ``send_request(dest_hash, payload)`` fires the request packet.
- ``await_beacon(dest_hash, timeout_s)`` blocks until the next beacon from that
  hash arrives (correlation is by destination hash + freshness — the reply is a
  broadcast announce, not addressed back to us, so no nonce is needed), or
  returns ``None`` on timeout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from monitor.health_beacon import HealthBeacon, beacon_status

#: Request opcodes (1 byte). 0x01 is the only one today; the byte leaves room
#: for future request types (reboot, extended diag, identify) without a
#: format scramble.
OPCODE_FULL_HEALTH = 0x01


def build_request(opcode: int = OPCODE_FULL_HEALTH) -> bytes:
    return bytes([opcode & 0xFF])


@dataclass
class PollResult:
    #: "ok" | "warn" | "alert" | "unreachable"
    node_status: str
    reachable: bool
    attempts: int
    beacon: Optional[HealthBeacon]

    @property
    def resolves_warning(self) -> bool:
        """True only when a fresh, clean reply justifies clearing a node's
        red/orange warning back to green."""
        return self.reachable and self.node_status == "ok"


class HealthPoller:
    def __init__(
        self,
        send_request: Callable[[bytes, bytes], None],
        await_beacon: Callable[[bytes, float], Optional[HealthBeacon]],
        retries: int = 3,
        timeout_s: float = 15.0,
        backoff_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._send = send_request
        self._await = await_beacon
        self.retries = retries
        self.timeout_s = timeout_s
        self.backoff_s = backoff_s
        self._sleep = sleep

    def poll(self, dest_hash: bytes) -> PollResult:
        payload = build_request()
        for attempt in range(1, self.retries + 1):
            self._send(dest_hash, payload)
            beacon = self._await(dest_hash, self.timeout_s)
            if beacon is not None:
                return PollResult(
                    node_status=beacon_status(beacon),
                    reachable=True,
                    attempts=attempt,
                    beacon=beacon,
                )
            # No reply this round; back off (respecting LoRa duty cycle) unless
            # that was the last attempt.
            if attempt < self.retries:
                self._sleep(self.backoff_s)

        # Silence after every retry is itself a signal: the node is likely down.
        return PollResult(
            node_status="unreachable",
            reachable=False,
            attempts=self.retries,
            beacon=None,
        )
