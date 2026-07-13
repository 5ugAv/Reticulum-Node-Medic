import pytest

from monitor.health_beacon import encode, decode
from monitor.health_poll import PollResult
from monitor.http_status import NodeStatus
from monitor.registry import NodeRegistry, NodeRecord, STALE_ALERT_HOURS


def http(status="ok", reachable=True, name="MEDIC-TEST", fw="0.6.2", faults=None):
    return NodeStatus(reachable=reachable, status=status, node_name=name,
                      firmware_version=fw, lora_online=True,
                      local_tcp_server_up=True, faults=faults or [])


def test_record_http_status_registers_and_adopts_name():
    reg = NodeRegistry()
    rec = reg.record_http_status(HASH, http(name="MEDIC-TEST"), NOW)
    assert rec.name == "MEDIC-TEST"
    assert rec.last_seen == NOW
    assert rec.status(NOW) == "ok"
    assert rec.firmware_version == "0.6.2"


def test_http_status_drives_traffic_light():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(status="alert", faults=["undervoltage"]), NOW)
    assert reg.get(HASH).status(NOW) == "alert"


def test_http_preferred_over_beacon_when_reachable():
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(fault=True), NOW)          # beacon says alert
    reg.record_http_status(HASH, http(status="ok"), NOW)   # but HTTP says ok
    assert reg.get(HASH).status(NOW) == "ok"


def test_unreachable_http_does_not_refresh_last_seen():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(), NOW)
    reg.record_http_status(HASH, http(reachable=False, status="unreachable"),
                           NOW + HOUR)
    # last_seen stayed at NOW -> staleness governs, not the failed poll
    assert reg.get(HASH).last_seen == NOW


def test_http_node_goes_stale_to_alert():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(), NOW)
    assert reg.get(HASH).status(NOW + (STALE_ALERT_HOURS + 1) * HOUR) == "alert"


def test_to_dashboard_dict_shape_for_screen():
    reg = NodeRegistry()
    reg.register(HASH, name="MEDIC-TEST", location="Bench", node_type="rtnode2400")
    reg.record_http_status(HASH, http(name="MEDIC-TEST"), NOW)
    reg.get(HASH).latest_http.wifi_rssi_dbm = -64
    d = reg.get(HASH).to_dashboard(NOW)
    assert d["name"] == "MEDIC-TEST"
    assert d["location"] == "Bench"
    assert d["status"] == "ok"
    assert d["type"] == "rtnode2400"
    assert d["signal_dbm"] == -64
    assert d["last_seen_hours"] == 0.0


def test_to_dashboard_signal_falls_back_to_beacon():
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(wifi_rssi_dbm=-70), NOW)
    assert reg.get(HASH).to_dashboard(NOW)["signal_dbm"] == -70


# ---- mesh ingest (rnpath reachability) ----------------------------------


def _mesh(dst, hops=1, iface="RNodeInterface[RNode LoRa Interface]"):
    from monitor.mesh import MeshNode
    return MeshNode(dst_hash=dst, hops=hops, interface=iface)


def test_ingest_mesh_registers_and_marks_reachable():
    reg = NodeRegistry()
    rec = reg.ingest_mesh(_mesh(HASH, hops=2), NOW)
    assert rec.dst_hash == HASH
    assert rec.mesh_hops == 2
    assert rec.last_seen == NOW
    # reachable via mesh, health unknown -> ok (not "unknown")
    assert rec.status(NOW) == "ok"


def test_mesh_only_node_goes_alert_when_stale():
    reg = NodeRegistry()
    reg.ingest_mesh(_mesh(HASH), NOW)
    assert reg.get(HASH).status(NOW + (STALE_ALERT_HOURS + 1) * HOUR) == "alert"


def test_http_health_still_preferred_over_mesh_reachability():
    reg = NodeRegistry()
    reg.ingest_mesh(_mesh(HASH), NOW)
    reg.record_http_status(HASH, http(status="warn"), NOW)   # richer signal wins
    assert reg.get(HASH).status(NOW) == "warn"


def test_ingest_mesh_keeps_known_node_name():
    reg = NodeRegistry()
    reg.register(HASH, name="EVERYWHERE", location="House")
    reg.ingest_mesh(_mesh(HASH), NOW)
    assert reg.get(HASH).name == "EVERYWHERE"    # birth-cert name preserved

HASH = "eabdd142596bcae888242ec1b172d566"
HASH2 = "aa11bb22cc33dd44ee55ff6600778899"

HOUR = 3600.0
NOW = 1_000_000.0


def beacon(**over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    kw.update(over)
    return decode(encode(**kw))


def line(dst=HASH, **over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    kw.update(over)
    return f"[HealthBeacon] announce dst={dst} data={encode(**kw).hex()}"


def test_register_creates_node_with_metadata():
    r = NodeRegistry()
    rec = r.register(HASH, name="TRUTH", location="Northcote", node_type="rtnode2400")
    assert isinstance(rec, NodeRecord)
    assert r.get(HASH).name == "TRUTH"
    assert r.get(HASH).location == "Northcote"


def test_ingest_updates_beacon_and_last_seen():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    r.ingest(HASH, beacon(), NOW)
    rec = r.get(HASH)
    assert rec.latest_beacon is not None
    assert rec.last_seen == NOW
    assert rec.status(NOW) == "ok"


def test_ingest_unknown_hash_auto_registers():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH) is not None      # first-seen node appears
    assert r.get(HASH).status(NOW) == "ok"


def test_fault_beacon_is_alert():
    r = NodeRegistry()
    r.ingest(HASH, beacon(fault=True), NOW)
    assert r.get(HASH).status(NOW) == "alert"


def test_weak_wifi_is_warn():
    r = NodeRegistry()
    r.ingest(HASH, beacon(wifi_rssi_dbm=-80), NOW)
    assert r.get(HASH).status(NOW) == "warn"


def test_staleness_over_six_hours_is_alert_even_if_last_ok():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)               # last beacon was OK
    later = NOW + (STALE_ALERT_HOURS + 0.5) * HOUR
    assert r.get(HASH).status(later) == "alert"  # not heard -> red


def test_recent_ok_within_window_stays_ok():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH).status(NOW + 2 * HOUR) == "ok"


def test_never_heard_is_unknown():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    assert r.get(HASH).status(NOW) == "unknown"


def test_last_seen_hours():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH).last_seen_hours(NOW + 3 * HOUR) == pytest.approx(3.0)


def test_ingest_line_parses_and_stores():
    r = NodeRegistry()
    rec = r.ingest_line(line(), NOW)
    assert rec is not None
    assert r.get(HASH).status(NOW) == "ok"
    assert r.get(HASH).latest_beacon.firmware_version == "0.6.2"


def test_ingest_line_ignores_non_beacon():
    r = NodeRegistry()
    assert r.ingest_line("[WATCHDOG] heap=180000", NOW) is None
    assert r.ingest_line("garbage", NOW) is None


def test_summary_counts_by_status():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)                       # ok
    r.ingest(HASH2, beacon(fault=True), NOW)            # alert
    s = r.summary(NOW)
    assert s["ok"] == 1
    assert s["alert"] == 1
    assert s["warn"] == 0


def test_filter_by_status_and_search():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    r.ingest(HASH, beacon(), NOW)
    r.register(HASH2, name="Thornbury")
    r.ingest(HASH2, beacon(fault=True), NOW)
    ok_nodes = r.visible(NOW, status="ok")
    assert [n.name for n in ok_nodes] == ["TRUTH"]
    found = r.visible(NOW, search="thorn")
    assert [n.name for n in found] == ["Thornbury"]


def test_all_sorted_alert_first():
    r = NodeRegistry()
    r.register(HASH, name="Aaa"); r.ingest(HASH, beacon(), NOW)               # ok
    r.register(HASH2, name="Bbb"); r.ingest(HASH2, beacon(fault=True), NOW)   # alert
    names = [n.name for n in r.all(NOW)]
    assert names[0] == "Bbb"    # alert first


def test_record_poll_ingests_clean_reply():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    result = PollResult(node_status="ok", reachable=True, attempts=1, beacon=beacon())
    r.record_poll(HASH, result, NOW)
    assert r.get(HASH).status(NOW) == "ok"   # cleared to green


def test_record_poll_unreachable_does_not_update_last_seen():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    result = PollResult(node_status="unreachable", reachable=False, attempts=3, beacon=None)
    r.record_poll(HASH, result, NOW)
    assert r.get(HASH).last_seen is None


def test_ingest_announce_adapter_decodes_and_stores():
    r = NodeRegistry()
    dst = bytes.fromhex(HASH)
    app_data = encode(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
                      wifi_up=True, lora_up=True, tcp_backbone_up=True,
                      local_tcp_server_up=True, wdt_armed=True, psram=True,
                      fault=False, board_id=0x3F, fw=(0, 6, 2))
    rec = r.ingest_announce(dst, app_data, NOW)
    assert rec is not None
    assert r.get(HASH).status(NOW) == "ok"
    assert r.get(HASH).latest_beacon.board_label == "Heltec32 V4"


def test_located_nodes_only_returns_geotagged():
    r = NodeRegistry()
    r.register(HASH, name="FAITH")
    rec = r.get(HASH); rec.lat = -37.814; rec.lon = 144.963
    r.ingest(HASH, beacon(), NOW)                 # gives it an 'ok' status
    r.register(HASH2, name="NOLOC")               # no coordinates -> omitted
    pts = r.located_nodes(NOW)
    assert [p["name"] for p in pts] == ["FAITH"]
    assert pts[0]["lat"] == -37.814 and pts[0]["lon"] == 144.963
    assert pts[0]["status"] == "ok"


def test_located_nodes_empty_when_none_geotagged():
    r = NodeRegistry()
    r.register(HASH, name="NOLOC")
    assert r.located_nodes(NOW) == []


def test_ingest_announce_rejects_bad_payload():
    r = NodeRegistry()
    assert r.ingest_announce(bytes.fromhex(HASH), b"\x01\x02", NOW) is None
