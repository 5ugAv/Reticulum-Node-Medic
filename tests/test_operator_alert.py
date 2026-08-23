"""Optional operator LXMF alert — address parsing, persistence, send gating."""

import monitor.operator_alert as oa
from monitor.operator_alert import (
    normalize_address, valid_address, send_operator_alert)

GOOD = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"       # 32 hex = 16-byte RNS hash


def test_normalize_strips_decorations():
    assert normalize_address(f"<lxmf@{GOOD.upper()}>") == GOOD
    assert normalize_address(f"  {GOOD}  ") == GOOD


def test_valid_address():
    assert valid_address(GOOD)
    assert not valid_address("deadbeef")              # too short
    assert not valid_address(GOOD + "ff")             # too long
    assert not valid_address("z" * 32)                # non-hex
    assert not valid_address("")


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(oa, "OPERATOR_ADDR_FILE", str(tmp_path / "op"))
    assert oa.save_operator_address(f"<{GOOD}>") == GOOD
    assert oa.load_operator_address() == GOOD


def test_save_invalid_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(oa, "OPERATOR_ADDR_FILE", str(tmp_path / "op"))
    oa.save_operator_address(GOOD)
    assert oa.save_operator_address("not-an-address") == ""     # invalid -> cleared
    assert oa.load_operator_address() == ""


def test_send_skips_when_no_address():
    ok, msg = send_operator_alert("Node down", address="")
    assert ok is False and "no operator address" in msg


def test_send_rejects_invalid_address():
    ok, msg = send_operator_alert("Node down", address="garbage")
    assert ok is False and "valid" in msg


def test_send_calls_sender_for_valid_address():
    calls = []
    ok, msg = send_operator_alert("Node down", address=GOOD,
                                  sender=lambda a, t: calls.append((a, t)) or True)
    assert ok is True and calls == [(GOOD, "Node down")]


def test_send_reports_failure_gracefully():
    ok, msg = send_operator_alert("x", address=GOOD, sender=lambda a, t: False)
    assert ok is False and "retry" in msg


def test_sender_exception_is_contained():
    def boom(a, t):
        raise RuntimeError("mesh down")
    ok, _ = send_operator_alert("x", address=GOOD, sender=boom)
    assert ok is False


def test_addr_regex_rejects_trailing_newline():
    # regex-newline security fix: `^[0-9a-f]{32}$` + re.match accepted
    # "<32 hex>\n" (Python's `$` matches just before a trailing newline).
    # fullmatch on a newline-free pattern rejects it; a legit hash still
    # matches. (normalize_address also .strip()s, so valid_address is doubly
    # protected — this guards the validation boundary itself.)
    assert oa._ADDR_RE.fullmatch(GOOD)
    assert oa._ADDR_RE.fullmatch(GOOD + "\n") is None
    assert oa._ADDR_RE.fullmatch(GOOD + "\r\n") is None
    assert oa._ADDR_RE.fullmatch(GOOD[:16] + "\n" + GOOD[16:]) is None
