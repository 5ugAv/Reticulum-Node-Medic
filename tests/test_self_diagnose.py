"""Self Diagnose — the medic's checks on its own onboard radio/GPS board."""

from monitor.self_diagnose import (
    check_usb_present, check_chip_alive, check_firmware_provisioned,
    check_splitter, check_rns_link, check_gps_fresh, summarize,
    ONBOARD_SERIAL, SEV_OK, SEV_WARN, SEV_CRIT)


def test_usb_present_detects_drop():
    ok = check_usb_present(f"usb-Espressif_..._{ONBOARD_SERIAL}-if00")
    assert ok.ok and ok.fix is None
    gone = check_usb_present("usb-something-else-if00")
    assert gone.severity == SEV_CRIT and gone.fix == "usb_recover"


def test_chip_alive_from_esptool():
    alive = check_chip_alive("Detecting chip type... ESP32-S3\nChip is ESP32-S3 (rev v0.2)")
    assert alive.ok
    dead = check_chip_alive("Connecting.....\nA fatal error occurred: Failed to connect")
    assert dead.severity == SEV_CRIT and dead.fix == "usb_recover"


def test_firmware_provisioned_detects_the_incident_symptom():
    # exactly what rnodeconf printed for the corrupt Jonesey
    bad = check_firmware_provisioned(
        "Radio reporting frequency is 16.9 MHz\n"
        "Serial port opened, but RNode did not respond. Is a valid firmware installed?")
    assert bad.severity == SEV_CRIT and bad.fix == "reflash_provision"
    good = check_firmware_provisioned("Firmware version: 1.80\nReticulum: abc123")
    assert good.ok


def test_splitter_spinning_hot_is_flagged():
    hot = check_splitter(is_active=True, cpu_seconds=1320, uptime_seconds=1560)
    assert hot.severity == SEV_WARN and hot.fix == "restart_splitter"   # ~85% CPU
    down = check_splitter(is_active=False, cpu_seconds=0, uptime_seconds=0)
    assert down.severity == SEV_CRIT and down.fix == "restart_splitter"
    good = check_splitter(is_active=True, cpu_seconds=5, uptime_seconds=1560)
    assert good.ok


def test_splitter_serial_error_in_log():
    f = check_splitter(True, 5, 1000, recent_log="serial.serialutil.SerialException: ...")
    assert f.severity == SEV_WARN and f.fix == "restart_splitter"


def test_rns_link_loop_detected():
    loop = check_rns_link("Opening serial port /tmp/rnode-jonesey...\n"
                          "Could not detect device for RNodeInterface[RNode LoRa]")
    assert loop.severity == SEV_CRIT and loop.fix == "reflash_provision"
    fine = check_rns_link("[INFO] Started rnsd")
    assert fine.ok


def test_gps_freshness_is_warning_not_critical():
    stale = check_gps_fresh('{"updated": 1000}', now=1000 + 3600)
    assert stale.severity == SEV_WARN          # indoors GPS is legitimately null
    fresh = check_gps_fresh('{"updated": 1000}', now=1000 + 30)
    assert fresh.ok
    assert check_gps_fresh("garbage", now=1).severity == SEV_WARN


def test_summarize_orders_fixes_and_flags_worst():
    findings = [
        check_usb_present("wrong"),                                    # crit usb_recover
        check_firmware_provisioned("RNode did not respond"),           # crit reflash_provision
        check_splitter(False, 0, 0),                                   # crit restart_splitter
        check_gps_fresh('{"updated":0}', now=99999),                   # warn (no fix)
    ]
    s = summarize(findings)
    assert s["worst"] == SEV_CRIT and s["healthy"] is False
    assert s["critical"] == 3 and s["warning"] == 1
    assert s["fixes"] == ["usb_recover", "reflash_provision", "restart_splitter"]


def test_summarize_all_healthy():
    s = summarize([check_usb_present(f"x{ONBOARD_SERIAL}"),
                   check_rns_link("ok"), check_gps_fresh('{"updated":100}', now=110)])
    assert s["healthy"] and s["worst"] == SEV_OK and s["fixes"] == []
