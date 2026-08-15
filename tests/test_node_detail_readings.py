"""The node-detail screen reads live values off whatever it was handed.

2026-08-11: the operator tapped a node in VITALS and the medic's screen went
BLACK. Not blanked — the app died, and it does not restart itself.
"""

import pytest

from monitor.health_beacon import decode
from monitor.registry import NodeRegistry


def _reading(*a, **kw):
    """Imported INSIDE the call, not at module scope.

    CI has no Kivy. `ui.screens.node_detail_screen` pulls it in, so a
    module-level import made this file fail at COLLECTION — taking the whole
    suite down with `Interrupted: 1 error during collection`, on both Python
    versions, in 16 seconds. It passed here because this Mac has Kivy
    installed: the exact `green locally, red in CI` split this repo has been
    bitten by before.

    The suite's stubs cover what is already imported; a new import path is
    not among them. Deferring keeps collection Kivy-free.
    """
    from ui.screens.node_detail_screen import _reading as impl
    return impl(*a, **kw)

HASH = "11223344556677889900aabbccddeeff"
NOW = 1_780_000_000.0


def _record_with_beacon():
    reg = NodeRegistry()
    # the golden hardware vector: a real RTNode-2400 health beacon
    reg.ingest(HASH, decode(bytes.fromhex("010000002400c7cc053b3f000602")), NOW)
    return reg.get(HASH)


def test_a_method_is_called_not_compared():
    """THE CRASH. signal_dbm is a METHOD on NodeRecord, so getattr handed back a
    bound method and the next line did `sig > -90`:

        TypeError: '>' not supported between instances of 'method' and 'int'

    A screen that dies takes the whole app with it, and the operator is left
    looking at a black rectangle with nothing on it to say why."""
    rec = _record_with_beacon()
    sig = _reading(rec, "signal_dbm")
    assert sig is None or isinstance(sig, int)
    if sig is not None:
        assert sig > -200           # the comparison that used to explode


def test_the_battery_reading_was_silently_missing_for_every_node():
    """The same bug wearing a quieter coat: `battery_pct` is not on the record
    at all — it is `_battery_pct()` — so `getattr(rec, "battery_pct", None)`
    returned None and EVERY node reported "Battery: not reported", including
    nodes that were sending their charge perfectly well.

    No crash, no error, just a screen quietly telling the operator less than
    the tool knew."""
    reg = NodeRegistry()
    rec = reg.register(HASH, name="solar node")

    class Beacon:                      # a node that IS reporting its charge
        battery_pct = 82

    rec.latest_beacon = Beacon()
    assert rec._battery_pct() == 82, "the record can see the charge"
    assert _reading(rec, "battery_pct") == 82, "and so must the screen"


def test_it_reads_the_dashboard_dict_too():
    """The screen is reachable with either shape. Fixing one and breaking the
    other would just move the black screen somewhere else."""
    d = {"signal_dbm": -70, "battery_pct": 82}
    assert _reading(d, "signal_dbm") == -70
    assert _reading(d, "battery_pct") == 82
    assert _reading(d, "nothing_here") is None


def test_a_reading_that_raises_is_absent_not_fatal():
    """A live accessor can fail — a half-decoded beacon, a record mid-update.
    "I could not read it" is a line on the screen; an exception is a black
    screen."""
    class Sulky:
        def signal_dbm(self):
            raise RuntimeError("no")

    assert _reading(Sulky(), "signal_dbm") is None


def test_an_unknown_reading_is_none_on_both_shapes():
    reg = NodeRegistry()
    rec = reg.register(HASH, name="bare")
    assert _reading(rec, "battery_pct") is None      # never heard from
    assert _reading(rec, "signal_dbm") is None
