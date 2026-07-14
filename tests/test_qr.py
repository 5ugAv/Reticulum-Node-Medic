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
    "reticulum_address": "1be7e0923d8c0cc95af8ddb65aad804a",
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

def test_payload_leads_with_how_to_reach_the_node():
    text = birth_cert_payload(SAMPLE)
    lines = text.splitlines()
    assert lines[0].startswith("RETICULUM NODE")
    # reachability comes before build details
    assert text.index("Host:") < text.index("Board:")
    assert "rtt-prop-01.local" in text
    assert "IP: 192.168.1.42, 10.0.0.9" in text
    assert "MAC: b8:27:eb:aa:bb:cc" in text
    assert "Reticulum: 1be7e0923d8c0cc95af8ddb65aad804a" in text


def test_payload_includes_build_details_and_rgb_pin():
    text = birth_cert_payload(SAMPLE)
    assert "Board: Heltec LoRa32 v4 (fw 1.86), RGB pin 47" in text
    assert "Radio: 915.125 MHz BW125 SF9 CR5 17dBm" in text
    assert "Built: 20260714_104500" in text


def test_payload_omits_missing_fields_without_crashing():
    text = birth_cert_payload({"hostname": "bare", "ssh_address": "bare.local"})
    assert "Host: bare" in text
    assert "MAC:" not in text and "Reticulum:" not in text and "Radio:" not in text


def test_payload_no_rgb_pin_when_stock():
    stock = dict(SAMPLE, rgb_led_pin=None)
    text = birth_cert_payload(stock)
    assert "RGB pin" not in text
    assert "Board: Heltec LoRa32 v4 (fw 1.86)" in text


def test_payload_stays_compact_enough_to_scan():
    # keep the QR an easy scan — comfortably within a mid-version QR's capacity
    assert len(birth_cert_payload(SAMPLE)) < 400


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
