import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.radio_firmware import RadioFirmwareCheck, LATEST_FIRMWARE

GOOD_INFO = "\n".join([
    "[Device] RNode",
    f"Firmware version: {LATEST_FIRMWARE}",
    "Firmware hash: 0badc0ffee",
    "Frequency: 915.125 MHz",
    "Bandwidth: 125.0 KHz",
    "TX power: 17 dBm",
    "Spreading factor: 9",
    "Coding rate: 5",
    "Serial baud rate: 115200",
    "Noise floor: -95 dBm",
])


def conn_with(info=GOOD_INFO, info_code=0, loop_code=0):
    c = EmulatedConnection()
    c.rule("--info", code=info_code, stdout=info)
    c.rule("--loop", code=loop_code, stdout="LOOP OK" if loop_code == 0 else "")
    c.rule("^systemctl is-active ModemManager", code=3, stdout="inactive")
    c.rule("rnodeconf", code=0, stdout="ok")  # catch-all (covers --version etc.)
    return c


def run(conn):
    return RadioFirmwareCheck(conn, NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def test_category_name():
    assert RadioFirmwareCheck(conn_with(), NodeProfile()).category_name == (
        "Radio & firmware"
    )


def test_all_healthy_no_issues():
    assert run(conn_with()) == []


def test_serial_not_responsive():
    issues = run(conn_with(info_code=1, info=""))
    assert "serial_responsive" in names(issues)


def test_firmware_not_present():
    info = "[Device] RNode\nno version line here"
    assert "firmware_present" in names(run(conn_with(info=info)))


def test_firmware_hash_not_set():
    info = GOOD_INFO.replace("Firmware hash: 0badc0ffee", "")
    assert "firmware_hash_set" in names(run(conn_with(info=info)))


def test_firmware_version_outdated():
    info = GOOD_INFO.replace(
        f"Firmware version: {LATEST_FIRMWARE}", "Firmware version: 1.10"
    )
    assert "firmware_version_current" in names(run(conn_with(info=info)))


def test_frequency_mismatch():
    info = GOOD_INFO.replace("915.125 MHz", "868.0 MHz")
    assert "frequency" in names(run(conn_with(info=info)))


def test_bandwidth_mismatch():
    info = GOOD_INFO.replace("125.0 KHz", "250.0 KHz")
    assert "bandwidth" in names(run(conn_with(info=info)))


def test_tx_power_mismatch():
    info = GOOD_INFO.replace("TX power: 17 dBm", "TX power: 22 dBm")
    assert "tx_power" in names(run(conn_with(info=info)))


def test_spreading_factor_mismatch():
    info = GOOD_INFO.replace("Spreading factor: 9", "Spreading factor: 7")
    assert "spreading_factor" in names(run(conn_with(info=info)))


def test_coding_rate_mismatch():
    info = GOOD_INFO.replace("Coding rate: 5", "Coding rate: 8")
    assert "coding_rate" in names(run(conn_with(info=info)))


def test_radio_loopback_fails():
    assert "radio_loopback" in names(run(conn_with(loop_code=1)))


def test_all_broken_reports_original_ten():
    conn = EmulatedConnection(default_code=1, default_stdout="")
    issues = run(conn)
    original = {
        "serial_responsive", "firmware_present", "firmware_hash_set",
        "firmware_version_current", "frequency", "bandwidth",
        "spreading_factor", "coding_rate", "tx_power", "radio_loopback",
    }
    assert original <= names(issues)


# ---- extended checks 57-60, 86-88 ----------------------------------------


def test_flow_control_atmega_flagged():
    info = GOOD_INFO + "\nPlatform: ATmega1284p"
    assert "flow_control_atmega" in names(run(conn_with(info=info)))


def test_flow_control_ok_when_atmega_flow_enabled():
    info = GOOD_INFO + "\nPlatform: ATmega1284p\nFlow control: enabled"
    assert "flow_control_atmega" not in names(run(conn_with(info=info)))


def test_modemmanager_interference_critical():
    conn = conn_with()
    conn.rules.insert(0, ("^systemctl is-active ModemManager", 0, "active", ""))
    issues = run(conn)
    assert "modemmanager_interference" in names(issues)
    assert next(i for i in issues
                if i.check_name == "modemmanager_interference").severity == "critical"


def test_heltec_baud_mismatch():
    info = GOOD_INFO.replace("Serial baud rate: 115200", "Serial baud rate: 9600")
    assert "heltec_baud" in names(run(conn_with(info=info)))


def test_serial_data_capable_charge_only_cable():
    conn = conn_with()
    # port opens (--info works) but --version returns nothing => charge-only
    conn.rules.insert(0, ("--version", 1, "", ""))
    assert "serial_data_capable" in names(run(conn))


def test_antenna_rssi_anomalous_noise_floor():
    info = GOOD_INFO.replace("Noise floor: -95 dBm", "Noise floor: -20 dBm")
    assert "antenna_rssi" in names(run(conn_with(info=info)))


def test_heltec_v4_reminders_fire_for_v4_profile():
    from node_profile import NodeHardware
    p = NodeProfile()
    p.hardware = NodeHardware.HELTEC_V4
    from diagnostics.radio_firmware import RadioFirmwareCheck
    # info without hardware revision -> both 60 and 88 fire
    info = GOOD_INFO
    conn = conn_with(info=info)
    issues = RadioFirmwareCheck(conn, p).run()
    n = {i.check_name for i in issues}
    assert "heltec_v4_dual_antenna" in n
    assert "heltec_hw_revision" in n


def test_fix_modemmanager_masks_service():
    conn = conn_with()
    conn.rules.insert(0, ("^systemctl is-active ModemManager", 0, "active", ""))
    conn.rule("systemctl mask ModemManager", code=0, stdout="")
    from diagnostics.radio_firmware import RadioFirmwareCheck
    check = RadioFirmwareCheck(conn, NodeProfile())
    issue = next(i for i in check.run()
                 if i.check_name == "modemmanager_interference")
    fix = check.fix(issue)
    assert fix.success is True
    assert any("mask ModemManager" in c for c in conn.history)


def test_fix_frequency_runs_rnodeconf():
    info = GOOD_INFO.replace("915.125 MHz", "868.0 MHz")
    conn = conn_with(info=info)
    check = RadioFirmwareCheck(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "frequency")
    fix = check.fix(issue)
    assert fix.success is True
    assert any("rnodeconf" in c for c in conn.history)
