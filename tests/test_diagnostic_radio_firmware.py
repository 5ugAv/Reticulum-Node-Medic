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


# -K -L hashes for the firmware-blessing check (the 2026-08-22 T114 trap).
# Real rnodeconf prints two lines: "The target firmware hash is: <64 hex>"
# and "The actual firmware hash is: <64 hex>".
H_STORED = "ab" * 32
H_RUNNING = "cd" * 32


def kl_output(target, actual):
    return (f"The target firmware hash is: {target}\n"
            f"The actual firmware hash is: {actual}\n")


KL_BLESSED = kl_output(H_STORED, H_STORED)


def conn_with(info=GOOD_INFO, info_code=0, loop_code=0):
    c = EmulatedConnection()
    c.rule("--info", code=info_code, stdout=info)
    c.rule("--loop", code=loop_code, stdout="LOOP OK" if loop_code == 0 else "")
    c.rule("^systemctl is-active ModemManager", code=3, stdout="inactive")
    c.rule("-K -L", code=0, stdout=KL_BLESSED)  # blessed unless a test says not
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
    # A genuinely healthy board must actually REPORT its noise floor; an absent
    # reading is now "unverified", not a silent pass (see the antenna_rssi
    # honesty fix). Supply a normal quiet floor so the check truly passes.
    info = GOOD_INFO + "\n\tNoise floor : -95 dBm"
    assert run(conn_with(info=info)) == []


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
    conn.rules.insert(0, ("^fuser ", 0, "", ""))    # the TARGET port is held by rnsd
    n = names(run(conn))
    assert n == {"radio_in_service"}
    assert "firmware_present" not in n
    assert "serial_responsive" not in n


def test_live_mode_but_free_work_board_is_diagnosed_not_deferred():
    # rnsd runs (holds Jonesey) but PROBE targets a FREE work board that didn't
    # read: this is a real fault to report, NOT "radio in service". The gate must
    # be scoped to the held port, not just "rnsd is running".
    conn = conn_with(info_code=1, info="")
    conn.rules.insert(0, ("^systemctl is-active rnsd", 0, "active", ""))
    conn.rules.insert(0, ("rnstatus --json", 0,
        '{"interfaces":[{"type":"RNodeInterface","name":"RNode","status":false}]}',
        ""))
    conn.rules.insert(0, ("^fuser ", 1, "", ""))    # target work board is FREE
    n = names(run(conn))
    assert "radio_in_service" not in n
    assert "serial_responsive" in n                 # real fault surfaced


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
    # corrupt": the stored firmware hash != the running firmware. fw_hash_probe
    # is now the FALLBACK read (C2 fold): it only runs when the -K -L blessing
    # read reached no verdict, so make the blessing unreadable here.
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0, "no hashes came back", ""))
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
    # confirm. With the blessing unreadable too, the fallback probe runs, gets
    # no FWHASH line -> "unknown" -> no issue.
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0, "no hashes came back", ""))
    assert "firmware_hash_valid" not in names(run(conn))


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


def test_fix_eeprom_identified_board_uses_the_proven_birth_path():
    # A board the device names (here a Heltec V3) is reprovisioned through
    # birth_flash — NOT a bare `rnodeconf --autoinstall`, whose prompts read a
    # keypress and would wedge the port for the full timeout (2026-08-01).
    info = GOOD_INFO + "\n\tBoard              : Heltec32 V3"
    conn = conn_with(info=info)
    conn.rules.insert(0, ("--autoinstall", 0, "Autoinstallation complete", ""))
    fix = RadioFirmwareCheck(conn, NodeProfile()).fix(_eeprom_issue())
    assert fix.success is True
    assert any("--autoinstall" in c for c in conn.history)
    assert not any("esptool" in c for c in conn.history)


def test_fix_eeprom_unidentified_board_fails_honestly():
    # An unidentifiable board must NOT get a guessed image — say so instead of
    # running a command that can never succeed.
    conn = conn_with()
    fix = RadioFirmwareCheck(conn, NodeProfile()).fix(_eeprom_issue())
    assert fix.success is False
    assert "identify" in fix.message.lower()


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


# ---- firmware blessing (-K -L): the parked-radio trap (2026-08-22 T114) ----
# A board whose stored blessed hash disagrees with the firmware actually
# running is PARKED at 0.000 while BLE/KISS/rnodeconf all answer politely.
# `rnodeconf -K -L` reads both hashes; the check reports BLESSED / PARKED /
# UNREADABLE (or refuses an unverifiable port outright) and never writes.


@pytest.fixture(autouse=True)
def _cleared_work_board_gate(monkeypatch):
    """The C6 Jonesey gate consults the REAL host's roster / by-id tree, so
    left live it would make this file's results depend on whichever machine
    runs it. Cleared by default; the guard test below overrides it. Yields
    the REAL function so the fail-closed test can still exercise it."""
    import diagnostics.radio_firmware as rf
    real = rf._work_board_cleared
    monkeypatch.setattr(rf, "_work_board_cleared", lambda c, p: True)
    yield real


def kl_count(conn):
    return sum(1 for c in conn.history if "-K -L" in c)


def probe_ran(conn):
    return any("fw_hash_probe" in c for c in conn.history)


def test_blessed_hashes_match_no_issue():
    conn = conn_with()          # default -K -L rule: hashes agree
    assert "firmware_blessing" not in names(run(conn))
    # a clean first read needs no retry laps and no mismatch-confirm read
    assert kl_count(conn) == 1
    # C5: one settle beat BEFORE the first read (Device.h:142 deferred reset;
    # the --info reads just above may have rebooted a hw-CDC board)
    first_kl = next(i for i, c in enumerate(conn.history) if "-K -L" in c)
    assert any("sleep 6" in c for c in conn.history[:first_kl])


def test_blessing_verdict_suppresses_fw_hash_probe():
    # C2: one fault, one issue. When -K -L reaches a verdict (either way),
    # the fw_hash_probe fallback must not also run and double-report.
    blessed = conn_with()
    run(blessed)
    assert not probe_ran(blessed)
    parked = conn_with()
    parked.rules.insert(0, ("-K -L", 0, kl_output(H_STORED, H_RUNNING), ""))
    issues = run(parked)
    assert not probe_ran(parked)
    assert "firmware_hash_valid" not in names(issues)
    assert "firmware_blessing" in names(issues)


def test_probe_fallback_runs_only_when_blessing_unreadable():
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0, "no hashes came back", ""))
    conn.rule("fw_hash_probe", code=0, stdout="FWHASH:MATCH")
    issues = run(conn)
    assert probe_ran(conn)                       # fallback consulted
    assert "firmware_hash_valid" not in names(issues)


def test_blessing_uses_raw_port_not_by_id_symlink():
    # the rnodeconf by-id nRF52 trap: a by-id symlink can rescan to None and
    # touch the wrong device — the check must hand rnodeconf the RAW tty.
    conn = conn_with()
    conn.rules.insert(0, ("^readlink -f /dev/serial/by-id", 0,
                          "/dev/ttyACM2", ""))
    profile = NodeProfile()
    profile.radio.serial_port = (
        "/dev/serial/by-id/usb-RAKwireless_WisCore_RAK4631_Board-if00")
    RadioFirmwareCheck(conn, profile).run()
    kl = [c for c in conn.history if "-K -L" in c]
    assert kl and all("/dev/ttyACM2" in c and "by-id" not in c for c in kl)


def test_blessing_reresolves_moved_port_through_by_id_anchor():
    # C3: production hands this check a RAW ttyACM node. After a reset the
    # board can re-enumerate to a DIFFERENT number, so the check anchors on
    # the stable by-id link and readlinks it each lap — the -K -L command
    # must follow the board to its NEW raw node, and still never pass the
    # symlink itself to rnodeconf.
    conn = conn_with()
    conn.rules.insert(0, ("for l in /dev/serial/by-id/*", 0,
                          "/dev/serial/by-id/usb-Heltec_V4-if00", ""))
    conn.rules.insert(0, ("^readlink -f /dev/serial/by-id", 0,
                          "/dev/ttyACM2", ""))
    profile = NodeProfile()
    profile.radio.serial_port = "/dev/ttyACM1"      # where it USED to be
    RadioFirmwareCheck(conn, profile).run()
    kl = [c for c in conn.history if "-K -L" in c]
    assert kl and all("/dev/ttyACM2" in c and "by-id" not in c for c in kl)


def test_parked_needs_two_agreeing_reads_and_prescribes_cure():
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0, kl_output(H_STORED, H_RUNNING), ""))
    issues = run(conn)
    parked = next(i for i in issues if i.check_name == "firmware_blessing")
    # C4: a mismatch is only believed after a CONFIRMING second read
    assert kl_count(conn) == 2
    assert parked.severity == "critical"
    assert parked.auto_fixable is False          # report + prescribe only
    assert "PARKED" in parked.description
    assert "0.000" in parked.description         # the parked-radio signature
    # P2: honest about BOTH causes — the commonest is the offline birth that
    # never stamped a hash, not only "reflashed outside the medic"
    assert "--autoinstall --nocheck" in parked.description
    # the cure names the exact command with the ACTUAL hash filled in...
    assert f"--firmware-hash {H_RUNNING}" in parked.description
    # ...demands the COLD power cycle (a reset is not enough)...
    assert "COLD power cycle" in parked.description
    # ...and offers the full reflash only as the heavier fallback
    assert "BIRTH" in parked.description
    assert H_STORED in parked.raw_detail and H_RUNNING in parked.raw_detail


def test_parked_diagnosis_is_read_only():
    # Diagnosis must NEVER write EEPROM — the cure is prescribed, not applied.
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0, kl_output(H_STORED, H_RUNNING), ""))
    run(conn)
    assert not any("--firmware-hash" in c for c in conn.history)


class _FlakyKLConn(EmulatedConnection):
    """-K -L answers from a scripted sequence (a dying tty lingering after a
    reset garbles reads); everything else follows the rule list."""

    def __init__(self, kl_outputs, base=None):
        super().__init__()
        self.rules = (base or conn_with()).rules
        self._kl = list(kl_outputs)

    def run(self, command, timeout=30):
        if "-K -L" in command:
            self.history.append(command)
            return (0, self._kl.pop(0) if self._kl else "", "")
        return super().run(command, timeout)


def test_transient_mismatch_is_not_condemned():
    # C4: one post-reset read shows a mismatch, the confirm read agrees with
    # itself — the board is BLESSED, and no critical was raised on one read.
    conn = _FlakyKLConn([kl_output(H_STORED, H_RUNNING),
                         kl_output(H_STORED, H_STORED)])
    assert "firmware_blessing" not in names(run(conn))
    assert kl_count(conn) == 2


def test_unstable_confirm_reports_unreadable():
    # the two reads disagree with EACH OTHER: an unstable state is reported
    # as UNREADABLE (honest), never committed as PARKED.
    other = "ef" * 32
    conn = _FlakyKLConn([kl_output(H_STORED, H_RUNNING),
                         kl_output(H_STORED, other)])
    issues = run(conn)
    unread = next(i for i in issues if i.check_name == "firmware_blessing")
    assert unread.severity == "info"
    assert "UNREADABLE" in unread.description


def test_settle_retry_recovers_a_garbled_first_read():
    # first lap: lingering-tty garbage; second lap: a clean blessed read.
    conn = _FlakyKLConn(["\x00\xffgarbage, no hashes here",
                         kl_output(H_STORED, H_STORED)])
    assert "firmware_blessing" not in names(run(conn))
    assert kl_count(conn) == 2
    # the settle pause ran between the laps
    assert any(c.startswith("sleep 8") for c in conn.history)


def test_unreadable_is_its_own_honest_answer():
    # every lap fails -> UNREADABLE, reported as "couldn't check", never a
    # guessed BLESSED or PARKED, and never a critical fault we didn't confirm.
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0,
                          "rnodeconf: could not open port /dev/ttyACM0", ""))
    issues = run(conn)
    unread = next(i for i in issues if i.check_name == "firmware_blessing")
    assert unread.severity == "info"
    assert unread.auto_fixable is False
    assert "UNREADABLE" in unread.description
    assert "honest" in unread.description
    # P5: the verdict carries its evidence — the trimmed rnodeconf output
    assert "could not open port" in unread.raw_detail
    from diagnostics.radio_firmware import BLESSING_LAPS
    assert kl_count(conn) == BLESSING_LAPS       # all settle laps were spent


def test_all_zero_actual_hash_never_prescribed():
    # C1, Columba's war table (mirrored at rtnode_build's hash step): a
    # device mid-glitch reports an ALL-ZEROS running hash; prescribing that
    # as the expectation bricks hw_ready on the next boot. Zeros = a read
    # glitch = UNREADABLE, and no cure command may carry the zero hash.
    from diagnostics.radio_firmware import ZERO_HASH, BLESSING_LAPS
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0, kl_output(H_STORED, ZERO_HASH), ""))
    issues = run(conn)
    unread = next(i for i in issues if i.check_name == "firmware_blessing")
    assert "UNREADABLE" in unread.description
    assert "--firmware-hash" not in unread.description
    assert ZERO_HASH not in unread.description
    assert kl_count(conn) == BLESSING_LAPS       # zeros never end the laps


def test_zero_target_with_real_actual_is_parked():
    # the classic unmarked offline birth: stored expectation all zeros, a
    # REAL running hash. That is a genuine parked state and the prescription
    # carries the real (non-zero) actual hash.
    from diagnostics.radio_firmware import ZERO_HASH
    conn = conn_with()
    conn.rules.insert(0, ("-K -L", 0, kl_output(ZERO_HASH, H_RUNNING), ""))
    issues = run(conn)
    parked = next(i for i in issues if i.check_name == "firmware_blessing")
    assert parked.severity == "critical"
    assert f"--firmware-hash {H_RUNNING}" in parked.description
    assert f"--firmware-hash {ZERO_HASH}" not in parked.description


def test_unverifiable_port_is_refused_untouched(monkeypatch):
    # C6: -K -L writes KISS frames into the port, and the splitter holds
    # Jonesey without exclusive=True — a port that can't be POSITIVELY
    # proven a work board is refused before anything touches it, and the
    # fw_hash_probe fallback (same port!) must not run either.
    import diagnostics.radio_firmware as rf
    monkeypatch.setattr(rf, "_work_board_cleared", lambda c, p: False)
    conn = conn_with()
    issues = run(conn)
    refused = next(i for i in issues if i.check_name == "firmware_blessing")
    assert refused.severity == "info"
    assert "refusing" in refused.description.lower()
    assert kl_count(conn) == 0                   # the port was never touched
    assert not probe_ran(conn)


def test_work_board_gate_fails_closed_on_error(_cleared_work_board_gate,
                                               monkeypatch):
    # if the roster machinery itself blows up, the gate refuses (fail-closed).
    # _cleared_work_board_gate yields the REAL function (the autouse fixture
    # has already swapped the module attribute for a pass-through).
    import sys

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("roster unavailable")

    monkeypatch.setitem(sys.modules, "ui.onboard_roster", Boom())
    real_gate = _cleared_work_board_gate
    assert real_gate(conn_with(), "/dev/ttyACM1") is False


def test_blessing_work_runs_inside_the_instrumented_check():
    # C7: the UI hears check_start BEFORE the blocking serial read, because
    # the work happens inside the lazy _check call — not before it.
    from workflows.repair import _InstrumentedModule
    conn = conn_with()
    module = RadioFirmwareCheck(conn, NodeProfile())
    seen = {}

    def emit(e):
        if e.type == "check_start" and e.check_name == "firmware_blessing":
            seen["kl_reads_at_start"] = kl_count(conn)

    _InstrumentedModule(module, emit).run()
    assert seen.get("kl_reads_at_start") == 0


def test_blessing_skipped_when_board_unresponsive():
    # no --info = serial_responsive owns the diagnosis; -K -L is never run
    # against a board that didn't identify as an RNode at all.
    conn = conn_with(info_code=1, info="")
    conn.rules.insert(0, ("^systemctl is-active rnsd", 3, "inactive", ""))
    issues = run(conn)
    assert "firmware_blessing" not in names(issues)
    assert kl_count(conn) == 0


def test_antenna_rssi_unverified_when_noise_floor_absent():
    # No rnstatus interface AND no noise-floor line in --info -> we cannot clear
    # the antenna, so we must not imply it is safe to transmit. Unverified (info),
    # not a silent pass (the honesty fix for the noise-floor offender).
    issues = run(conn_with(info=GOOD_INFO))   # GOOD_INFO carries no noise floor
    rssi = next(i for i in issues if i.check_name == "antenna_rssi")
    assert rssi.severity == "info"
