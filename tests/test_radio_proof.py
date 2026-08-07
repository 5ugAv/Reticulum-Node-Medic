"""Birth must prove the RADIO, not just the cable (task #77).

Operator, 2026-08-06: "there should also be a test during that birth where it
tests the lora to see if it can find it over the lora as well."

Birth proves a node is CONFIGURED. Only hearing it proves it is REACHABLE, and
that is what the node is for. Every test here is a way the old, missing check
would have let a deaf node onto a pole.
"""

import types

from workflows import radio_proof as rp


def _result(reachable=True, rssi=None, snr=None):
    beacon = types.SimpleNamespace(rssi=rssi, snr=snr)
    return types.SimpleNamespace(reachable=reachable, beacon=beacon)


def _prove(iface="RNodeInterface[Jonesey]", reachable=True, dropped=None,
           rssi=None, snr=None, **kw):
    def drop(h):
        if dropped is not None:
            dropped.append(h)
    return rp.prove_radio(
        b"\x01\x02", drop_cached_path=drop,
        poll=lambda h: _result(reachable, rssi, snr),
        interface_for=lambda h: iface, now=lambda: 1000.0, **kw)


# --- the happy path ---------------------------------------------------------

def test_hearing_it_over_a_lora_interface_is_proof():
    p = _prove(node_name="faith", rssi=-91.5, snr=7.0)
    assert p.heard is True
    assert p.rssi == -91.5 and p.snr == 7.0
    assert "Heard faith over the radio" in p.summary


def test_the_certificate_records_evidence_not_a_promise():
    f = _prove(node_name="faith", rssi=-91.5).cert_fields()
    assert f["radio_verified"] is True
    assert "RNodeInterface" in f["radio_interface"]
    assert f["radio_rssi"] == -91.5
    assert f["radio_checked_at"] == 1000.0


# --- CACHED PATHS LIE -------------------------------------------------------
# first-range-test-faith cost an evening to this: a reachability check answered
# through a stale cached path and reported a working radio that was not working.

def test_the_cached_path_is_dropped_BEFORE_asking():
    dropped = []
    _prove(dropped=dropped)
    assert dropped == [b"\x01\x02"], "the cached path was not dropped first"


# --- THE FLATTERING CASE ----------------------------------------------------
# It answers — over the USB cable that is still plugged in. The radio is exactly
# as unproven as before, but a naive check calls it a pass.

def test_answering_over_the_cable_is_NOT_proof():
    p = _prove(iface="AutoInterface")
    assert p.heard is False
    assert p.answered_off_air is True
    assert "proves the cable, which we already knew" in p.summary


def test_the_cable_case_says_to_unplug_and_retry_FIRST():
    p = _prove(iface="AutoInterface")
    assert "Unplug the USB cable" in p.checks[0]


def test_a_tcp_interface_is_not_proof_either():
    """Reaching it through somebody's hub says nothing about its LoRa modem."""
    assert _prove(iface="TCPClientInterface[hub]").heard is False


def test_an_unknown_interface_fails_CLOSED():
    """The operator is about to walk away and trust this. An interface we do not
    recognise must not be allowed to stand in for a radio."""
    assert rp.is_over_air("SomethingNew") is False
    assert rp.is_over_air("") is False
    assert _prove(iface="MysteryInterface").heard is False


def test_a_real_rnode_interface_IS_recognised():
    for name in ("RNodeInterface[Jonesey]", "rnode0", "LoRa Interface"):
        assert rp.is_over_air(name) is True, name


# --- failure is informative, never fatal ------------------------------------

def test_not_being_heard_is_a_RESULT_not_an_exception():
    p = _prove(reachable=False, node_name="faith")
    assert p.heard is False
    assert "did not answer over the radio" in p.summary
    assert p.checks, "a failure with no advice is just bad news"


def test_a_broken_transport_does_not_fail_the_whole_birth():
    """The birth has otherwise succeeded. An exception in the radio check must
    not throw that away."""
    def boom(h):
        raise RuntimeError("no rnsd")
    p = rp.prove_radio(b"\x01", drop_cached_path=lambda h: None, poll=boom,
                       interface_for=lambda h: "", now=lambda: 1.0)
    assert p.heard is False
    assert "Could not run the radio check" in p.summary


def test_a_failure_to_drop_the_path_still_runs_the_check():
    """A weaker test is better than no test."""
    def boom(h):
        raise RuntimeError("rnpath missing")
    p = rp.prove_radio(b"\x01", drop_cached_path=boom,
                       poll=lambda h: _result(True),
                       interface_for=lambda h: "RNodeInterface[J]",
                       now=lambda: 1.0)
    assert p.heard is True


def test_the_antenna_warnings_come_first_and_name_the_damage():
    """Never transmit without an antenna — the one check whose absence breaks
    hardware rather than just the test."""
    p = _prove(reachable=False)
    assert "antenna" in p.checks[0].lower()
    assert "damage it permanently" in p.checks[1]


def test_mismatched_radio_params_are_named_as_a_cause():
    """A node on a different frequency is invisible, not broken — and that is
    the failure most likely to be misread as a dead board."""
    p = _prove(reachable=False)
    assert any("frequency" in c for c in p.checks)


def test_an_unheard_node_never_claims_verification_on_the_certificate():
    f = _prove(reachable=False).cert_fields()
    assert f["radio_verified"] is False
    assert f["radio_rssi"] is None
