"""Regression: a triage session AUTO-CLEARS once a BIRTH consumes it (Settings
spec). A pending mount survey must never linger to be inherited by the next,
unrelated birth."""

import pytest

from monitor import triage
from monitor.triage import TriageSession


@pytest.fixture(autouse=True)
def _reset_registry():
    """Never leak an active session between tests (module-level state)."""
    triage.clear_active_session()
    yield
    triage.clear_active_session()


def test_no_session_pending_by_default():
    assert triage.active_session() is None


def test_register_makes_session_active():
    s = TriageSession()
    triage.set_active_session(s)
    assert triage.active_session() is s
    assert s.consumed is False


def test_birth_consume_returns_session_and_autoclears():
    s = TriageSession()
    # a real survey produced a best reading BIRTH would stamp onto the cert
    s.feed(snr=12, rssi=-70, noise=-118, t=0.0)
    triage.set_active_session(s)

    consumed = triage.consume_active_session()

    assert consumed is s                      # handed back so BIRTH can read it
    assert s.consumed is True                 # flagged as spent
    # THE regression guarantee: nothing lingers after a BIRTH consumes it
    assert triage.active_session() is None


def test_second_consume_is_empty_no_stale_inheritance():
    s = TriageSession()
    triage.set_active_session(s)
    assert triage.consume_active_session() is s
    # a subsequent birth with no fresh triage must get nothing, not the old one
    assert triage.consume_active_session() is None
    assert triage.active_session() is None


def test_consume_with_nothing_pending_is_none():
    assert triage.consume_active_session() is None


def test_clear_drops_pending_without_consuming():
    s = TriageSession()
    triage.set_active_session(s)
    triage.clear_active_session()            # operator left Triage without birthing
    assert triage.active_session() is None
    assert s.consumed is False               # not consumed, just dropped
