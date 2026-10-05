"""The birth screen's end-of-build wiring, read from source.

Kivy is not importable in CI, so these inspect the code rather than run it —
the same technique and the same reason as ``tests/test_radio_proof.py``.
"""

from tests.srcutil import func_source, src


def test_birth_records_every_destination_the_machine_answers_on():
    """Birth is the one moment anybody knows the node's rnsd address and its
    health address belong to the same Pi — the mesh can never work it out,
    because the health reporter keeps its own identity file."""
    fn = func_source("ui/screens/birth_screen.py", "_register_kin")
    assert "register_device(" in fn
    for key in ("health_dst", "reticulum_address", "identity_hash"):
        assert key in fn


def test_the_health_destination_is_the_device_id():
    """register_device makes the FIRST hash the device id, and the health
    destination is the one whose beacons carry the readings."""
    fn = func_source("ui/screens/birth_screen.py", "_register_kin")
    order = fn.index("health_dst"), fn.index("reticulum_address")
    assert order[0] < order[1]


def test_the_report_verdict_is_shown_before_the_certificate_fields():
    """An operator on their way out of the door should not have to read a
    key/value list to find out whether the node has ever spoken to the medic."""
    text = src("ui/screens/birth_screen.py")
    verdict = text.index("self._add_report_verdict()")
    # The heading is tr()-wrapped since 2026-09-15; the ORDER is the law here.
    fields = text.index('_line(tr("Build certificate:")')
    assert verdict < fields


def test_nothing_heard_is_shown_in_amber_not_left_out():
    """A quiet omission reads as 'fine' to anyone skimming."""
    fn = func_source("ui/screens/birth_screen.py", "_add_report_verdict")
    assert "anything_heard" in fn
    assert "amber" in fn


def test_the_verdict_survives_a_build_that_never_ran_the_proof():
    fn = func_source("ui/screens/birth_screen.py", "_add_report_verdict")
    assert "if proof is None" in fn
