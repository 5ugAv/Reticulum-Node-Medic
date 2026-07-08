import pytest

from monitor.health_beacon import encode, decode
from monitor.health_poll import (
    HealthPoller,
    PollResult,
    build_request,
    OPCODE_FULL_HEALTH,
)


def beacon(**over):
    kw = dict(
        uptime_s=7200, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
        wifi_up=True, lora_up=True, tcp_backbone_up=True,
        local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
        board_id=0x3F, fw=(0, 6, 2),
    )
    kw.update(over)
    return decode(encode(**kw))


class FakeChannel:
    """Records requests; returns a canned beacon (or None = timeout) per attempt."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self._i = 0

    def send(self, dest_hash, payload):
        self.requests.append((dest_hash, payload))

    def await_beacon(self, dest_hash, timeout_s):
        r = self.replies[self._i] if self._i < len(self.replies) else None
        self._i += 1
        return r


def poller(channel, retries=3):
    return HealthPoller(
        send_request=channel.send,
        await_beacon=channel.await_beacon,
        retries=retries,
        timeout_s=15.0,
        backoff_s=2.0,
        sleep=lambda s: None,
    )


HASH = b"\x37\x8a\xc3\xed"  # stand-in destination hash


def test_request_opcode_byte():
    assert build_request() == bytes([OPCODE_FULL_HEALTH])
    assert OPCODE_FULL_HEALTH == 0x01


def test_poll_clean_reply_clears_to_green():
    ch = FakeChannel([beacon()])
    result = poller(ch).poll(HASH)
    assert isinstance(result, PollResult)
    assert result.reachable is True
    assert result.node_status == "ok"
    assert result.attempts == 1
    assert result.resolves_warning is True
    # a request was actually sent to the node's hash, carrying the opcode
    assert ch.requests[0][0] == HASH
    assert ch.requests[0][1] == bytes([OPCODE_FULL_HEALTH])


def test_poll_fault_reply_stays_red():
    ch = FakeChannel([beacon(fault=True)])
    result = poller(ch).poll(HASH)
    assert result.reachable is True
    assert result.node_status == "alert"
    assert result.resolves_warning is False


def test_poll_warn_reply_does_not_clear():
    ch = FakeChannel([beacon(wifi_rssi_dbm=-80)])
    result = poller(ch).poll(HASH)
    assert result.node_status == "warn"
    assert result.resolves_warning is False


def test_poll_retries_then_succeeds():
    ch = FakeChannel([None, None, beacon()])
    result = poller(ch).poll(HASH)
    assert result.attempts == 3
    assert result.node_status == "ok"
    assert result.reachable is True
    # one request per attempt
    assert len(ch.requests) == 3


def test_poll_no_reply_after_retries_is_unreachable():
    ch = FakeChannel([None, None, None])
    result = poller(ch, retries=3).poll(HASH)
    assert result.reachable is False
    assert result.node_status == "unreachable"
    assert result.attempts == 3
    assert result.resolves_warning is False
    assert result.beacon is None


def test_poll_stops_sending_once_answered():
    ch = FakeChannel([beacon(), beacon(), beacon()])
    poller(ch).poll(HASH)
    # answered on first attempt -> exactly one request sent
    assert len(ch.requests) == 1


def test_backoff_sleeps_between_failed_attempts():
    slept = []
    ch = FakeChannel([None, beacon()])
    p = HealthPoller(
        send_request=ch.send, await_beacon=ch.await_beacon,
        retries=3, timeout_s=15.0, backoff_s=2.0, sleep=slept.append)
    p.poll(HASH)
    # slept once (after the single failed attempt, before the retry)
    assert slept == [2.0]


def test_carries_fresh_beacon_for_dashboard():
    b = beacon(uptime_s=999)
    ch = FakeChannel([b])
    result = poller(ch).poll(HASH)
    assert result.beacon is not None
    assert result.beacon.uptime_s == 999
