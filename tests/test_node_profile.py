from datetime import datetime

import node_profile as np
from node_profile import (
    NodeHardware,
    ConnectionMethod,
    NodeRole,
    RadioConfig,
    NodeProfile,
)


def test_node_hardware_values():
    assert NodeHardware.PI_3A_PLUS.value == "Raspberry Pi 3A+"
    assert NodeHardware.PI_ZERO_2W.value == "Raspberry Pi Zero 2W"
    assert NodeHardware.PI_5.value == "Raspberry Pi 5"
    assert NodeHardware.HELTEC_V4.value == "Heltec LoRa32 V4"
    assert NodeHardware.UNKNOWN.value == "Unknown"


def test_connection_method_values():
    assert ConnectionMethod.USB_SERIAL.value == "USB-C serial"
    assert ConnectionMethod.DIRECT_SERIAL.value == "Direct serial cable"
    assert ConnectionMethod.SSH.value == "SSH over network"
    assert ConnectionMethod.NONE.value == "Not connected"


def test_node_role_values():
    assert NodeRole.TRANSPORT.value == "Transport node"
    assert NodeRole.GATEWAY.value == "Gateway node"
    assert NodeRole.MESHTASTIC_BRIDGE.value == "Meshtastic bridge node"
    assert NodeRole.UNKNOWN.value == "Unknown"


def test_radio_config_australian_defaults():
    r = RadioConfig()
    assert r.frequency_mhz == 915.125
    assert r.bandwidth_khz == 125.0
    assert r.spreading_factor == 9
    assert r.coding_rate == 5
    assert r.tx_power_dbm == 17
    assert r.serial_port == "/dev/ttyUSB0"
    assert r.firmware_version is None
    assert r.firmware_hash_set is False


def test_radio_config_overridable():
    r = RadioConfig(frequency_mhz=868.0, tx_power_dbm=14)
    assert r.frequency_mhz == 868.0
    assert r.tx_power_dbm == 14
    # untouched fields keep defaults
    assert r.spreading_factor == 9


def test_node_profile_defaults():
    p = NodeProfile()
    assert p.hardware is NodeHardware.UNKNOWN
    assert p.role is NodeRole.UNKNOWN
    assert p.connection is ConnectionMethod.NONE
    assert p.ssh_user == "pi"
    assert isinstance(p.radio, RadioConfig)
    assert p.radio.frequency_mhz == 915.125
    assert p.has_rnode is False


def test_node_profile_flag_fields_default_false():
    p = NodeProfile()
    for flag in (
        "has_solar_controller",
        "has_battery_bank",
        "has_cooling_fan",
        "has_rtc_module",
        "has_meshtastic_bridge",
        "has_meshchat_client",
        "has_sideband_client",
        "has_columba_client",
        "has_meshtastic_client",
    ):
        assert getattr(p, flag) is False, flag


def test_node_profile_list_fields_independent():
    a = NodeProfile()
    b = NodeProfile()
    a.build_steps_completed.append("detect_hardware")
    a.issues_found.append("x")
    a.fixes_applied.append("y")
    # mutable defaults must not be shared between instances
    assert b.build_steps_completed == []
    assert b.issues_found == []
    assert b.fixes_applied == []


def test_node_profile_radio_independent():
    a = NodeProfile()
    b = NodeProfile()
    a.radio.tx_power_dbm = 5
    assert b.radio.tx_power_dbm == 17


def test_node_profile_session_fields():
    p = NodeProfile()
    assert isinstance(p.session_start, datetime)
    # session_id is a timestamp string YYYYMMDD_HHMMSS
    assert isinstance(p.session_id, str)
    assert len(p.session_id) == 15
    assert p.session_id[8] == "_"


def test_node_profile_operator_notes_default_empty():
    assert NodeProfile().operator_notes == ""
