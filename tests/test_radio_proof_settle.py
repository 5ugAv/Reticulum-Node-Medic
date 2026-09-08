"""A young node must not be recorded as unreachable.

Operator, 2026-09-09, birthing an EoRa-S3 as an RTNode. Build finished, the
node was on air 2 hops away — and the certificate said "did not answer over
the radio". The timing settled it:

    radio poll ran at : 01:55:20
    its announce came : 01:55:48      (28 s later)

prove_radio calls drop_cached_path() first, on purpose, so a route learned
while the board was on the cable cannot fake the result. On a REBIRTH the node
also comes back with a brand-new identity, so no path has ever existed. Then it
polled once, immediately, and wrote the failure into a permanent record.

Same disease as the false "and joined <ssid>" fixed the night before, inverted:
that asserted a success it had not checked, this asserted a failure it checked
too early.
"""

from workflows.radio_proof import prove_radio


class _Reply:
    def __init__(self, reachable):
        self.reachable = reachable


def _proof(polls, **kw):
    """Drive prove_radio with a scripted sequence of poll answers."""
    seen = {"slept": []}
    it = iter(polls)
    return seen, prove_radio(
        b"\x01" * 16,
        drop_cached_path=lambda h: None,
        poll=lambda h: next(it),
        interface_for=lambda h: "RNodeInterface[test]",
        now=lambda: 1788882920.0,
        node_name="RTNode 5AA9",
        sleep=lambda s: seen["slept"].append(s),
        **kw)


def test_a_node_that_answers_on_the_second_ask_is_heard():
    """The exact case: silent at first, announces ~30 s later."""
    seen, p = _proof([_Reply(False), _Reply(True)])
    assert p.heard, "polled again after the announce window and it answered"
    assert seen["slept"], "it must actually wait before asking again"
    assert seen["slept"][0] >= 30, "wait must cover the ~30 s announce window"


def test_a_genuinely_silent_node_is_still_reported_silent():
    """The retry must not turn every failure into a pass."""
    _, p = _proof([_Reply(False), _Reply(False)])
    assert not p.heard
    assert p.checks, "a real failure still carries its troubleshooting checks"


def test_the_wording_does_not_condemn_the_node():
    """"did not answer" reads as a verdict on the node. It is a verdict on
    this moment, and it goes into a permanent record."""
    _, p = _proof([_Reply(False), _Reply(False)])
    assert "has not answered" in p.summary and "yet" in p.summary
    assert "did not answer over the radio." != p.summary


def test_a_node_heard_first_time_never_waits():
    """No settle delay added to the normal, already-working path."""
    seen, p = _proof([_Reply(True)])
    assert p.heard
    assert not seen["slept"], "must not delay a build that already succeeded"


def test_settle_can_be_switched_off():
    seen, p = _proof([_Reply(False)], settle_s=0)
    assert not p.heard and not seen["slept"]
