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


# --- the GPS check has to say something TRUE (2026-08-07) -------------------
# It reported "Telemetry fresh" for months on a medic that had never once had a
# fix. True, and useless: freshness is a fact about the serial link, not about
# whether the medic knows where it is.

import json as _json


def _gps(**kw):
    st = {"updated": 1000.0, "sats": 0, "has_fix": False, "gps_frames": 50}
    st.update(kw)
    return _json.dumps(st)


def test_a_live_fix_reports_the_satellite_count():
    f = check_gps_fresh(_gps(sats=9, has_fix=True), now=1001.0)
    assert f.severity == SEV_OK
    assert "9 satellites" in f.detail


def test_a_COASTING_fix_is_a_warning_even_though_the_link_is_healthy():
    """The dangerous state: a position with no satellites behind it. It looks
    like an answer and is not one."""
    f = check_gps_fresh(_gps(sats=0, has_fix=True), now=1001.0)
    assert f.severity == SEV_WARN
    assert "COASTING" in f.detail
    assert "WAS" in f.detail              # names the actual risk


def test_no_fix_indoors_is_NOT_a_fault():
    """Most of a medic's life is indoors. Crying warning for that trains the
    operator to ignore the screen."""
    f = check_gps_fresh(_gps(sats=0, has_fix=False), now=1001.0)
    assert f.severity == SEV_OK
    assert "normal indoors" in f.detail


def test_a_radio_that_never_mentions_gps_is_called_out_separately():
    """Board/firmware question, not a sky question. Sending someone outside
    with a receiver that was never going to answer wastes an afternoon."""
    f = check_gps_fresh(_gps(gps_frames=0), now=1001.0)
    assert f.severity == SEV_WARN
    assert "never sent a GPS frame" in f.detail
    assert "Not a sky problem" in f.detail


def test_stale_telemetry_still_wins_over_everything():
    """If the splitter is not getting frames at all, the position fields are
    meaningless and the link is the story."""
    f = check_gps_fresh(_gps(sats=9, has_fix=True), now=1000.0 + 3600)
    assert f.severity == SEV_WARN
    assert "stale" in f.detail


def test_the_findings_carry_the_evidence_not_just_prose():
    f = check_gps_fresh(_gps(sats=9, has_fix=True), now=1001.0)
    assert f.data["sats"] == 9 and f.data["has_fix"] is True
    assert f.data["gps_frames"] == 50


# --- the other end of the cable ---------------------------------------------

def test_it_notices_networkmanager_eating_the_cable_link():
    """2026-08-11: NM ran DHCP on usb0, failed after 45 s, tore the link down and
    wedged the gadget — killing two builds while the blame went to the power
    supply, the cable, the Pi and two medic USB ports, all of them fine."""
    from monitor.self_diagnose import check_cable_link_unmanaged, SEV_OK, SEV_CRIT
    log = "device (usb0): dhcp4 (usb0): activation: beginning transaction"
    bad = check_cable_link_unmanaged(conf_present=False, nm_log_tail=log)
    assert bad.severity == SEV_CRIT
    assert "45 s" in bad.detail and "usb0" in bad.detail
    assert "99-nodemedic-usb0.conf" in bad.detail, "name the file that fixes it"

    good = check_cable_link_unmanaged(conf_present=True)
    assert good.severity == SEV_OK


def test_missing_config_without_evidence_is_a_warning_not_a_crisis():
    """No drop-in but no DHCP seen either — worth fixing before the next cable
    birth, not worth shouting about on a medic that may never do one."""
    from monitor.self_diagnose import check_cable_link_unmanaged, SEV_WARN
    f = check_cable_link_unmanaged(conf_present=False, nm_log_tail="")
    assert f.severity == SEV_WARN


def test_the_cable_link_check_actually_runs():
    """A check that exists but is never called is a check that does not exist.
    This one was written and left unwired for an hour — the same shape as the
    bug it catches, where the medic diagnosed everything except itself."""
    from tests.srcutil import func_source
    src = func_source("monitor/self_diagnose_runtime.py", "gather")
    assert "check_cable_link_unmanaged" in src
    assert "NM_USB0_CONF" in src, "it has to look for the real file"
    assert "NetworkManager" in src, "and read NM's log for the DHCP evidence"
