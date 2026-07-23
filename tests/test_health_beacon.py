import pytest

from monitor.health_beacon import (
    HealthBeacon,
    decode,
    encode,
    beacon_status,
    RESET_REASONS,
    BOARD_IDS,
    PAYLOAD_LEN,
)


def sample_bytes(**over):
    kw = dict(
        uptime_s=7200, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
        wifi_up=True, lora_up=True, tcp_backbone_up=True,
        local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
        board_id=0x3F, fw=(0, 6, 2),
    )
    kw.update(over)
    return encode(**kw)


# Cross-project golden vector agreed with the RTNode-2400 firmware side.
# uptime=7200s, heap=140KB, rssi=-62, reset=poweron, flags b0..b5 set
# (wifi/lora/backbone/local/wdt/psram), board 0x3F Heltec V4, fw 0.6.2.
GOLDEN = bytes.fromhex("0100001C20008CC2003F3F000602")


def test_golden_vector_encode_is_byte_exact():
    assert sample_bytes() == GOLDEN


# Second golden vector — a REAL capture from a Light-RTnode-2400 (Heltec V4),
# supplied by the firmware side. identity 378ac3ed…, rtnode.health dst
# eabdd142596bcae888242ec1b172d566. The dst hash is board-specific; the
# app_data below is the portable contract artifact.
REAL_HW = bytes.fromhex("010000002400c7cc053b3f000602")
REAL_HW_DEST_HASH = "eabdd142596bcae888242ec1b172d566"


def test_real_hardware_vector_decodes():
    b = decode(REAL_HW)
    assert b.format_version == 1
    assert b.firmware_version == "0.6.2"
    assert b.board_label == "Heltec32 V4"
    assert b.uptime_s == 36
    assert b.free_heap_kb == 199
    assert b.wifi_rssi_dbm == -52
    assert b.reset_reason_label == "other"
    assert (b.wifi_up, b.lora_up, b.tcp_backbone_up, b.local_tcp_server_up,
            b.wdt_armed, b.psram, b.fault, b.airtime_lock) == (
        True, True, False, True, True, True, False, False)


def test_real_hardware_vector_status_ok():
    # tcp_backbone down does NOT affect the traffic-light — only fault/lora/
    # wifi/wdt do — so a live leaf node still reads "ok".
    assert beacon_status(decode(REAL_HW)) == "ok"


def test_golden_vector_decode():
    b = decode(GOLDEN)
    assert b.uptime_s == 7200
    assert b.free_heap_kb == 140
    assert b.wifi_rssi_dbm == -62
    assert b.reset_reason_label == "poweron"
    assert (b.wifi_up, b.lora_up, b.tcp_backbone_up, b.local_tcp_server_up,
            b.wdt_armed, b.psram) == (True, True, True, True, True, True)
    assert b.fault is False
    assert b.airtime_lock is False
    assert b.board_label == "Heltec32 V4"
    assert b.firmware_version == "0.6.2"


def test_to_bytes_round_trips_golden():
    assert decode(GOLDEN).to_bytes() == GOLDEN


def test_to_bytes_round_trips_real_hardware():
    assert decode(REAL_HW).to_bytes() == REAL_HW


def test_to_bytes_round_trips_with_airtime_lock():
    raw = sample_bytes(airtime_lock=True, fault=True)
    assert decode(raw).to_bytes() == raw


def test_airtime_lock_bit7():
    b = decode(sample_bytes(airtime_lock=True))
    assert b.airtime_lock is True
    # airtime lock is normal throttling, not an alert on its own
    assert beacon_status(b) in ("ok", "warn")


def test_full_board_id_enum_present():
    # a representative spread of the shared RNode board-type bytes
    assert BOARD_IDS[0x38] == "Heltec32 V2"
    assert BOARD_IDS[0x3A] == "Heltec32 V3"
    assert BOARD_IDS[0x3F] == "Heltec32 V4"
    assert BOARD_IDS[0x51] == "RAK4631"


def test_unknown_board_id_is_labelled():
    b = decode(sample_bytes(board_id=0x99))
    assert "unknown" in b.board_label
    assert "0x99" in b.board_label


def test_payload_is_14_bytes():
    assert PAYLOAD_LEN == 14
    assert len(sample_bytes()) == 14


def test_roundtrip_basic_fields():
    b = decode(sample_bytes())
    assert b.format_version == 1
    assert b.uptime_s == 7200
    assert b.free_heap_kb == 140
    assert b.wifi_rssi_dbm == -62
    assert b.reset_reason == 0
    assert b.board_id == 0x3F
    assert b.firmware_version == "0.6.2"


def test_big_endian_uptime():
    raw = sample_bytes(uptime_s=0x01020304)
    assert raw[1:5] == bytes([0x01, 0x02, 0x03, 0x04])


def test_negative_rssi_is_signed_int8():
    b = decode(sample_bytes(wifi_rssi_dbm=-90))
    assert b.wifi_rssi_dbm == -90


def test_flags_decode():
    b = decode(sample_bytes(
        wifi_up=True, lora_up=False, tcp_backbone_up=True,
        local_tcp_server_up=False, wdt_armed=True, psram=False, fault=True))
    assert b.wifi_up is True
    assert b.lora_up is False
    assert b.tcp_backbone_up is True
    assert b.local_tcp_server_up is False
    assert b.wdt_armed is True
    assert b.psram is False
    assert b.fault is True


def test_reset_reason_label():
    b = decode(sample_bytes(reset_reason=3))
    assert b.reset_reason_label == "task_wdt"
    assert RESET_REASONS[1] == "panic"


def test_board_label():
    b = decode(sample_bytes(board_id=0x3F))
    assert b.board_label == BOARD_IDS[0x3F]


def test_decode_rejects_short_payload():
    with pytest.raises(ValueError):
        decode(b"\x01\x02")


def test_decode_tolerates_trailing_bytes_for_future_versions():
    # a v2 payload that appends a byte must still decode the v1 prefix
    raw = sample_bytes() + b"\x64"  # e.g. future battery SoC %
    b = decode(raw)
    assert b.uptime_s == 7200


# ---- status mapping (drives the Monitor dashboard) -----------------------


def test_status_ok():
    assert beacon_status(decode(sample_bytes())) == "ok"


def test_fault_bit_forces_alert():
    assert beacon_status(decode(sample_bytes(fault=True))) == "alert"


def test_lora_down_is_alert():
    assert beacon_status(decode(sample_bytes(lora_up=False))) == "alert"


def test_weak_wifi_is_warn():
    assert beacon_status(decode(sample_bytes(wifi_rssi_dbm=-80))) == "warn"


def test_very_weak_wifi_is_warn_not_alert():
    # New intent: weak WiFi alone can only WARN, never alert. RED is reserved
    # for real faults / LoRa down (previously -90 dBm escalated to "alert").
    assert beacon_status(decode(sample_bytes(wifi_rssi_dbm=-90))) == "warn"


def test_faith_regression_weak_wifi_healthy_node_is_warn():
    # FAITH regression: faults empty, LoRa up, WiFi up but -87 dBm while
    # associating. Must be WARN (orange), NOT alert — a healthy node stays out
    # of the red/alert banner just because its WiFi is weak.
    b = decode(sample_bytes(
        fault=False, lora_up=True, wifi_up=True, wifi_rssi_dbm=-87))
    assert beacon_status(b) == "warn"


def test_watchdog_not_armed_is_warn():
    assert beacon_status(decode(sample_bytes(wdt_armed=False))) == "warn"


def test_wifi_down_ignores_rssi():
    # wifi down (rssi sentinel 0) must not read as a signal alert
    b = decode(sample_bytes(wifi_up=False, wifi_rssi_dbm=0))
    assert beacon_status(b) in ("ok", "warn")
