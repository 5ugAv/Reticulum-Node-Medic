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

import math
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


# -- delivery honesty: warm the path, send, then believe only what was heard --
#
# Two field lessons (proven 2026-08-21/22) that make a naive fire-and-forget
# 0x01 lie in both directions:
#
# 1. SILENT EPHEMERAL DROP: an RNS packet sent while ``Transport.has_path()``
#    is False dies without an error in THIS process — even when rnpath, talking
#    to the shared rnsd daemon, just proved the path. The sending stack must
#    hold the path itself: request it, then wait for it, before sending.
# 2. REPLY THROTTLING: the firmware rations poll replies to spend less shared
#    airtime, so a node polled repeatedly goes reply-silent while perfectly
#    healthy. Silence after a delivered request is "did not answer" — never
#    "is down".
#
# The transports are injected callables so this logic is testable without a
# live mesh (and without Kivy), same pattern as HealthPoller above.

#: Outcomes of ``warm_and_send`` — three DISTINCT truths, because the honest
#: wording differs: "no route" means nothing ever left this medic; "unanswered"
#: means the request was delivered into the mesh and silence followed.
DELIVERY_ANSWERED = "answered"
DELIVERY_UNANSWERED = "unanswered"
DELIVERY_NO_ROUTE = "no_route"


def warm_path(
    dest_hash: bytes,
    has_path: Callable[[bytes], bool],
    request_path: Callable[[bytes], None],
    wait_s: float = 15.0,
    poll_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True once this stack holds a path to *dest_hash*; False if none arrives.

    Already-known path -> immediate True, no request, no waiting. Otherwise one
    ``request_path`` then a poll of ``has_path`` once per *poll_s*, capped at
    *wait_s* — the caller is expected to run this OFF the UI thread.
    """
    if has_path(dest_hash):
        return True
    request_path(dest_hash)
    polls = math.ceil(wait_s / poll_s) if wait_s > 0 and poll_s > 0 else 0
    for _ in range(polls):
        sleep(poll_s)
        if has_path(dest_hash):
            return True
    return False


def warm_and_send(
    dest_hash: bytes,
    has_path: Callable[[bytes], bool],
    request_path: Callable[[bytes], None],
    send: Callable[[bytes], None],
    await_reply: Callable[[bytes, float], bool],
    path_wait_s: float = 15.0,
    reply_wait_s: float = 15.0,
    poll_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """One honest delivery attempt: warm the path, send, listen.

    Returns DELIVERY_NO_ROUTE (path never resolved — ``send`` was NEVER
    called, because it would have been a silent ephemeral drop),
    DELIVERY_ANSWERED (sent and ``await_reply`` heard the node), or
    DELIVERY_UNANSWERED (sent, then silence — which throttling makes a
    normal thing for a healthy node to do).
    """
    if not warm_path(dest_hash, has_path, request_path,
                     wait_s=path_wait_s, poll_s=poll_s, sleep=sleep):
        return DELIVERY_NO_ROUTE
    send(dest_hash)
    if await_reply(dest_hash, reply_wait_s):
        return DELIVERY_ANSWERED
    return DELIVERY_UNANSWERED


def heard_since(
    get_record: Callable[[str], object],
    watch_hashes,
    sent_at: float,
    wait_s: float,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 1.0,
) -> bool:
    """True once any watched record shows a GENUINE post-send word from the
    node; False when *wait_s* passes without one.

    The oracle is ``last_heard_announce_at`` — the registry stamp written only
    by non-echo announce/beacon ingest — and deliberately NOT ``last_seen``:
    last_seen also moves on mesh path-table folds, so a rediscover tick landing
    inside the window would have faked an answer from a node dead for an hour
    (the SolarLove class of lie, in miniature). A record that is missing or
    unreadable mid-watch is simply not an answer.

    Ends with one final look AFTER the deadline: the reply is most likely to
    land late in the window, and exiting on the clock alone read a reply at
    14.3 s as silence.
    """
    def _check() -> bool:
        for h in watch_hashes:
            try:
                rec = get_record(h)
                ts = getattr(rec, "last_heard_announce_at", None)
            except Exception:
                continue                  # vanished mid-watch != answered
            if ts is not None and ts >= sent_at:
                return True
        return False

    deadline = now() + wait_s
    while now() < deadline:
        if _check():
            return True
        sleep(poll_s)
    return _check()
