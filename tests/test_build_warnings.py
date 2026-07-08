import pytest

from node_profile import NodeHardware
from workflows.build_warnings import BUILD_WARNINGS, warnings_for, warning_ids


def test_all_four_warnings_defined():
    assert warning_ids() == {89, 90, 91, 93}


def test_universal_warnings_always_present():
    keys = {w["key"] for w in warnings_for(NodeHardware.PI_5)}
    assert "usb_data_cable" in keys      # 89
    assert "antenna_band" in keys        # 90


def test_heltec_antenna_port_only_for_heltec():
    heltec = {w["key"] for w in warnings_for(NodeHardware.HELTEC_V4)}
    pi = {w["key"] for w in warnings_for(NodeHardware.PI_5)}
    assert "heltec_antenna_port" in heltec   # 91
    assert "heltec_antenna_port" not in pi


def test_captive_portal_only_when_wifi():
    with_wifi = {w["key"] for w in warnings_for(NodeHardware.HELTEC_V4, wifi=True)}
    without = {w["key"] for w in warnings_for(NodeHardware.PI_5, wifi=False)}
    assert "captive_portal" in with_wifi      # 93
    assert "captive_portal" not in without


def test_warning_text_is_plain_english():
    for w in BUILD_WARNINGS:
        assert isinstance(w["text"], str) and len(w["text"]) > 10


def test_usb_warning_mentions_data_cable():
    w = next(w for w in BUILD_WARNINGS if w["key"] == "usb_data_cable")
    assert "data" in w["text"].lower()


def test_antenna_band_mentions_915():
    w = next(w for w in BUILD_WARNINGS if w["key"] == "antenna_band")
    assert "915" in w["text"]
