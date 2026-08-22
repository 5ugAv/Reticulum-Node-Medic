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


def test_very_weak_wifi_is_warn_not_alert():
    # New intent: weak WiFi alone can only WARN, never alert (was -85 -> alert).
    assert status_colour({**HEALTHY, "wifi_rssi": -88}) == "warn"


def test_faith_regression_weak_wifi_healthy_node_is_warn():
    # FAITH regression: faults=[], lora_online=True, wifi_connected, -87 dBm.
    # Must be WARN, never alert.
    d = {**HEALTHY, "faults": [], "lora_online": True,
         "wifi_connected": True, "wifi_rssi": -87}
    assert status_colour(d) == "warn"


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


# ---- hostile /status is survivable: one bad node never aborts the sweep -----
# Everything below is untrusted JSON off the LAN. parse_status feeds a sweep of
# MANY nodes, so a single malformed body must become a clean result, never an
# exception that blinds the medic to every other node.


def test_string_uptime_ms_does_not_raise():
    ns = parse_status(dict(HEALTHY, uptime_ms="not-a-number"))
    assert ns.uptime_s == 0                       # safe fallback, no crash


def test_list_uptime_ms_does_not_raise():
    ns = parse_status(dict(HEALTHY, uptime_ms=[1, 2, 3]))
    assert ns.uptime_s == 0


def test_non_list_faults_is_treated_as_no_faults():
    ns = parse_status(dict(HEALTHY, faults="boom"))
    assert ns.faults == []
    assert ns.status != "alert"                   # a string must not fake an alert


def test_faults_are_bounded_in_count_and_length():
    from monitor.http_status import MAX_FAULTS, MAX_FAULT_LEN
    ns = parse_status(dict(HEALTHY, faults=["x" * 100_000] * 5_000))
    assert len(ns.faults) <= MAX_FAULTS
    assert all(len(f) <= MAX_FAULT_LEN for f in ns.faults)


def test_faults_of_wrong_element_type_are_dropped():
    ns = parse_status(dict(HEALTHY, faults=[{"a": 1}, 5, None, "real"]))
    assert ns.faults == ["real"]


def test_non_string_text_fields_coerce_to_empty():
    ns = parse_status(dict(HEALTHY, node_name={"x": 1}, board=["a"], reset_reason=7))
    assert ns.node_name == "" and ns.board == "" and ns.reset_reason == ""


def test_poll_status_never_raises_on_a_hostile_body():
    hostile = json.dumps({"uptime_ms": [1, 2, 3], "faults": {"nope": 1},
                          "node_name": {"x": 1}, "wifi_rssi": "loud"})
    ns = poll_status("h", get=getter(200, hostile))   # must not raise
    assert ns.reachable is True and ns.uptime_s == 0 and ns.faults == []
    assert ns.wifi_rssi_dbm is None


def test_oversize_body_is_capped_by_the_default_getter(monkeypatch):
    import monitor.http_status as hs

    class _Resp:
        status = 200
        _data = b"x" * (hs.MAX_STATUS_BYTES * 4)

        def read(self, n=-1):
            return self._data[:n] if n and n >= 0 else self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, url, timeout):
            return _Resp()

    monkeypatch.setattr(hs, "_NO_PROXY_OPENER", _Opener())
    code, body = hs._default_get("http://h/status", 1.0)
    assert code == 200 and len(body) <= hs.MAX_STATUS_BYTES


# ---- non-finite floats: json.loads accepts Infinity/NaN, int() on them raises


def test_infinity_and_nan_fields_never_raise():
    for v in (float("inf"), float("-inf"), float("nan")):
        assert parse_status(dict(HEALTHY, uptime_ms=v)).uptime_s == 0
    for v in (float("inf"), float("nan")):
        assert parse_status(dict(HEALTHY, wifi_rssi=v)).wifi_rssi_dbm is None


def test_json_body_with_Infinity_and_NaN_is_survivable():
    # Python's json.loads decodes these tokens by default — a hostile node can
    # send them and the sweep must not crash.
    body = '{"uptime_ms": Infinity, "wifi_rssi": NaN, "faults": []}'
    ns = poll_status("h", get=getter(200, body))     # must not raise
    assert ns.reachable is True
    assert ns.uptime_s == 0 and ns.wifi_rssi_dbm is None
