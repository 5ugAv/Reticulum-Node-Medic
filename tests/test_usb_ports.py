"""ui/usb_ports.py — engraved-hole labels for kernel USB paths.

Everything runs against a mocked udevadm: the map is a measurement of the
medic's wiring (2026-08-21) and CI machines have no such holes. What matters
here is the honesty contract — measured segments translate, everything else
(shadow buses, hub-only paths, udevadm failure) says nothing extra.
"""

import subprocess
from types import SimpleNamespace

import pytest

from ui import usb_ports
from ui.usb_ports import (MEDIC_PORT_MAP, case_port_label, describe_port,
                          usb_path_for_tty)


def _udevadm(path, rc=0):
    """A subprocess.run stand-in answering with a canned sysfs *path*."""
    def run(cmd, **kwargs):
        assert cmd[:4] == ["udevadm", "info", "-q", "path"]
        return SimpleNamespace(returncode=rc, stdout=path, stderr="")
    return run


SYS = "/devices/platform/axi/1000120000.pcie/1f00300000.usb/xhci-hcd.1/usb{b}/{seg}/{seg}:1.0/tty/{tty}"


def test_map_matches_the_2026_08_21_measurement():
    # Four holes, four segments; Jonesey's hole is Port 1.
    assert MEDIC_PORT_MAP == {"3-2": 1, "1-1": 2, "1-2": 3, "3-1": 4}


def test_direct_segment_translates(monkeypatch):
    monkeypatch.setattr(usb_ports.subprocess, "run",
                        _udevadm(SYS.format(b=1, seg="1-2", tty="ttyACM1")))
    assert usb_path_for_tty("/dev/ttyACM1") == "1-2"
    assert case_port_label("/dev/ttyACM1") == "Port 3"
    assert describe_port("/dev/ttyACM1") == "Port 3 (/dev/ttyACM1)"


def test_dotted_hub_chain_names_the_root_hole(monkeypatch):
    # A board on a hub in the top-right hole: 1-1.3 -> the hole is 1-1.
    path = ("/devices/platform/axi/usb1/1-1/1-1.3/1-1.3:1.0/tty/ttyACM2")
    monkeypatch.setattr(usb_ports.subprocess, "run", _udevadm(path))
    assert usb_path_for_tty("/dev/ttyACM2") == "1-1.3"
    assert case_port_label("/dev/ttyACM2") == "Port 2 (via hub)"
    assert describe_port("/dev/ttyACM2") == "Port 2 (via hub) (/dev/ttyACM2)"


def test_shadow_bus_is_never_translated(monkeypatch):
    # USB-3 shadow bus 2-1: which blue hole that is was never measured, so
    # the honest answer is no label at all.
    monkeypatch.setattr(usb_ports.subprocess, "run",
                        _udevadm(SYS.format(b=2, seg="2-1", tty="ttyACM0")))
    assert usb_path_for_tty("/dev/ttyACM0") == "2-1"
    assert case_port_label("/dev/ttyACM0") is None
    assert describe_port("/dev/ttyACM0") == "/dev/ttyACM0"


def test_udevadm_missing_or_failing_is_none(monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("udevadm")
    monkeypatch.setattr(usb_ports.subprocess, "run", boom)
    assert usb_path_for_tty("/dev/ttyACM1") is None
    assert case_port_label("/dev/ttyACM1") is None
    assert describe_port("/dev/ttyACM1") == "/dev/ttyACM1"

    monkeypatch.setattr(usb_ports.subprocess, "run", _udevadm("", rc=2))
    assert usb_path_for_tty("/dev/ttyACM1") is None

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 3))
    monkeypatch.setattr(usb_ports.subprocess, "run", slow)
    assert usb_path_for_tty("/dev/ttyACM1") is None


def test_path_with_no_segment_is_none(monkeypatch):
    # A path with hubs/interfaces but no plain <bus>-<port> component (the
    # ":1.0" suffix must disqualify a component, not be trimmed off it).
    monkeypatch.setattr(usb_ports.subprocess, "run",
                        _udevadm("/devices/virtual/tty/ttyS0"))
    assert usb_path_for_tty("/dev/ttyS0") is None
    assert describe_port("/dev/ttyS0") == "/dev/ttyS0"


def test_empty_device_is_honest():
    assert usb_path_for_tty("") is None
    assert case_port_label("") is None
    assert describe_port("") == ""
