"""Birth-vs-adopt classification — pure, no hardware (CI has no board/Kivy)."""

from monitor.node_classifier import (classify, parse_banner, parse_status,
                                      params_match, CANONICAL)

# Real serial banner captured off FAITH (an already-provisioned RTNode-2400 V4).
FAITH_BANNER = """
[Boundary] Loaded LoRa config from EEPROM
[Boundary] LoRa: freq=915125000 bw=125000 sf=9 cr=5 txp=17
Starting RNS...
[INF] Transport mode is enabled
[HealthBeacon] init dst=5a0b000b000000000000000000000006, first announce in ~30s
[Boundary] Boot stable
"""

FAITH_STATUS = ('{"fork":"RTNode","fw_version":"0.6.2","board_model":63,'
                '"board":"heltec_v4","lora_online":true,"wifi_ip":"192.168.1.51",'
                '"node_name":"FAITH RTnode","faults":[]}')


def test_canonical_is_the_contract():
    assert CANONICAL == {"freq": 915125000, "bw": 125000, "sf": 9, "cr": 5, "txp": 17}


def test_faith_is_an_adopt_candidate():
    v = classify(FAITH_BANNER, FAITH_STATUS)
    assert v["kind"] == "adopt"
    assert v["is_ours"] and v["beaconing"] and v["params_ok"]
    assert v["identity_hash"] == "5a0b000b000000000000000000000006"
    assert v["node_name"] == "FAITH RTnode"
    assert v["firmware"] == "0.6.2" and v["board"] == "heltec_v4"


def test_banner_alone_is_enough_to_adopt():
    # no /status (node not on WiFi) — banner carries identity + params
    v = classify(FAITH_BANNER, None)
    assert v["kind"] == "adopt"
    assert v["identity_hash"] == "5a0b000b000000000000000000000006"
    assert v["node_name"] is None            # name only comes from /status here


def test_blank_board_is_a_birth():
    v = classify("", None)
    assert v["kind"] == "birth"
    assert not v["is_ours"] and not v["beaconing"]


def test_foreign_firmware_is_a_birth():
    v = classify("U-Boot 2021\nsome bootloader\nlogin:", None)
    assert v["kind"] == "birth"
    assert not v["is_ours"]


def test_our_firmware_but_wrong_params_is_a_birth():
    off = FAITH_BANNER.replace("freq=915125000", "freq=868000000")
    v = classify(off, FAITH_STATUS)
    assert v["kind"] == "birth"
    assert v["is_ours"] and v["beaconing"] and not v["params_ok"]
    assert "off canonical" in v["reason"].lower()


def test_our_firmware_not_yet_beaconing_is_a_birth():
    # our stack booting but no HealthBeacon line yet (still in setup)
    banner = "[Boundary] LoRa: freq=915125000 bw=125000 sf=9 cr=5 txp=17\nStarting RNS..."
    v = classify(banner, None)
    assert v["kind"] == "birth"
    assert v["is_ours"] and not v["beaconing"]


def test_params_match_needs_all_fields():
    assert params_match(dict(CANONICAL))
    partial = dict(CANONICAL); partial.pop("txp")
    assert not params_match(partial)
    assert not params_match(None)


def test_parse_banner_and_status_helpers():
    b = parse_banner(FAITH_BANNER)
    assert b["params"]["sf"] == 9 and b["identity_hash"].startswith("5a0b000b")
    s = parse_status(FAITH_STATUS)
    assert s["node_name"] == "FAITH RTnode" and s["is_ours"]
    assert parse_status("not json") == {}
    assert parse_status(None) == {}
