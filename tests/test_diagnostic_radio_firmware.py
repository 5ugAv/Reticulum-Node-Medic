import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.radio_firmware import RadioFirmwareCheck, LATEST_FIRMWARE

# Verbatim shape of `rnodeconf <port> --info` captured from a real RNode
# (Heltec LoRa32, firmware 1.86). Labels are column-aligned with a SPACE before
# the colon, and there are decoy "Frequency range" / "Max TX power" header lines
# the parsers must skip. Values here match NodeProfile() defaults.
GOOD_INFO = "\n".join([
    f"Current firmware version: {LATEST_FIRMWARE}",
    "Device info:",
    "\tProduct            : RNode",
    "\tDevice signature   : Verified",
    f"\tFirmware version   : {LATEST_FIRMWARE}",
    "\tHardware revision  : 1",
    "\tSerial number      : 00:00:00:1c",
    "\tModem chip         : SX1262",
    "\tFrequency range    : 860.0 MHz - 930.0 MHz",
    "\tMax TX power       : 28 dBm",
    "\tDevice mode        : TNC",
    "\t  Frequency        : 915.125 MHz",
    "\t  Bandwidth        : 125.0 KHz",
    "\t  TX power         : 17 dBm (50.119 mW)",
    "\t  Spreading factor : 9",
    "\t  Coding rate      : 5",
    "\t  On-air bitrate   : 1.07 kbps",
])


def conn_with(info=GOOD_INFO, info_code=0, loop_code=0):
    c = EmulatedConnection()
    c.rule("--info", code=info_code, stdout=info)
    c.rule("--loop", code=loop_code, stdout="LOOP OK" if loop_code == 0 else "")
    c.rule("^systemctl is-active ModemManager", code=3, stdout="inactive")
    c.rule("rnodeconf", code=0, stdout="ok")  # catch-all
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


def test_live_mode_defers_instead_of_false_criticals():
    # rnsd is running the RNode (holds the port) -> rnodeconf can't read it.
    # Must NOT false-report "no firmware"; reports one info and defers to
    # Network & mesh. Verified against nodemedic's live rnsd.
    conn = conn_with(info_code=1, info="")
    conn.rules.insert(0, ("^systemctl is-active rnsd", 0, "active", ""))
    conn.rules.insert(0, ("rnstatus --json", 0,
        '{"interfaces":[{"type":"RNodeInterface","name":"RNode","status":false}]}',
        ""))
    n = names(run(conn))
    assert n == {"radio_in_service"}
    assert "firmware_present" not in n
    assert "serial_responsive" not in n


def test_maintenance_mode_dead_board_still_flags():
    # rnsd NOT running -> a truly unresponsive board still reports the real fault
    conn = conn_with(info_code=1, info="")
    conn.rules.insert(0, ("^systemctl is-active rnsd", 3, "inactive", ""))
    n = names(run(conn))
    assert "serial_responsive" in n
    assert "radio_in_service" not in n


def test_firmware_not_present():
    info = "[Device] RNode\nno version line here"
    assert "firmware_present" in names(run(conn_with(info=info)))


def test_eeprom_invalid_flagged_firmware_still_present():
    # a REAL faulty board (verified live): firmware 1.86 present but EEPROM
    # invalid. The specific fault must be flagged; firmware_present must NOT
    # false-fire (the "Current firmware version" line proves firmware is there).
    info = ("Device connected\nCurrent firmware version: 1.86\n"
            "Reading EEPROM...\nEEPROM is invalid, no further information available")
    n = names(run(conn_with(info=info)))
    assert "eeprom_valid" in n
    assert "firmware_present" not in n


def test_firmware_hash_mismatch_flagged_corrupt():
    # A board can have a valid EEPROM + validated signature yet show "firmware
    # corrupt": the stored firmware hash != the running firmware. --info hides it;
    # PROBE reads both hashes via fw_hash_probe and flags it CRITICAL + auto-fix.
    conn = conn_with()
    conn.rule("fw_hash_probe", code=0, stdout="FWHASH:MISMATCH aaaa bbbb")
    issues = run(conn)
    hv = next(i for i in issues if i.check_name == "firmware_hash_valid")
    assert hv.severity == "critical" and hv.auto_fixable
    assert "corrupt" in hv.description.lower()


def test_firmware_hash_match_not_flagged():
    conn = conn_with()
    conn.rule("fw_hash_probe", code=0, stdout="FWHASH:MATCH")
    assert "firmware_hash_valid" not in names(run(conn))


def test_firmware_hash_unknown_fails_safe():
    # Can't read the hashes (e.g. probe error) -> never flag a fault we can't
    # confirm. Default conn returns no FWHASH line -> "unknown" -> no issue.
    assert "firmware_hash_valid" not in names(run(conn_with()))


def test_firmware_version_outdated():
    info = GOOD_INFO.replace(
        f"Firmware version   : {LATEST_FIRMWARE}", "Firmware version   : 1.10"
    )
    assert "firmware_version_current" in names(run(conn_with(info=info)))


def test_frequency_mismatch():
    info = GOOD_INFO.replace("915.125 MHz", "868.0 MHz")
    assert "frequency" in names(run(conn_with(info=info)))


def test_bandwidth_mismatch():
    info = GOOD_INFO.replace("125.0 KHz", "250.0 KHz")
    assert "bandwidth" in names(run(conn_with(info=info)))


def test_tx_power_mismatch():
    # must change the per-mode "TX power", not the "Max TX power" header
    info = GOOD_INFO.replace("17 dBm (50.119 mW)", "22 dBm (50.119 mW)")
    assert "tx_power" in names(run(conn_with(info=info)))


def test_spreading_factor_mismatch():
    info = GOOD_INFO.replace("Spreading factor : 9", "Spreading factor : 7")
    assert "spreading_factor" in names(run(conn_with(info=info)))


def test_coding_rate_mismatch():
    info = GOOD_INFO.replace("Coding rate      : 5", "Coding rate      : 8")
    assert "coding_rate" in names(run(conn_with(info=info)))


def test_radio_loopback_fails():
    # rnodeconf has no --loop; L1 fails when the board returns no info at all
    assert "radio_loopback" in names(run(conn_with(info="")))


def test_tx_power_ignores_max_tx_power_header():
    # the "Max TX power : 28 dBm" header must NOT be read as the configured
    # TX power — with the per-mode value still 17 there is no mismatch.
    info = GOOD_INFO.replace("Max TX power       : 28 dBm",
                             "Max TX power       : 30 dBm")
    assert "tx_power" not in names(run(conn_with(info=info)))


def test_frequency_ignores_range_header():
    # the "Frequency range : 860.0 MHz - 930.0 MHz" header must NOT be read as
    # the configured frequency; changing the range alone is not a mismatch.
    info = GOOD_INFO.replace("Frequency range    : 860.0 MHz - 930.0 MHz",
                             "Frequency range    : 410.0 MHz - 525.0 MHz")
    assert "frequency" not in names(run(conn_with(info=info)))


def test_all_broken_reports_core_faults():
    # A fully unresponsive board: the firmware-hash-mismatch check is correctly
    # SKIPPED (it needs a responsive, provisioned board), so it isn't listed here.
    conn = EmulatedConnection(default_code=1, default_stdout="")
    issues = run(conn)
    core = {
        "serial_responsive", "firmware_present",
        "firmware_version_current", "frequency", "bandwidth",
        "spreading_factor", "coding_rate", "tx_power", "radio_loopback",
    }
    assert core <= names(issues)
    assert "firmware_hash_valid" not in names(issues)   # guarded off when no info


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
    info = GOOD_INFO + "\n\tSerial baud rate: 9600"
    assert "heltec_baud" in names(run(conn_with(info=info)))


def test_serial_data_capable_charge_only_cable():
    # the serial device node exists but the board returns no --info data
    conn = conn_with(info="")
    conn.rules.insert(0, ("^test -c", 0, "", ""))
    assert "serial_data_capable" in names(run(conn))


def test_antenna_rssi_anomalous_noise_floor():
    # no rnstatus rule here, so the check falls back to an info noise-floor line
    info = GOOD_INFO + "\n\tNoise floor : -20 dBm"
    assert "antenna_rssi" in names(run(conn_with(info=info)))


def test_heltec_v4_reminders_fire_for_v4_profile():
    from node_profile import NodeHardware
    p = NodeProfile()
    p.hardware = NodeHardware.HELTEC_V4
    from diagnostics.radio_firmware import RadioFirmwareCheck
    # info without hardware revision -> both 60 and 88 fire
    info = GOOD_INFO.replace("\tHardware revision  : 1\n", "")
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


def _eeprom_issue():
    from diagnostics.base import Issue
    return Issue(check_name="eeprom_valid", category="Radio & firmware",
                 description="", severity="critical", auto_fixable=True)


def test_fix_eeprom_v4_reflashes_neopixel_firmware():
    # A Heltec V4 with an invalid EEPROM is repaired by the full RGB reflash:
    # reprovision the EEPROM (autoinstall) AND restore the NeoPixel firmware
    # (esptool + firmware-hash) — never left on stock firmware.
    from node_profile import NodeHardware
    from workflows.rnode_v4_rgb import FIRMWARE_DIR, BUILD_BIN
    p = NodeProfile()
    p.hardware = NodeHardware.HELTEC_V4
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rule(f"test -d {FIRMWARE_DIR}", code=0)          # already cloned
    conn.rule(f"test -f {BUILD_BIN}", code=0)             # firmware built
    conn.rule("erase_flash", code=0, stdout="Chip erase completed successfully")
    conn.rule("write_flash", code=0, stdout="Hash of data verified.")
    conn.rule("-r --product", code=0,
              stdout="Device signature validated\nEEPROM Bootstrapping successful!")
    conn.rule('-H "$HASH"', code=0, stdout="Firmware hash set")
    conn.rule("--info", code=0, stdout=GOOD_INFO)
    fix = RadioFirmwareCheck(conn, p).fix(_eeprom_issue())
    assert fix.success is True
    # full RGB image written (incl the app at 0x10000) + vendor V4 provision
    assert any("write_flash" in c and "0x10000" in c for c in conn.history)
    assert any("-r --product c3" in c and "--model c8" in c for c in conn.history)


def test_fix_eeprom_non_v4_uses_autoinstall_only():
    # Any other RNode keeps its stock firmware — reprovision via autoinstall,
    # no esptool overlay.
    conn = conn_with()
    conn.rule("rnodeconf /dev/ttyUSB0 --autoinstall", code=0, stdout="ok")
    fix = RadioFirmwareCheck(conn, NodeProfile()).fix(_eeprom_issue())
    assert fix.success is True
    assert any("--autoinstall" in c for c in conn.history)
    assert not any("esptool" in c for c in conn.history)


def test_fix_eeprom_failure_surfaces_recovery_ladder():
    # When autoinstall can't reflash, PROBE must hand the operator the recovery
    # steps (BOOT+RST, good cable, flash-on-medic), not a dead-end error.
    from diagnostics.radio_firmware import FLASH_RECOVERY
    conn = conn_with()
    conn.rules.insert(0, ("--autoinstall", 1,
                          "", "Serial data stream stopped: Possible serial noise"))
    fix = RadioFirmwareCheck(conn, NodeProfile()).fix(_eeprom_issue())
    assert fix.success is False
    assert FLASH_RECOVERY in fix.message
    for cue in ("BOOT", "cable", "Node Medic"):
        assert cue in fix.message


def test_fix_frequency_runs_rnodeconf():
    info = GOOD_INFO.replace("915.125 MHz", "868.0 MHz")
    conn = conn_with(info=info)
    check = RadioFirmwareCheck(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "frequency")
    fix = check.fix(issue)
    assert fix.success is True
    assert any("rnodeconf" in c for c in conn.history)
