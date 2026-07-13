import json

import pytest

from monitor.http_status import (
    poll_status,
    parse_status,
    status_colour,
    to_monitor_node,
    NodeStatus,
)

# Verbatim /status from the real medic-provisioned RTNode (MEDIC-TEST) — healthy.
HEALTHY = {
    "fork": "RTNode", "fw_version": "0.6.2", "rnode_proto": "1.85",
    "board_model": 63, "board": "heltec_v4", "psram": True,
    "uptime_ms": 83973029, "reset_reason": "unknown",
    "heap_internal_free": 201316, "heap_internal_min": 198216,
    "wdt_armed": True, "wdt_timeout_s": 60, "wifi_connected": True,
    "wifi_rssi": -64, "wifi_ip": "192.168.1.180", "lora_online": True,
    "tcp_backbone_connected": False, "local_tcp_server_up": True,
    "local_tcp_client_connected": False, "node_name": "MEDIC-TEST",
    "faults": [],
}


def getter(status_code=200, body=None, exc=None):
    def _get(url, timeout):
        if exc:
            raise exc
        return (status_code, body if body is not None else json.dumps(HEALTHY))
    return _get


# ---- parsing the real payload -------------------------------------------


def test_parse_real_healthy_status():
    ns = parse_status(HEALTHY)
    assert ns.reachable is True
    assert ns.status == "ok"
    assert ns.node_name == "MEDIC-TEST"
    assert ns.board == "heltec_v4"
    assert ns.firmware_version == "0.6.2"
    assert ns.wifi_connected is True
    assert ns.wifi_rssi_dbm == -64
    assert ns.wifi_ip == "192.168.1.180"
    assert ns.lora_online is True
    assert ns.local_tcp_server_up is True
    assert ns.uptime_s == 83973          # ms -> s
    assert ns.faults == []


# ---- status colour (mirrors beacon_status) ------------------------------


def test_healthy_is_ok():
    assert status_colour(HEALTHY) == "ok"


def test_any_fault_is_alert():
    assert status_colour({**HEALTHY, "faults": ["undervoltage"]}) == "alert"


def test_lora_down_is_alert():
    assert status_colour({**HEALTHY, "lora_online": False}) == "alert"


def test_weak_wifi_is_warn():
    assert status_colour({**HEALTHY, "wifi_rssi": -78}) == "warn"     # <= -75


def test_very_weak_wifi_is_alert():
    assert status_colour({**HEALTHY, "wifi_rssi": -88}) == "alert"    # <= -85


def test_watchdog_disarmed_is_warn():
    assert status_colour({**HEALTHY, "wdt_armed": False}) == "warn"


def test_missing_fields_default_healthy_not_alarmed():
    # a firmware that omits lora_online/wdt_armed shouldn't be falsely alerted
    assert status_colour({"faults": [], "wifi_connected": False}) == "ok"


# ---- poll_status (injected HTTP) ----------------------------------------


def test_poll_success():
    ns = poll_status("192.168.1.180", get=getter())
    assert ns.reachable and ns.status == "ok" and ns.node_name == "MEDIC-TEST"


def test_poll_unreachable_on_exception():
    ns = poll_status("10.0.0.9", get=getter(exc=OSError("no route")))
    assert ns.reachable is False and ns.status == "unreachable"


def test_poll_non_200_is_unreachable():
    ns = poll_status("x", get=getter(status_code=404, body="Not found"))
    assert ns.status == "unreachable"


def test_poll_bad_json_is_unreachable():
    ns = poll_status("x", get=getter(body="<html>oops"))
    assert ns.status == "unreachable"


def test_poll_builds_url_with_nondefault_port():
    seen = {}
    def _get(url, timeout):
        seen["url"] = url
        return (200, json.dumps(HEALTHY))
    poll_status("host", get=_get, port=8080)
    assert seen["url"] == "http://host:8080/status"


def test_poll_default_port_omits_port():
    seen = {}
    def _get(url, timeout):
        seen["url"] = url
        return (200, json.dumps(HEALTHY))
    poll_status("host", get=_get)
    assert seen["url"] == "http://host/status"


# ---- monitor node adapter -----------------------------------------------


def test_to_monitor_node_shape():
    node = to_monitor_node(parse_status(HEALTHY), location="Shed")
    assert node["name"] == "MEDIC-TEST"
    assert node["status"] == "ok"
    assert node["type"] == "rtnode2400"
    assert node["signal_dbm"] == -64
    assert node["location"] == "Shed"


def test_unreachable_node_maps_to_alert():
    node = to_monitor_node(NodeStatus(reachable=False, status="unreachable"))
    assert node["status"] == "alert"
    assert node["last_seen_hours"] > 24
