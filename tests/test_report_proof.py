"""Proving the node reports back — and, mostly, refusing to claim that it did.

A certificate that says "working node" when the medic has heard nothing is the
failure this module exists to end, so nearly every test here is about the ways a
report can be absent and still get written down as a success.
"""

from workflows import report_proof as rp
from workflows.radio_proof import NOT_APPLICABLE


class _Answer:
    def __init__(self, reachable):
        self.reachable = reachable


def _poll(*answering):
    """A poller that answers only for the hosts listed."""
    hosts = set(answering)
    return lambda host: _Answer(host in hosts)


# ---- HTTP ------------------------------------------------------------------

def test_no_address_to_ask_is_could_not_check_not_a_failure_to_report():
    p = rp.prove_reporting([], _poll(), beacon_reason="no radio yet")
    assert p.http_answered is False
    assert "could not check" in p.summary


def test_the_address_that_answered_is_recorded_verbatim():
    p = rp.prove_reporting(["192.168.1.42", "10.55.0.1"],
                           _poll("10.55.0.1"), beacon_reason="r")
    assert p.http_answered is True
    assert p.http_host == "10.55.0.1"
    assert "10.55.0.1" in p.summary


def test_the_lan_address_is_tried_before_the_cable_one():
    asked = []

    def poll(host):
        asked.append(host)
        return _Answer(True)
    rp.prove_reporting(["192.168.1.42", "10.55.0.1"], poll, beacon_reason="r")
    assert asked == ["192.168.1.42"]


def test_a_poller_that_raises_moves_on_to_the_next_address():
    def poll(host):
        if host == "192.168.1.42":
            raise OSError("no route")
        return _Answer(True)
    p = rp.prove_reporting(["192.168.1.42", "10.55.0.1"], poll, beacon_reason="r")
    assert p.http_answered is True and p.http_host == "10.55.0.1"


def test_every_address_tried_is_named_when_none_answered():
    p = rp.prove_reporting(["a", "b"], _poll(), beacon_reason="r")
    assert "a, b" in p.summary
    assert p.checks, "a channel that failed must come with something to check"


# ---- the beacon channel ----------------------------------------------------

def test_no_test_to_take_is_not_applicable_and_carries_no_checks():
    p = rp.prove_reporting(["h"], _poll("h"),
                           beacon_reason="the radio goes on after the build")
    assert p.beacon_not_applicable is True
    assert p.beacon_heard is False
    assert "the radio goes on after the build" in p.summary
    assert p.checks == [], "there is nothing for the operator to fix here"


def test_not_applicable_reaches_the_certificate_as_words_not_as_false():
    p = rp.prove_reporting(["h"], _poll("h"), beacon_reason="not yet")
    fields = p.cert_fields()
    assert fields["reports_over_lora"] == NOT_APPLICABLE
    assert fields["reports_lora_interface"] == NOT_APPLICABLE


def test_not_applicable_is_never_read_as_verified():
    """The radio_proof trap, one module over: a non-empty string is truthy, so
    a node that could never have been tested would read as proven."""
    cert = rp.prove_reporting(["h"], _poll("h"), beacon_reason="x").cert_fields()
    assert bool(cert["reports_over_lora"]) is True     # the trap
    assert rp.reported_over_lora(cert) is False        # the reader


def test_a_silent_radio_is_reported_and_comes_with_checks():
    p = rp.prove_reporting(
        ["h"], _poll("h"),
        hear_beacon=lambda: rp.BeaconOutcome(detail="heard nothing back in 25s"))
    assert p.beacon_heard is False
    assert p.beacon_not_applicable is False
    assert "heard nothing back in 25s" in p.summary
    assert rp.BEACON_CHECKS[0] in p.checks


def test_a_probe_that_explodes_is_a_result_not_an_exception():
    def boom():
        raise RuntimeError("RNS is not here")
    p = rp.prove_reporting(["h"], _poll("h"), hear_beacon=boom)
    assert p.beacon_heard is False
    assert "RNS is not here" in p.summary


def test_a_decoded_beacon_names_the_interface_it_arrived_on():
    p = rp.prove_reporting(
        ["h"], _poll("h"),
        hear_beacon=lambda: rp.BeaconOutcome(heard=True, interface="RNodeInterface[LoRa]"))
    assert p.beacon_heard is True
    assert "RNodeInterface[LoRa]" in p.summary
    assert p.cert_fields()["reports_over_lora"] is True


# ---- the two channels stay apart -------------------------------------------

def test_the_channels_are_reported_separately_never_blended():
    p = rp.prove_reporting(["h"], _poll(),   # HTTP silent
                           hear_beacon=lambda: rp.BeaconOutcome(heard=True))
    assert p.http_answered is False and p.beacon_heard is True
    assert p.anything_heard is True
    # both halves are visible; neither is summarised away
    assert "did not answer /status" in p.summary
    assert "health beacon" in p.summary


def test_heard_on_neither_channel_is_heard_on_neither():
    p = rp.prove_reporting(["h"], _poll(),
                           hear_beacon=lambda: rp.BeaconOutcome())
    assert p.anything_heard is False
    assert rp.reported_over_http(p.cert_fields()) is False
    assert rp.reported_over_lora(p.cert_fields()) is False


def test_not_applicable_alone_is_not_something_heard():
    """The dangerous middle: nothing answered anywhere, but one channel had a
    good excuse. That is still a node the medic has never heard from."""
    p = rp.prove_reporting([], _poll(), beacon_reason="radio comes later")
    assert p.anything_heard is False


def test_the_readers_reject_a_missing_certificate_entirely():
    assert rp.reported_over_http({}) is False
    assert rp.reported_over_lora(None) is False
