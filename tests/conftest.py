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
import os
import pytest


def pytest_configure(config):
    # Sixty-odd test files read sources and assets relative to the cwd; from
    # any other directory they aborted at collection (readiness ledger #197).
    # One chdir makes the whole suite location-independent.
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config.addinivalue_line(
        "markers",
        "onboard_guard: test drives the onboard-board guard itself — do not "
        "neutralise the host lookups for it")
    config.addinivalue_line(
        "markers",
        "usb_ports: test drives the engraved-hole port translation itself — "
        "do not stand its medic-identity check down for it")


@pytest.fixture(autouse=True)
def _hermetic_onboard_guard(request, monkeypatch, tmp_path):
    """Stand the medic's onboard-board gate down for the duration of a test."""
    # ui.usb_ports leaks the host in the same way: its engraved-hole labels
    # consult udevadm, /proc/device-tree/model and the roster, so a message
    # like "Board on /dev/ttyACM1." on the Mac reads "Board on Port 3
    # (/dev/ttyACM1)." on the medic — different text, same 2026-08-05 class of
    # failure. The cached per-process verdict is reset for EVERY test (marked
    # or not — a verdict one test measured must never leak into the next);
    # then the identity check is pinned False so no label ever fires, except
    # for the tests that drive the translation itself (``usb_ports`` marker),
    # which mock every host lookup on their own.
    try:
        import ui.usb_ports as _usb_ports
    except Exception:
        _usb_ports = None
    if _usb_ports is not None:
        _usb_ports._reset_for_tests()
        if not request.node.get_closest_marker("usb_ports"):
            monkeypatch.setattr(_usb_ports, "_medic_verified",
                                lambda: False, raising=False)
    if request.node.get_closest_marker("onboard_guard"):
        return
    try:
        import ui.onboard_roster as roster
    except Exception:                      # module unavailable — nothing to do
        return
    # A roster path that does not exist: guard_is_active() consults it.
    monkeypatch.setattr(roster, "ROSTER_PATH", str(tmp_path / "no-onboard.json"),
                        raising=False)
    # The gate is skipped on a host with nothing to protect. Tests inherit that
    # so they behave identically wherever they run.
    monkeypatch.setattr(roster, "guard_is_active", lambda *a, **k: False,
                        raising=False)
    # AND the two host lookups themselves. Patching ROSTER_PATH alone does NOT
    # reach them, and an earlier comment here claimed it did — it cannot.
    # ``is_onboard(port, path=ROSTER_PATH)`` binds that default ONCE, at import
    # time, so a caller passing no path still reads the REAL roster however the
    # module attribute is rebound afterwards.
    #
    # That is what kept six tests red on the medic and green on the Mac. On the
    # medic ``/dev/ttyACM0`` genuinely exists, so ``serial_for_port`` resolved
    # JONESEY'S OWN serial, ``is_onboard`` said True, and detect_rnode_port
    # correctly refused to hand out the medic's radio — returning None where the
    # test expected a port. The product was right every time; the test was
    # asking a question that means something different on the machine the tool
    # actually runs on.
    #
    # Tests that drive the guard itself carry the ``onboard_guard`` marker and
    # returned above, so they still exercise the real lookups.
    monkeypatch.setattr(roster, "serial_for_port", lambda *a, **k: "",
                        raising=False)
    monkeypatch.setattr(roster, "is_onboard", lambda *a, **k: False,
                        raising=False)


@pytest.fixture(autouse=True)
def _hermetic_imaged_pi(monkeypatch, tmp_path):
    """No test may touch the medic's real last-imaged-Pi record — the birth
    flow's memory of the card it just wrote (readiness ledger #194:
    test_pi_imager overwrote it). provisioning.pi_discover resolves its
    default path at CALL time, so pointing the module constant at a scratch
    file is enough; the record's own tests pass explicit paths."""
    try:
        from provisioning import pi_discover
    except Exception:
        return
    monkeypatch.setattr(pi_discover, "STATE_PATH",
                        str(tmp_path / "last_imaged_pi.json"), raising=False)


@pytest.fixture(autouse=True)
def _hermetic_medic_prefs(monkeypatch, tmp_path):
    """No test may write the medic's real screen brightness or trust store.
    The suite used to leave the operator's saved brightness at 6% and plant a
    fake trusted clone in the real trust store (readiness ledger #192, #193).
    Both modules resolve their default path at CALL time now, so pointing
    the module constants at scratch files is enough."""
    try:
        from provisioning import brightness
        monkeypatch.setattr(brightness, "CONFIG", str(tmp_path / "brightness"),
                            raising=False)
    except Exception:
        pass
    try:
        from monitor import trust
        monkeypatch.setattr(trust, "CONFIG", str(tmp_path / "trust.json"),
                            raising=False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _hermetic_medic_country(monkeypatch):
    """Every card a test images is baked for the SAME country, whatever the
    machine running the suite has for a Wi-Fi regulatory domain. pi_imager.
    flash() asks provisioning.wifi.medic_country() at call time (readiness
    ledger #123); unstubbed, a dev box with no regdom made every imaging
    message carry the AU-fallback note and a Linux box would bake its own
    country. Tests of medic_country() itself bind the real function at
    import time (tests/test_ledger_hygiene_20261004.py) and so bypass this."""
    try:
        from provisioning import wifi
    except Exception:
        return
    monkeypatch.setattr(wifi, "medic_country", lambda *a, **k: "AU", raising=False)


@pytest.fixture(autouse=True)
def _hermetic_language(monkeypatch, tmp_path):
    """Every test starts and ends in English.

    ``ui.i18n`` caches the active language in a module-level global
    (``_current``), set by ``set_language()`` and read by every ``tr()`` call.
    Nothing resets it between tests, so a test that calls
    ``set_language("es")`` (test_i18n.py does, more than once) leaves every
    LATER test in the same pytest process reading Spanish — found live
    2026-09-06: a guide_steps() test compared translated body text and failed
    only when run after the i18n suite, because tr() was quietly answering in
    Tok Pisin from an unrelated translation script run earlier in the session.

    Also points ``LANGUAGE_FILE`` at a scratch path so a test cannot read or
    write the real on-disk preference — the persistence layer belongs to
    test_i18n.py's own fixtures, not to every other test that happens to
    import a module which calls ``tr()``. (This paragraph was a promise the
    body did not keep until 2026-10-04: a saved non-English preference on
    the medic turned 66 tests red — readiness ledger #195.)
    """
    try:
        import ui.i18n as i18n
    except Exception:
        yield
        return
    monkeypatch.setattr(i18n, "LANGUAGE_FILE", str(tmp_path / "language"),
                        raising=False)
    # English, cached: tr() never consults the disk unless a test asks it to
    # (test_i18n's own fixture clears the cache again for the round-trips).
    monkeypatch.setattr(i18n, "_current", i18n.DEFAULT_LANGUAGE, raising=False)
    yield
    i18n._current = None
