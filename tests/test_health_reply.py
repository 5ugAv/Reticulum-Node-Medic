"""The unicast health reply's wire contract, node predicate, waits and
pending-poll table — pure bytes, and real Ed25519 keys where RNS is
installed (docs/HEALTH_REPLY_UNICAST.md, 2026-09-21, revised after review)."""
import pytest

import monitor.health_reply as hr

RNS = pytest.importorskip("RNS")
D1, D2 = b"\x11" * 16, b"\x22" * 16
N1, N2 = b"\xa1" * 8, b"\xb2" * 8
BEACON = b"\x02" + bytes(range(19))


def test_request_carries_the_reply_destination_and_a_nonce():
    req = hr.build_request_to(D1, N1)
    assert req == bytes([0x04]) + D1 + N1 and len(req) == hr.REQUEST_LEN
    a, b = hr.build_request_to(D1), hr.build_request_to(D1)
    assert a[17:] != b[17:], "nonces are random per request"
    with pytest.raises(ValueError):
        hr.build_request_to(b"\x11" * 15)
    assert hr.build_fallback_request() == bytes([hr.OPCODE_FULL_HEALTH])


def test_a_node_parses_the_request_and_ignores_malformed_ones():
    assert hr.parse_request(hr.build_request_to(D2, N2)) == (0x04, D2, N2)
    assert hr.parse_request(bytes([0x01])) == (0x01, None, None)
    assert hr.parse_request(bytes([0x04]) + D2) == (-1, None, None)      # no nonce
    assert hr.parse_request(b"") == (-1, None, None)


def test_a_reply_verifies_only_under_the_named_nodes_own_key():
    node, stranger = RNS.Identity(), RNS.Identity()
    reply = hr.make_reply(D1, N1, BEACON, node.sign)
    assert len(reply) == 16 + 8 + len(BEACON) + hr.SIG_LEN
    recall = lambda d: node if d == D1 else None
    assert hr.verify_reply(reply, recall) == (D1, N1, BEACON)
    assert hr.verify_reply(reply, lambda d: stranger) is None
    assert hr.verify_reply(reply, lambda d: None) is None        # never heard it
    for i in (0, 16, 24, len(reply) - 1):                         # dest, nonce, beacon, sig
        t = bytearray(reply); t[i] ^= 0x01
        assert hr.verify_reply(bytes(t), recall) is None
    assert hr.verify_reply(b"short", recall) is None
    assert hr.verify_reply(reply, lambda d: (_ for _ in ()).throw(RuntimeError())) is None


def test_two_overlapping_polls_are_each_answered_by_their_own_reply():
    clock = [1000.0]
    polls = hr.PendingPolls(now=lambda: clock[0])
    polls.add(N1, D1, hops=2)
    polls.add(N2, D2, hops=1)
    clock[0] = 1048.0
    late = polls.claim(N1, D1)                          # ROOFRAK answers late
    assert late and late["dest"] == D1 and late["answered_after_s"] == 48.0
    assert polls.claim(N1, D1) is None                  # once
    assert polls.claim(N2, D1) is None                  # nonce lifted, wrong node
    assert polls.claim(N2, D2)["hops"] == 1
    assert polls.pending() == 0


def test_a_pending_poll_expires():
    clock = [0.0]
    polls = hr.PendingPolls(ttl_s=300.0, now=lambda: clock[0])
    polls.add(N1, D1, hops=1)
    clock[0] = 301.0
    assert polls.claim(N1, D1) is None and polls.pending() == 0


def test_uptime_must_not_run_backwards_unless_the_node_rebooted():
    assert hr.uptime_is_fresh(5000, 4000) is True
    assert hr.uptime_is_fresh(3000, 4000) is False           # replayed / stale
    assert hr.uptime_is_fresh(3000, 4000, rebooted=True) is True
    assert hr.uptime_is_fresh(35, 4000) is True                # just rebooted
    assert hr.uptime_is_fresh(None, 4000) is True


def test_the_node_answers_unicast_only_with_a_key_and_a_road():
    assert hr.node_reply_mode(recalled=True, has_path=True) == "unicast"
    assert hr.node_reply_mode(recalled=True, has_path=False) == "announce"
    assert hr.node_reply_mode(recalled=False, has_path=True) == "announce"


def test_wait_windows_respect_the_rate_limiter_and_the_relay_cap():
    assert hr.unicast_wait_s(1) == 32.0 and hr.unicast_wait_s(2) == 32.0
    assert hr.unicast_wait_s(5) == 35.0
    assert hr.unicast_wait_s(None) >= hr.NODE_RATE_LIMIT_S, "0x01 after 0x04 must clear the limiter"
    hold = hr.relay_cap_hold_s(1800.0)
    assert 45.0 < hold < 49.0
    assert hr.announce_wait_s(1) == 15.0
    assert hr.announce_wait_s(3) == pytest.approx(15.0 + 2 * hold)


def test_is_pending_flips_when_a_reply_claims_the_nonce():
    polls = hr.PendingPolls(now=lambda: 0.0)
    polls.add(N1, D1, hops=1)
    assert polls.is_pending(N1) is True
    assert polls.claim(N1, D1) is not None
    assert polls.is_pending(N1) is False
