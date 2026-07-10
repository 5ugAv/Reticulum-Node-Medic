import pytest

from node_profile import NodeProfile
from workflows.rtnode_portal import (
    build_form,
    encode_form,
    submit_form,
    onboard,
    PORTAL_HOST,
    PORTAL_PATH,
    PORTAL_SSID,
    OPERATOR_FIELDS,
    PREFILLED_FIELDS,
)


def test_build_form_uses_real_portal_field_names():
    form = build_form(NodeProfile())
    # real firmware field names (POST /save), not placeholders
    for k in ("node_name", "ssid", "psk", "wifi_en",
              "freq", "bw", "sf", "cr", "txp"):
        assert k in form


# Every arg the firmware's `POST /save` handler reads (config_server->arg(...)),
# transcribed from RTNode-2400 FirewallConfig.h @ feature/neopixel-status-led.
# Any key build_form emits that is NOT here would be silently ignored by the
# board — a contract drift we want to fail loudly.
FIRMWARE_SAVE_ARGS = {
    "ssid", "psk", "wifi_en", "disp_blank", "disp_rot", "tcp_mode", "tcp_port",
    "bb_host", "bb_port", "ap_tcp_en", "ap_tcp_port", "ifac_en", "ifac_name",
    "ifac_pass", "advert_en", "advert_lat", "advert_lon", "advert_jitter",
    "node_name", "mdns_en", "mdns_name", "freq", "bw", "sf", "cr", "txp",
    "stal", "ltal",
}


def test_every_emitted_field_is_a_real_firmware_arg():
    # exercise all branches: with and without a GPS/advertisement location
    forms = [
        build_form(NodeProfile()),
        build_form(NodeProfile(), node_name="n", wifi_ssid="s", wifi_password="p",
                   lat=-37.8, lon=144.9),
    ]
    for form in forms:
        unknown = set(form) - FIRMWARE_SAVE_ARGS
        assert not unknown, f"fields the firmware ignores: {unknown}"


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


def test_location_advertisement_fuzzed_by_default():
    form = build_form(NodeProfile(), lat=-37.814, lon=144.963)
    assert form["advert_en"] == "1"
    assert form["advert_lat"] == "-37.814000"
    assert form["advert_lon"] == "144.963000"
    assert form["advert_jitter"] == "1"          # privacy fuzz ON by default


def test_location_advertisement_can_publish_exact():
    form = build_form(NodeProfile(), lat=-37.814, lon=144.963, jitter=False)
    assert form["advert_jitter"] == "0"


def test_no_coordinates_disables_advertisement_not_zero_zero():
    form = build_form(NodeProfile())
    assert form["advert_en"] == "0"
    assert "advert_lat" not in form              # never write 0,0


def test_advertise_false_disables_even_with_coords():
    form = build_form(NodeProfile(), lat=-37.8, lon=144.9, advertise=False)
    assert form["advert_en"] == "0"


def test_onboard_includes_location():
    def good_post(url, body, headers):
        assert "advert_lat=-37.814000" in body
        return (200, "reboot")
    ok, _ = onboard(NodeProfile(), "TRUTH", "MeshNet", "pw",
                    lat=-37.814, lon=144.963, do_join=False, post=good_post)
    assert ok is True


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


# ---- end-to-end onboarding (join AP -> POST) -----------------------------


def _good_post(url, body, headers):
    return (200, "Device will reboot in 3 seconds and connect to your WiFi network.")


def test_onboard_joins_then_posts():
    joined = {}

    def fake_join(ssid):
        joined["ssid"] = ssid
        return (True, "connected")

    ok, msg = onboard(NodeProfile(), "TRUTH", "MeshNet", "pw",
                      join_ap=fake_join, post=_good_post)
    assert ok is True
    assert joined["ssid"] == PORTAL_SSID


def test_onboard_aborts_if_join_fails_and_does_not_post():
    posted = {"called": False}

    def fake_join(ssid):
        return (False, "no wifi adapter")

    def fake_post(url, body, headers):
        posted["called"] = True
        return (200, "reboot")

    ok, msg = onboard(NodeProfile(), "TRUTH", "MeshNet", "pw",
                      join_ap=fake_join, post=fake_post)
    assert ok is False
    assert posted["called"] is False       # never posted — nothing to talk to
    assert "RTNode-Setup" in msg


def test_onboard_skip_join_posts_directly():
    ok, msg = onboard(NodeProfile(), "TRUTH", "MeshNet", "pw",
                      do_join=False, post=_good_post)
    assert ok is True
