import pytest

from node_profile import NodeProfile
from workflows.rtnode_portal import (
    build_form,
    encode_form,
    submit_form,
    PORTAL_HOST,
    PORTAL_PATH,
    OPERATOR_FIELDS,
    PREFILLED_FIELDS,
)


def test_build_form_uses_real_portal_field_names():
    form = build_form(NodeProfile())
    # real firmware field names (POST /save), not placeholders
    for k in ("node_name", "ssid", "psk", "wifi_en",
              "freq", "bw", "sf", "cr", "txp"):
        assert k in form


def test_recommended_lora_values_and_units():
    form = build_form(NodeProfile())
    assert form["freq"] == "915.125"     # MHz decimal string
    assert form["bw"] == "125000"        # Hz integer
    assert form["sf"] == "9"
    assert form["cr"] == "5"
    assert form["txp"] == "17"


def test_operator_fields_blank_by_default():
    form = build_form(NodeProfile())
    assert form["node_name"] == ""
    assert form["ssid"] == ""
    assert form["psk"] == ""
    assert form["wifi_en"] == "0"        # no creds -> LoRa-only


def test_operator_values_flow_in_and_enable_wifi():
    form = build_form(NodeProfile(), node_name="TRUTH",
                      wifi_ssid="MeshNet", wifi_password="s3cret")
    assert form["node_name"] == "TRUTH"
    assert form["ssid"] == "MeshNet"
    assert form["psk"] == "s3cret"
    assert form["wifi_en"] == "1"        # creds present -> WiFi enabled


def test_overridden_radio_params_flow_through():
    p = NodeProfile()
    p.radio.frequency_mhz = 868.0
    p.radio.bandwidth_khz = 250.0
    form = build_form(p)
    assert form["freq"] == "868.0"
    assert form["bw"] == "250000"


def test_field_partition_constants():
    assert set(OPERATOR_FIELDS) == {"node_name", "ssid", "psk"}
    assert set(PREFILLED_FIELDS) == {"freq", "bw", "sf", "cr", "txp"}


def test_encode_form_is_urlencoded():
    body = encode_form({"node_name": "a b", "freq": "915.125"})
    assert "node_name=a+b" in body or "node_name=a%20b" in body
    assert "freq=915.125" in body


def test_submit_success_on_200_with_reboot_text():
    calls = {}

    def fake_post(url, body, headers):
        calls["url"] = url
        calls["body"] = body
        return (200, "<html>Device will reboot in 3 seconds and connect to "
                     "your WiFi network.</html>")

    form = build_form(NodeProfile(), node_name="TRUTH",
                      wifi_ssid="MeshNet", wifi_password="pw")
    ok, msg = submit_form(form, post=fake_post)
    assert ok is True
    assert calls["url"] == f"http://{PORTAL_HOST}{PORTAL_PATH}"
    assert "node_name=TRUTH" in calls["body"]
    assert "ssid=MeshNet" in calls["body"]


def test_submit_failure_on_non_200():
    def fake_post(url, body, headers):
        return (404, "not found")
    ok, msg = submit_form(build_form(NodeProfile()), post=fake_post)
    assert ok is False


def test_submit_handles_transport_error():
    def fake_post(url, body, headers):
        raise OSError("network unreachable")
    ok, msg = submit_form(build_form(NodeProfile()), post=fake_post)
    assert ok is False
    assert "unreachable" in msg.lower() or "could not" in msg.lower()
