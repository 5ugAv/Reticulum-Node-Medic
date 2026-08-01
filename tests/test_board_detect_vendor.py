"""Bridge-vs-native is decided by the USB VENDOR, not the port's name.

Bench, 2026-08-02: a LilyGO LoRa32 appeared on /dev/ttyACM1 behind vendor 1a86
(QinHeng CH9102). CH343/CH9102 are BRIDGE chips that enumerate as CDC, so they
land on ttyACM — and the old name-based rule therefore called a bridged board
"native", inverting the answer for every recent LilyGO board.
"""

import pytest

from ui import board_detect as bd


@pytest.mark.parametrize("vendor,expect", [
    ("10c4", "bridge"),   # Silicon Labs CP2102 — Heltec V3
    ("1a86", "bridge"),   # QinHeng CH9102 — the board on the bench
    ("0403", "bridge"),   # FTDI
    ("067b", "bridge"),   # Prolific
    ("303a", "native"),   # Espressif S3 USB-JTAG — Jonesey
    ("2e8a", "native"),   # Raspberry Pi gadget
    ("239a", "native"),   # Adafruit nRF52840
])
def test_vendor_decides_the_kind(monkeypatch, vendor, expect):
    monkeypatch.setattr(bd, "port_usb_vendor", lambda p: vendor)
    assert bd._port_usb_kind("/dev/ttyACM1") == expect


def test_a_bridge_on_ttyACM_is_not_called_native(monkeypatch):
    """The exact inversion: without this, a CH9102 board is 'native' and every
    bridged board gets excluded from the shortlist."""
    monkeypatch.setattr(bd, "port_usb_vendor", lambda p: "1a86")
    assert bd._port_usb_kind("/dev/ttyACM1") == "bridge"


def test_the_name_rule_survives_as_a_fallback(monkeypatch):
    """Unknown or unreadable vendor: fall back to the old heuristic rather than
    refusing to answer — a wrong exclusion costs one tap, silence costs more."""
    monkeypatch.setattr(bd, "port_usb_vendor", lambda p: "")
    assert bd._port_usb_kind("/dev/ttyUSB0") == "bridge"
    assert bd._port_usb_kind("/dev/ttyACM0") == "native"
    assert bd._port_usb_kind("") is None


def test_vendor_lookup_never_raises_on_a_bogus_port():
    assert bd.port_usb_vendor("/dev/nonexistent-xyz") == ""
    assert bd.port_usb_vendor("") == ""
