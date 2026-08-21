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


# ---- warm-and-send delivery honesty (2026-08-21/22 field lessons) ----------
#
# 2026-08-21: a packet sent while the sending stack's has_path() is False dies
# SILENTLY — warm_and_send must never call send() without a path. 2026-08-22:
# the firmware throttles repeat poll replies, so sent-but-silent is its own
# outcome ("did not answer"), distinct from no-route ("never sent").

from monitor.health_poll import (
    DELIVERY_ANSWERED,
    DELIVERY_NO_ROUTE,
    DELIVERY_UNANSWERED,
    warm_and_send,
    warm_path,
)


class FakeTransport:
    """has_path answers scripted per call (then False forever); records every
    request_path / send / await_reply, and counts sleeps instead of sleeping."""

    def __init__(self, path_answers, reply=False):
        self._answers = list(path_answers)
        self.reply = reply
        self.requested = []
        self.sent = []
        self.awaited = []
        self.slept = []

    def has_path(self, dh):
        return self._answers.pop(0) if self._answers else False

    def request_path(self, dh):
        self.requested.append(dh)

    def send(self, dh):
        self.sent.append(dh)

    def await_reply(self, dh, wait_s):
        self.awaited.append((dh, wait_s))
        return self.reply


def _run(t, **kw):
    kw.setdefault("sleep", t.slept.append)
    return warm_and_send(HASH, t.has_path, t.request_path,
                         t.send, t.await_reply, **kw)


def test_path_already_known_sends_immediately():
    t = FakeTransport([True], reply=True)
    assert _run(t) == DELIVERY_ANSWERED
    assert t.requested == []          # no request needed
    assert t.slept == []              # and no waiting either
    assert t.sent == [HASH]


def test_path_arriving_on_third_wait_still_sends():
    # initial check False, then polls: False, False, True -> send
    t = FakeTransport([False, False, False, True], reply=True)
    assert _run(t) == DELIVERY_ANSWERED
    assert t.requested == [HASH]      # exactly one path request
    assert t.sent == [HASH]
    assert len(t.slept) == 3          # slept up to (and incl.) the 3rd poll


def test_path_never_arriving_is_no_route_and_never_sends():
    t = FakeTransport([False])
    assert _run(t) == DELIVERY_NO_ROUTE
    # THE 2026-08-21 rule: no path -> the packet is never fired (it would
    # have died silently), and there is no reply window to wait through.
    assert t.sent == []
    assert t.awaited == []
    # the wait is capped: 15 s at one poll per second
    assert len(t.slept) == 15


def test_sent_but_silent_is_unanswered_not_no_route():
    t = FakeTransport([True], reply=False)
    assert _run(t) == DELIVERY_UNANSWERED
    assert t.sent == [HASH]           # it WAS delivered into the mesh
    # the reply window that was actually waited through is the default one
    assert t.awaited == [(HASH, 15.0)]


def test_warm_path_polls_once_per_interval_up_to_cap():
    t = FakeTransport([False])
    slept = []
    ok = warm_path(HASH, t.has_path, t.request_path,
                   wait_s=5.0, poll_s=1.0, sleep=slept.append)
    assert ok is False
    assert slept == [1.0] * 5


def test_warm_path_immediate_when_known():
    t = FakeTransport([True])
    assert warm_path(HASH, t.has_path, t.request_path,
                     sleep=lambda s: None) is True
    assert t.requested == []


def test_app_ping_is_wired_through_warm_and_send():
    """The UI's ping must ride this module's honesty logic — and its wording
    must separate 'no route' (never sent) from 'did not answer' (sent, then
    silence), never claiming the node is down on silence alone (throttling,
    2026-08-22). Source inspection because Kivy is not importable here."""
    from tests.srcutil import func_source
    src = func_source("ui/app.py", "_ping_node")
    assert "warm_and_send" in src
    assert "did not answer" in src
    assert "not sent" in src                    # the no-route wording
    # the old overclaim — promising a reply before hearing one — is gone
    assert "fresh readings arrive in seconds" not in src
