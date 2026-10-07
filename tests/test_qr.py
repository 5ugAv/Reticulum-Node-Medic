"""Birth-certificate QR sharing — the pure parts.

The QR is how the operator gets a certificate off a phone-less, offline medic:
scan it with any camera. The matrix generation leans on segno (a pure-Python
encoder) imported lazily, so these tests cover the payload we build and the
graceful fallback when segno is absent; the actual encode is exercised only when
segno is installed.
"""

import sys

import pytest

from ui.qr import birth_cert_payload, qr_matrix


SAMPLE = {
    "hostname": "rtt-prop-01",
    "ssh_address": "rtt-prop-01.local",
    "ip_addresses": ["192.168.1.42", "10.0.0.9"],
    "primary_interface": "wlan0",
    "mac_address": "b8:27:eb:aa:bb:cc",
    "reticulum_address": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "role": "LXMF propagation node",
    "board": "Heltec LoRa32 v4",
    "rnode_firmware": "1.86",
    "rgb_led_pin": 47,
    "frequency_mhz": 915.125,
    "bandwidth_khz": 125.0,
    "spreading_factor": 9,
    "coding_rate": 5,
    "tx_power_dbm": 17,
    "serial_port": "/dev/ttyACM0",
    "session_id": "20260714_104500",
}


# ---- payload -------------------------------------------------------------

def test_payload_is_anonymous_no_reachability_or_provenance():
    """THE anonymity guarantee (operator ethos 2026-08-01): a scanned QR must
    never reveal where the node lives, whose network it is on, or who built
    it — a wild node stays untraceable to a person or place."""
    cert = dict(SAMPLE, node_name="Rooftop-East",
                location="-37.512345, 145.523456 (map)",
                notes="Mounted on the water tank, 4m mast",
                built_by="nodemedic (a1b2c3d4)",
                identity_hash="b7c8d9e066778899")
    text = birth_cert_payload(cert)
    for leak in ("Host:", "IP:", "MAC: b8", "Reticulum:", "Identity:",
                 "Location:", "Notes:", "Built", "192.168", "rtt-prop-01",
                 "-37.512", "water tank", "nodemedic", "b7c8d9e0"):
        assert leak not in text, f"QR leaks: {leak!r}\n{text}"


def test_payload_carries_the_identity_lite_tier():
    cert = dict(SAMPLE, node_name="Rooftop-East", node_type="pi_rnode",
                born="2026-08-01 04:25")
    text = birth_cert_payload(cert)
    lines = text.splitlines()
    assert lines[0].startswith("RETICULUM NODE")
    assert lines[1] == "Name: Rooftop-East"
    assert "Type: Pi + RNode (propagation)" in text
    assert "Board: Heltec LoRa32 v4 (fw 1.86), RGB pin 47" in text
    assert "Radio: 915.125 MHz BW125 SF9 CR5 17dBm" in text
    assert "Born: 2026-08-01 04:25" in text


def test_payload_board_id_matches_the_glass():
    cert = {"node_type": "rnode", "board": "Heltec Wireless Tracker",
            "usb_serial": ("usb-Espressif_USB_JTAG_serial_debug_unit_"
                           "A1:B2:C3:D4:E5:F6-if00"),
            "radio": "915.125 MHz / BW125 / SF9 / CR5 / 17 dBm"}
    text = birth_cert_payload(cert)
    assert "Board ID: A1:B2:C3:D4:E5:F6  (screen ID E5F6)" in text
    assert "Radio: 915.125 MHz / BW125 / SF9 / CR5 / 17 dBm" in text
    assert "Type: RNode (radio for a host)" in text


def test_payload_omits_missing_fields_without_crashing():
    text = birth_cert_payload({"board": "bare"})
    assert "Board: bare" in text
    assert "Radio:" not in text and "Board ID:" not in text


# ---- matrix generation ---------------------------------------------------

def test_qr_matrix_returns_none_without_segno(monkeypatch):
    # block the import so the fallback path is deterministic regardless of env
    monkeypatch.setitem(sys.modules, "segno", None)
    assert qr_matrix("anything") is None


def test_qr_matrix_is_square_boolean_grid_when_segno_present():
    pytest.importorskip("segno")
    m = qr_matrix(birth_cert_payload(SAMPLE))
    assert m is not None
    assert len(m) == len(m[0])                       # square
    assert len(m) >= 21                              # at least a version-1 QR
    assert all(isinstance(cell, bool) for row in m for cell in row)
    # finder pattern: top-left module is dark
    assert m[0][0] is True
