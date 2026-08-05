"""Keep the suite hermetic — the same result on a dev Mac and on the medic.

Found 2026-08-05, the first time the suite was ever run ON the medic: 50 tests
failed there that pass on the Mac. None of them were product bugs. They were
the host environment leaking in.

Two ways it leaks, both from ``ui.onboard_roster``:

1. ``guard_is_active()`` is True when the host has an onboard roster OR a
   ``/dev/serial/by-id`` directory — i.e. on the medic, and nowhere else. The
   flash workflows call ``assert_flashable`` only when it is active, so on the
   Mac the gate is skipped entirely and the tests sail past with fake macOS
   port names like ``/dev/cu.usbmodem2101``. On the medic the gate engages,
   cannot resolve a serial for a port that does not exist, and fail-closes —
   correctly. 40+ tests turned red for doing the right thing.

2. ``local_board_ports`` resolves candidate ports through the real
   ``/dev/serial/by-id``. Tests that patch ``glob.glob`` still hit the medic's
   actual udev tree underneath and get an empty list.

So the fixture below points the roster at a path that does not exist and stands
the gate down for the suite at large. This is NOT a loss of coverage: the gate
is what stops the medic flashing its own radio, and it keeps its own dedicated
tests in ``test_onboard_roster.py``, which drive it directly with their own
paths and are exempted here via the ``onboard_guard`` marker.

Without this the suite simply cannot be run where the tool actually lives,
which is the one place its results mean anything.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "onboard_guard: test drives the onboard-board guard itself — do not "
        "neutralise the host lookups for it")


@pytest.fixture(autouse=True)
def _hermetic_onboard_guard(request, monkeypatch, tmp_path):
    """Stand the medic's onboard-board gate down for the duration of a test."""
    if request.node.get_closest_marker("onboard_guard"):
        return
    try:
        import ui.onboard_roster as roster
    except Exception:                      # module unavailable — nothing to do
        return
    # A roster path that does not exist: guard_is_active() consults it, and so
    # does every default-argument caller of load_roster/is_onboard.
    monkeypatch.setattr(roster, "ROSTER_PATH", str(tmp_path / "no-onboard.json"),
                        raising=False)
    # The gate is skipped on a host with nothing to protect. Tests inherit that
    # so they behave identically wherever they run.
    monkeypatch.setattr(roster, "guard_is_active", lambda *a, **k: False,
                        raising=False)
