"""ui/usb_ports.py — engraved-hole labels for kernel USB paths.

Everything runs against mocked host lookups: the map is a measurement of the
medic's wiring (2026-08-21) and CI machines have no such holes. What matters
here is the honesty contract — the map speaks ONLY when the host proves it is
THE medic (roster guard active + Pi 5 device tree + Jonesey anchored at 3-2),
and even then only about measured segments over a real local connection.
Everything else (shadow buses, hub-only paths, bus renumbering, other Pis,
udevadm failure, emulated/remote ports) says nothing extra.

The ``usb_ports`` marker keeps conftest's hermetic fixture from pinning the
identity check False here — these tests mock every lookup themselves.
"""

from types import SimpleNamespace

import pytest

from ui import onboard_roster, usb_ports
from ui.usb_ports import (MEDIC_PORT_MAP, case_port_label, describe_port,
                          usb_path_for_tty)

pytestmark = pytest.mark.usb_ports

JONESEY_SERIAL = "AA:BB:CC:DD:EE:FF"
SYS = ("/devices/platform/axi/1000120000.pcie/1f00300000.usb/xhci-hcd.1/"
       "usb{b}/{seg}/{seg}:1.0/tty/{tty}")


def _host(monkeypatch, *, guard=True, model="Raspberry Pi 5 Model B Rev 1.0",
          jonesey_seg="3-2", paths=None):
    """Fake one complete host: roster + device tree + udevadm answers.

    Jonesey sits on /dev/ttyACM0 at *jonesey_seg*; *paths* maps further ttys
    to their udevadm sysfs paths. Anything unlisted gets a nonzero udevadm
    (device unknown), exactly like the real tool."""
    monkeypatch.setattr(onboard_roster, "guard_is_active", lambda *a, **k: guard)
    monkeypatch.setattr(onboard_roster, "load_roster",
                        lambda *a, **k: {"rnode_eeff": JONESEY_SERIAL})
    monkeypatch.setattr(onboard_roster, "attached_serial_ports",
                        lambda *a, **k: ["/dev/ttyACM0"])
    monkeypatch.setattr(onboard_roster, "serial_for_port",
                        lambda p, *a, **k: JONESEY_SERIAL if p == "/dev/ttyACM0"
                        else None)
    monkeypatch.setattr(usb_ports, "_device_tree_model", lambda: model)
    table = dict(paths or {})
    if jonesey_seg:
        table["/dev/ttyACM0"] = SYS.format(b=jonesey_seg.split("-")[0],
                                           seg=jonesey_seg, tty="ttyACM0")
    calls = []

    def run(dev):
        calls.append(dev)
        if dev in table:
            return SimpleNamespace(returncode=0, stdout=table[dev], stderr="")
        return SimpleNamespace(returncode=2, stdout="", stderr="")

    monkeypatch.setattr(usb_ports, "_run_udevadm", run)
    usb_ports._reset_for_tests()
    return calls


def _with_board(monkeypatch, seg, tty="ttyACM1", **host_kw):
    dev = f"/dev/{tty}"
    _host(monkeypatch, paths={dev: SYS.format(b=seg.split("-")[0].split(".")[0],
                                              seg=seg, tty=tty)}, **host_kw)
    return dev


def test_map_active_on_the_verified_medic(monkeypatch):
    dev = _with_board(monkeypatch, "1-2")
    assert usb_path_for_tty(dev) == "1-2"
    assert case_port_label(dev) == "Port 3"
    assert describe_port(dev) == "Port 3 (/dev/ttyACM1)"


def test_dotted_hub_chain_names_the_root_hole(monkeypatch):
    # A board on a hub in the top-right hole: 1-1.3 -> the hole is 1-1.
    dev = _with_board(monkeypatch, "1-1.3", tty="ttyACM2")
    assert usb_path_for_tty(dev) == "1-1.3"
    assert case_port_label(dev) == "Port 2 (via hub)"
    assert describe_port(dev) == "Port 2 (via hub) (/dev/ttyACM2)"


def test_bus_swap_silences_the_whole_map(monkeypatch):
    # Kernel renumbering: Jonesey reads 1-2 instead of 3-2. The anchor fails,
    # so NOTHING gets a number — not even a segment the map "knows".
    dev = _with_board(monkeypatch, "1-2", jonesey_seg="1-1")
    assert usb_path_for_tty(dev) is None
    assert case_port_label(dev) is None
    assert describe_port(dev) == dev


def test_pi4_never_gets_a_label(monkeypatch):
    # Same bus numbers, no engraving: a Pi 4 (or the Lima sandbox) must fail
    # the device-tree check however plausible its segments look.
    dev = _with_board(monkeypatch, "1-2",
                      model="Raspberry Pi 4 Model B Rev 1.4")
    assert case_port_label(dev) is None
    assert describe_port(dev) == dev


def test_inactive_roster_guard_never_gets_a_label(monkeypatch):
    # guard_is_active False = "not the medic" (its own docstring); a laptop
    # with the world's commonest USB segments stays unlabelled.
    dev = _with_board(monkeypatch, "1-2", guard=False)
    assert case_port_label(dev) is None
    assert describe_port(dev) == dev


def test_jonesey_missing_from_roster_silences_the_map(monkeypatch):
    dev = _with_board(monkeypatch, "1-2")
    monkeypatch.setattr(onboard_roster, "load_roster",
                        lambda *a, **k: {"gps_1234": "11:22:33:44:55:66"})
    usb_ports._reset_for_tests()
    assert case_port_label(dev) is None


def test_shadow_bus_is_never_translated(monkeypatch):
    # USB-3 shadow bus 2-1: which blue hole that is was never measured, so
    # even the fully verified medic gives the honest non-answer.
    dev = _with_board(monkeypatch, "2-1", tty="ttyACM3")
    assert usb_path_for_tty(dev) == "2-1"
    assert case_port_label(dev) is None
    assert describe_port(dev) == dev


def test_udevadm_failure_is_none(monkeypatch):
    _host(monkeypatch)

    def boom(dev):
        raise FileNotFoundError("udevadm")
    monkeypatch.setattr(usb_ports, "_run_udevadm", boom)
    usb_ports._reset_for_tests()
    # Jonesey can't be resolved either, so the identity check itself fails
    # closed — the double protection C1 and C2 describe.
    assert usb_path_for_tty("/dev/ttyACM1") is None
    assert describe_port("/dev/ttyACM1") == "/dev/ttyACM1"


def test_path_with_no_segment_is_none(monkeypatch):
    # A path with no plain <bus>-<port> component (the ":1.0" suffix must
    # disqualify a component, not be trimmed off it).
    dev = "/dev/ttyS0"
    _host(monkeypatch, paths={dev: "/devices/virtual/tty/ttyS0"})
    assert usb_path_for_tty(dev) is None
    assert describe_port(dev) == dev


def test_nonlocal_connections_show_the_raw_path(monkeypatch):
    # RNM_DEMO on the medic pins /dev/ttyACM0 for a board that is not plugged
    # in — Jonesey's hole. local=False (emulated/SSH) must bypass the map
    # even on the fully verified medic.
    dev = _with_board(monkeypatch, "1-2")
    assert describe_port(dev, local=False) == dev
    from transport.connection import EmulatedConnection, LocalConnection
    assert usb_ports.connection_is_local(LocalConnection())
    assert not usb_ports.connection_is_local(EmulatedConnection())
    assert not usb_ports.connection_is_local(None)


def test_falsy_device_passes_through(monkeypatch):
    _host(monkeypatch)
    assert describe_port("") == ""
    assert describe_port(None) is None
    assert usb_path_for_tty("") is None
    assert case_port_label("") is None


def test_verdict_and_segments_are_cached(monkeypatch):
    # One identity measurement and one udevadm fork per tty per TTL window —
    # the chooser redraw storm must not fork children on the UI thread.
    dev = _with_board(monkeypatch, "1-2")
    first = describe_port(dev)

    def no_more_identity(*a, **k):
        raise AssertionError("identity must be measured once per process")

    def no_more_udevadm(dev_):
        raise AssertionError("segment must come from the memo")

    monkeypatch.setattr(onboard_roster, "guard_is_active", no_more_identity)
    monkeypatch.setattr(usb_ports, "_run_udevadm", no_more_udevadm)
    assert describe_port(dev) == first == "Port 3 (/dev/ttyACM1)"
    # _reset_for_tests forgets both; the now-broken host fails the re-measure
    # (guard raises -> fail closed) and the map goes silent again.
    usb_ports._reset_for_tests()
    assert describe_port(dev) == dev
