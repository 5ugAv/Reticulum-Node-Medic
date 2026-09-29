import pytest

from monitor import health_beacon as hb
from monitor.health_beacon import (
    HealthBeacon,
    decode,
    encode,
    beacon_status,
    battery_reason,
    link_reason,
    RESET_REASONS,
    BOARD_IDS,
    PAYLOAD_LEN,
    PAYLOAD_LEN_V2,
    FORMAT_VERSION_V2,
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


# Second golden vector — the app_data BYTES are a real capture from a
# Light-RTnode-2400 (Heltec V4), supplied by the firmware side. The identity
# and destination hash that came with it were board-specific addresses on a
# live mesh, so they are replaced here with patterned synthetics; the
# app_data is the portable contract artifact and is untouched.
REAL_HW = bytes.fromhex("010000002400c7cc053b3f000602")
REAL_HW_DEST_HASH = "11223344556677889900aabbccddeeff"


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


# ---- v2: power + link tail -----------------------------------------------
# Every birthed node (RTNode-2400 VBAT, Pi+RNode UPS) can now report battery +
# its own LoRa link. The tail is append-only: v1 tools read the shared prefix,
# v2 tools read the tail when the payload is long enough.


def sample_v2(**over):
    kw = dict(
        uptime_s=7200, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
        wifi_up=True, lora_up=True, tcp_backbone_up=True,
        local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
        board_id=0x3F, fw=(0, 7, 0),
        battery_mv=3940, battery_pct=78, on_battery=True, on_solar=True,
        charging=True, lora_snr_db=6, lora_rssi_dbm=-92,
    )
    kw.update(over)
    return encode(**kw)


# Cross-project v2 golden vector — locked byte-for-byte with the RTNode-2400
# firmware packer (firmware/rtnode-2400/HealthBeaconPack.h; see
# tests/test_firmware_beacon_contract.py). Same base as GOLDEN + the v2 tail:
# battery 3940 mV / 78% / on-battery+charging+solar (0x07), SNR 6, RSSI -92.
GOLDEN_V2 = bytes.fromhex("0200001c20008cc2003f3f0006020f644e0706a4")


def test_v2_golden_vector_encode_is_byte_exact():
    raw = encode(
        7200, 140, -62, 0, wifi_up=True, lora_up=True, tcp_backbone_up=True,
        local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
        board_id=0x3F, fw=(0, 6, 2), battery_mv=3940, battery_pct=78,
        on_battery=True, charging=True, on_solar=True, lora_snr_db=6,
        lora_rssi_dbm=-92)
    assert raw == GOLDEN_V2


def test_v2_golden_vector_decodes():
    b = decode(GOLDEN_V2)
    assert b.format_version == 2
    assert b.battery_mv == 3940 and b.battery_pct == 78
    assert (b.on_battery, b.charging, b.on_solar, b.on_mains) == (True, True, True, False)
    assert b.lora_snr_db == 6 and b.lora_rssi_dbm == -92


def test_v2_payload_is_20_bytes_and_versioned():
    raw = sample_v2()
    assert len(raw) == PAYLOAD_LEN_V2 == 20
    assert raw[0] == FORMAT_VERSION_V2


def test_v2_round_trips_all_fields():
    b = decode(sample_v2())
    assert b.battery_mv == 3940
    assert b.battery_pct == 78
    assert (b.on_battery, b.charging, b.on_solar, b.on_mains) == (
        True, True, True, False)
    assert b.lora_snr_db == 6
    assert b.lora_rssi_dbm == -92
    assert b.to_bytes() == sample_v2()


def test_v2_helpers_and_labels():
    b = decode(sample_v2())
    assert b.has_power_telemetry is True
    assert b.has_link_telemetry is True
    assert "78%" in b.battery_label and "3.94 V" in b.battery_label
    assert "solar" in b.power_source_label and "charging" in b.power_source_label


def test_v1_beacon_has_no_power_or_link_telemetry():
    b = decode(sample_bytes())
    assert b.has_power_telemetry is False
    assert b.has_link_telemetry is False
    assert b.battery_pct is None and b.lora_snr_db is None
    assert b.battery_label == "not reported"


def test_v1_tool_reads_v2_shared_prefix():
    # A v2 payload must still yield correct v1 fields (interop with old tools).
    b = decode(sample_v2(uptime_s=12345, board_id=0x3F))
    assert b.uptime_s == 12345
    assert b.board_label == "Heltec32 V4"
    assert b.lora_up is True


def test_encode_promotes_to_v2_when_battery_given():
    # supplying any power/link field auto-selects the v2 wire format
    raw = encode(
        uptime_s=1, heap_kb=100, wifi_rssi_dbm=-50, reset_reason=0,
        wifi_up=True, lora_up=True, tcp_backbone_up=True,
        local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
        board_id=0x3F, fw=(0, 7, 0), battery_pct=55)
    assert len(raw) == 20 and raw[0] == FORMAT_VERSION_V2
    assert decode(raw).battery_pct == 55


def test_unknown_battery_and_link_sentinels_decode_to_none():
    # a v2 node that can't read battery/link emits sentinels -> None on decode
    raw = encode(
        uptime_s=1, heap_kb=100, wifi_rssi_dbm=-50, reset_reason=0,
        wifi_up=True, lora_up=True, tcp_backbone_up=True,
        local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
        board_id=0x3F, fw=(0, 7, 0), format_version=FORMAT_VERSION_V2)
    b = decode(raw)
    assert len(raw) == 20  # tail present...
    assert b.battery_mv is None and b.battery_pct is None  # ...but unknown
    assert b.lora_snr_db is None and b.lora_rssi_dbm is None


# battery status contribution


def test_critical_discharging_battery_is_alert():
    b = decode(sample_v2(battery_pct=8, charging=False, on_mains=False,
                         on_battery=True, on_solar=False))
    assert battery_reason(b) == "alert"
    assert beacon_status(b) == "alert"


def test_low_discharging_battery_is_warn():
    b = decode(sample_v2(battery_pct=22, charging=False, on_mains=False,
                         on_battery=True, on_solar=False))
    assert battery_reason(b) == "warn"
    assert beacon_status(b) == "warn"


def test_low_but_charging_battery_does_not_flag():
    # a solar node dipping while it charges is recovering, not in trouble
    b = decode(sample_v2(battery_pct=8, charging=True, on_solar=True,
                         on_battery=True))
    assert battery_reason(b) is None
    assert beacon_status(b) == "ok"


def test_low_on_mains_battery_does_not_flag():
    b = decode(sample_v2(battery_pct=5, charging=False, on_mains=True,
                         on_battery=False, on_solar=False))
    assert battery_reason(b) is None


def test_healthy_battery_is_ok():
    b = decode(sample_v2(battery_pct=88))
    assert battery_reason(b) is None


# link status contribution


def test_marginal_lora_link_is_warn():
    b = decode(sample_v2(lora_snr_db=-11))
    assert link_reason(b) == "warn"
    assert beacon_status(b) == "warn"


def test_good_lora_link_does_not_flag():
    b = decode(sample_v2(lora_snr_db=7))
    assert link_reason(b) is None


# --- decode must refuse payloads that are not beacons -----------------------

def test_decode_rejects_an_lxmf_announce_masquerading_as_a_beacon():
    """The VITALS listener hears EVERY announce on the mesh, and decode()
    accepted anything >= 14 bytes — so an ordinary phone's LXMF announce
    (msgpack: [display_name, stamp_cost]) parsed as a health beacon and
    landed on VITALS as a red-alerting phantom node (break-lens agent,
    2026-08-13; the operator's screen showed the (unnamed) red rows live).
    The codec's OWN first byte is a format version; unknown versions are not
    beacons."""
    import pytest
    # msgpack fixarray ["Alice's Phone", 8] — first byte 0x92, not a version
    lxmf = bytes([0x92, 0xAD]) + b"Alice's Phone" + bytes([0x08])
    assert len(lxmf) >= 14
    with pytest.raises(ValueError):
        decode(lxmf)


def test_decode_rejects_a_plain_text_name():
    import pytest
    with pytest.raises(ValueError):
        decode(b"PropagationNode-42")        # 'P' = 0x50: not a version byte


def test_decode_still_accepts_future_prefix_compatible_versions():
    """Forward-compat stays: a v3 beacon (version byte 3) must still decode
    its v1/v2 prefix — the ceiling exists to reject text and msgpack, not
    the future."""
    v2 = bytes.fromhex("010000002400c7cc053b3f000602")  # golden v1 vector
    b = decode(v2)
    assert b.format_version == 1
    v3ish = bytes([0x03]) + v2[1:] + b"\x00\x00\x00\x00\x00\x00\x00extra"
    assert decode(v3ish).format_version == 3


# -- v3 position tail (2026-08-27: the self-locating node) -------------------

def test_v3_position_round_trip():
    from monitor.health_beacon import decode, encode
    raw = encode(60, 100, 0, 0, wifi_up=False, lora_up=True,
                 tcp_backbone_up=False, local_tcp_server_up=False,
                 wdt_armed=False, psram=False, fault=False, board_id=0x3C,
                 lat=-37.810123, lng=144.962555, position_sats=9)
    assert len(raw) == 29 and raw[0] == 0x03
    b = decode(raw)
    assert b.has_position
    assert abs(b.lat - -37.810123) < 1e-6
    assert abs(b.lng - 144.962555) < 1e-6
    assert b.position_sats == 9 and b.position_fuzzed is False


def test_v3_no_fix_is_honestly_absent():
    # "GPS fitted, hunting sky" — a v3 beacon with the sentinel reports
    # NO position, never 0,0 or a stale place.
    from monitor.health_beacon import decode, encode, FORMAT_VERSION_V3
    raw = encode(60, 100, 0, 0, wifi_up=False, lora_up=True,
                 tcp_backbone_up=False, local_tcp_server_up=False,
                 wdt_armed=False, psram=False, fault=False, board_id=0x3C,
                 format_version=FORMAT_VERSION_V3)
    assert len(raw) == 29 and raw[0] == 0x03
    b = decode(raw)
    assert not b.has_position and b.lat is None and b.lng is None


def test_v3_fuzzed_flag_survives_and_garbage_never_pins():
    from monitor.health_beacon import decode, encode
    raw = encode(60, 100, 0, 0, wifi_up=False, lora_up=True,
                 tcp_backbone_up=False, local_tcp_server_up=False,
                 wdt_armed=False, psram=False, fault=False, board_id=0x3C,
                 lat=1.0, lng=2.0, position_fuzzed=True)
    assert decode(raw).position_fuzzed is True
    # flags bit set but coordinates out of range -> refused
    bad = bytearray(raw)
    bad[20:24] = (95_000_000).to_bytes(4, "big", signed=True)
    assert not decode(bytes(bad)).has_position


def test_v2_reader_still_reads_a_v3_beacon_prefix():
    # forward compatibility: the v2/v1 fields decode identically from a v3
    # payload (append-only contract).
    from monitor.health_beacon import decode, encode
    raw = encode(120, 88, 0, 3, wifi_up=False, lora_up=True,
                 tcp_backbone_up=False, local_tcp_server_up=False,
                 wdt_armed=True, psram=False, fault=False, board_id=0x3C,
                 battery_mv=3900, battery_pct=78, lat=1.5, lng=2.5)
    b = decode(raw[:20])                   # a v2 tool's view: 20 bytes
    assert b.battery_pct == 78 and b.lat is None
    full = decode(raw)
    assert full.battery_pct == 78 and full.has_position


def _h(prefix_hex):
    """A 16-byte destination hash beginning with these 4 hex digits."""
    return bytes.fromhex(prefix_hex) + b"\x00" * 12


_BEACON_KW = dict(uptime_s=120, heap_kb=44, wifi_rssi_dbm=-60, reset_reason=1,
                  wifi_up=True, lora_up=True, tcp_backbone_up=False,
                  local_tcp_server_up=False, wdt_armed=True, psram=True,
                  fault=False, board_id=9, airtime_lock=False, fw=(1, 2, 3))


def _v3(**over):
    kw = dict(_BEACON_KW); kw.update(over)
    return hb.encode(format_version=hb.FORMAT_VERSION_V3, **kw)


def _v4(**over):
    kw = dict(_BEACON_KW); kw.update(over)
    return hb.encode(format_version=hb.FORMAT_VERSION_V4, **kw)


# --- v4: who this node can hear (2026-09-29) --------------------------------
# The map could only ever draw what the MEDIC hears: every edge in its topology
# starts at the medic, because the medic's path table is its only evidence. Two
# nodes that talk to each other all day were drawn as two unrelated dots.

def test_a_v3_reader_still_reads_a_v4_beacon():
    """The whole point of the length-gated tails: an older tool must keep
    everything it understood. A v4 beacon carries position exactly as before."""
    b = hb.decode(_v4(lat=-37.7, lng=145.0, neighbours=[(_h("5a110011"), 9, 45)]))
    assert (b.lat, b.lng) == (-37.7, 145.0)
    assert b.uptime_s == 120 and b.board_id == 9


def test_a_v3_beacon_reports_no_neighbours_rather_than_none_heard():
    """Empty, because it was never asked. A node that has never reported
    neighbours must not be drawn as isolated — silence is not evidence."""
    assert hb.decode(_v3()).neighbours == []


def test_a_v4_node_that_hears_nobody_says_so():
    """Distinct from the case above, and the count byte is what distinguishes
    them: this node WAS asked and heard nothing."""
    data = _v4(neighbours=[])
    assert len(data) == hb.PAYLOAD_LEN_V4_HEAD
    assert hb.decode(data).neighbours == []


def test_the_tail_is_cheap_enough_for_lora():
    """Airtime is the scarcest thing we spend. Six neighbours is the cap and
    the whole tail must stay inside 25 bytes."""
    many = [(_h("%08x" % (0x11110000 + i)), 5, 30) for i in range(20)]
    data = _v4(neighbours=many)
    assert len(data) - hb.PAYLOAD_LEN_V3 <= 1 + hb.NEIGHBOURS_MAX * hb.NEIGHBOUR_LEN
    assert len(hb.decode(data).neighbours) == hb.NEIGHBOURS_MAX


def test_the_freshest_neighbours_are_the_ones_that_get_the_airtime():
    """When a node hears more than it can afford to report, the ones worth the
    bytes are the ones it heard recently — then the ones it heard well."""
    entries = [(_h("aaaa0000"), 10, 40000),      # strong but stale
               (_h("bbbb0000"), -5, 30),         # fresh, weak
               (_h("cccc0000"), 9, 30)]          # fresh and strong
    got = hb.decode(_v4(neighbours=entries)).neighbours
    assert [n["short_hash"] for n in got][:2] == [0xcccc, 0xbbbb]


def test_a_neighbour_with_no_usable_hash_is_dropped_not_zeroed():
    """A zero short hash would match a real node. Sending nothing is the only
    safe way to say 'I could not identify this one'."""
    got = hb.decode(_v4(neighbours=[(None, 5, 30), (_h("5a110011"), 5, 30)])).neighbours
    assert [n["short_hash"] for n in got] == [0xc627]


def test_a_stale_neighbour_is_not_reported_at_all():
    """Past the last bucket there is nothing useful to say, and a link the node
    last heard three days ago is not a link to draw today."""
    assert hb.decode(_v4(neighbours=[(_h("5a110011"), 5, 10 * 86400)])).neighbours == []


def test_the_age_is_an_upper_bound_never_a_midpoint():
    """The honest reading of a bucket is 'no older than X'. Inventing a
    midpoint would be a precision the node never sent."""
    assert hb.bucket_seconds(0) == hb.AGE_BUCKETS_S[0]
    assert hb.bucket_seconds(hb.AGE_BUCKET_STALE) is None
    assert hb.bucket_seconds(-1) is None
    assert hb.age_bucket(None) == hb.AGE_BUCKET_STALE
    assert hb.age_bucket(-5) == hb.AGE_BUCKET_STALE, "a backwards clock is not freshness"


def test_a_truncated_v4_tail_keeps_the_entries_that_arrived():
    """A beacon claiming six neighbours and carrying two is damaged, not a
    reason to lose the two."""
    full = _v4(neighbours=[(_h("aaaa0000"), 5, 30), (_h("bbbb0000"), 5, 30),
                           (_h("cccc0000"), 5, 30)])
    chopped = full[:-hb.NEIGHBOUR_LEN]            # the third entry never arrives
    got = hb.decode(chopped).neighbours
    assert len(got) == 2
    assert {n["short_hash"] for n in got} == {0xaaaa, 0xbbbb}
