from monitor.health_beacon import encode, decode
from monitor.registry import NodeRegistry
from monitor.formatting import beacon_lines

HASH = "eabdd142596bcae888242ec1b172d566"
NOW = 1_000_000.0


def _reg_with_beacon(**over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    kw.update(over)
    r = NodeRegistry()
    r.ingest(HASH, decode(encode(**kw)), NOW)
    return r.get(HASH)


def test_beacon_lines_no_beacon():
    r = NodeRegistry()
    rec = r.register(HASH, name="TRUTH")
    lines = beacon_lines(rec)
    assert lines == ["No health beacon received yet."]


def test_beacon_lines_report_key_fields():
    lines = beacon_lines(_reg_with_beacon())
    joined = "\n".join(lines)
    assert "Firmware: 0.6.2" in joined
    assert "Heltec32 V4" in joined
    assert "WiFi: up (-62 dBm)" in joined
    assert "LoRa: up" in joined
    assert "Watchdog: armed" in joined
    assert "Fault: no" in joined


def test_beacon_lines_wifi_down_hides_rssi():
    lines = beacon_lines(_reg_with_beacon(wifi_up=False, wifi_rssi_dbm=0))
    joined = "\n".join(lines)
    assert "WiFi: down" in joined
    assert "dBm" not in joined.split("LoRa")[0]  # no rssi shown for down wifi


def test_beacon_lines_flag_fault_and_unarmed_watchdog():
    lines = beacon_lines(_reg_with_beacon(fault=True, wdt_armed=False))
    joined = "\n".join(lines)
    assert "Fault: YES" in joined
    assert "NOT armed" in joined
