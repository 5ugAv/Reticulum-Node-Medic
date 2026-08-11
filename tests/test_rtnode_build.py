import os

import pytest

from node_profile import NodeProfile, NodeHardware
from transport.connection import EmulatedConnection
from workflows.rtnode_build import (
    RTNodeBuildWorkflow,
    RTNODE_BUILD_ENV,
    RTNODE_REPO_URL,
    RTNODE_BRANCH,
    check_sd_overflow,
)

BEACON_LINE = (
    "[HealthBeacon] announce dst=11223344556677889900aabbccddeeff "
    "data=010000002400c7cc053b3f000602")

EXPECTED_STEPS = [
    "detect_board",
    "flash_firmware",
    "wifi_onboarding",   # must precede verify_beacon: a fresh board is silent
    "verify_beacon",     # until onboarded + rebooted
    "verify_sd_overflow",  # SD-overflow targets only; others skip
    "birth_certificate",
]


def conn(port="/dev/cu.usbmodem2101", flash_code=0, beacon=BEACON_LINE):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    # detect_heltec_v4 now lists Linux globs first, so the command starts
    # "ls /dev/ttyACM* ..." — the tool ships on a Pi.
    c.rules.insert(0, ("^ls /dev/tty", 0 if port else 1, port, ""))
    c.rules.insert(0, ("pio run", flash_code, "SUCCESS" if flash_code == 0 else "err", ""))
    # verify_beacon now RESETS + captures serial via an inline python one-liner
    # (the old rnm-serial-capture never existed on the medic); match its
    # distinctive serial.Serial invocation.
    c.rules.insert(0, ("serial.Serial", 0, beacon, ""))
    # the outcome probe (board_configured_on_lan): default = not configured
    c.rules.insert(0, ("curl -s -m 6 http://rtnode", 0, "", ""))
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0, "", ""))
    return c


def wf(c=None, profile=None, gps_reader=lambda: None):
    return RTNodeBuildWorkflow(c or conn(), profile or NodeProfile(),
                               gps_reader=gps_reader)


def test_steps_registered_in_order():
    assert [n for n, _ in wf().steps] == EXPECTED_STEPS


def test_full_run_completes_all_steps():
    w = wf()
    w.run_all()
    assert w.current_index == len(EXPECTED_STEPS)
    assert all(r.success for r in w.results)


def test_detect_sets_heltec_and_port():
    w = wf(conn(port="/dev/cu.usbmodem2101"))
    r = w.steps[0][1](w)
    assert r.success
    assert w.profile.hardware is NodeHardware.HELTEC_V4
    assert w.profile.connection_port == "/dev/cu.usbmodem2101"
    assert w.profile.radio.serial_port == "/dev/cu.usbmodem2101"


def test_detect_finds_linux_ttyacm_port():
    # the medic is a Pi: a real Heltec V4 enumerates as /dev/ttyACM0
    w = wf(conn(port="/dev/ttyACM0"))
    r = w.steps[0][1](w)
    assert r.success
    assert w.profile.connection_port == "/dev/ttyACM0"


def test_detect_lists_linux_globs_first():
    from workflows.rtnode_build import _PORT_GLOBS
    assert _PORT_GLOBS[0] == "/dev/ttyACM*"          # Pi wins on the ship platform


def test_detect_fails_when_no_board():
    w = wf(conn(port=""))
    r = w.steps[0][1](w)
    assert r.success is False


def test_detect_warns_on_multiple_boards():
    c = conn(port="/dev/cu.usbmodem2101 /dev/cu.usbserial-A50285BI")
    w = wf(c)
    r = w.steps[0][1](w)
    assert r.success
    assert w.profile.connection_port == "/dev/cu.usbmodem2101"   # first, deterministically
    assert "WARNING" in r.message and "2 USB-serial devices" in r.message


def test_detect_uses_pinned_work_board_port_not_first_tty():
    # On the medic ttyACM0 is Jonesey (its OWN radio); the work board is ttyACM1.
    # A pinned board_port MUST be used verbatim so the build never flashes the
    # medic's radio — even though a naive `ls` would list ttyACM0 first.
    c = conn(port="/dev/ttyACM1")            # ls of the pinned port confirms present
    w = RTNodeBuildWorkflow(c, NodeProfile(), gps_reader=lambda: None,
                            board_port="/dev/ttyACM1")
    r = w.steps[0][1](w)
    assert r.success
    assert w.profile.connection_port == "/dev/ttyACM1"
    assert "excluded" in r.message.lower()


def test_detect_pinned_port_gone_fails_cleanly():
    c = EmulatedConnection(default_code=0, default_stdout="")   # ls finds nothing
    w = RTNodeBuildWorkflow(c, NodeProfile(), gps_reader=lambda: None,
                            board_port="/dev/ttyACM1")
    r = w.steps[0][1](w)
    assert r.success is False and "disappear" in r.message.lower()


def test_make_rtnode_build_pins_to_work_board_port():
    from ui.hw_factories import make_rtnode_build
    wf_ = make_rtnode_build(lambda: None, ports_fn=lambda: ["/dev/ttyACM1"])
    assert getattr(wf_, "board_port", None) == "/dev/ttyACM1"


def test_flash_uses_platformio_env_and_port():
    c = conn()
    w = wf(c)
    w.steps[0][1](w)          # detect (sets port)
    r = w.steps[1][1](w)      # flash
    assert r.success
    flash_cmd = next(cmd for cmd in c.history if "pio run" in cmd)
    assert RTNODE_BUILD_ENV in flash_cmd
    assert "-t upload" in flash_cmd
    assert "/dev/cu.usbmodem2101" in flash_cmd


def test_flash_failure_reported():
    c = conn(flash_code=1)
    w = wf(c)
    w.steps[0][1](w)
    r = w.steps[1][1](w)
    assert r.success is False


def test_verify_decodes_beacon_and_records_identity():
    w = wf()
    for i in range(4):        # detect, flash, wifi_onboarding, verify
        w.steps[i][1](w)
    assert w.profile.reticulum_identity_hash == "11223344556677889900aabbccddeeff"
    assert w.beacon is not None
    assert w.beacon.firmware_version == "0.6.2"
    assert w.beacon.board_label == "Heltec32 V4"


def test_verify_fails_without_beacon():
    w = wf(conn(beacon="boot ok, no beacon here"))
    w.steps[0][1](w)          # detect
    w.steps[1][1](w)          # flash
    w.steps[2][1](w)          # wifi_onboarding
    r = w.steps[3][1](w)      # verify_beacon
    assert r.success is False


def test_wifi_onboarding_is_operator_step():
    w = wf()
    r = w.steps[2][1](w)
    # portal onboarding is a documented manual step (skipped, but not a failure)
    assert r.skipped is True
    assert "RTNode-Setup" in r.message
    assert "10.0.0.1" in r.message


def test_wifi_onboarding_prefills_recommended_radio_params():
    w = wf()
    w.steps[2][1](w)
    form = w.onboarding
    # recommended LoRa settings pre-filled using the REAL portal field names
    assert form["freq"] == "915.125"     # MHz decimal string
    assert form["bw"] == "125000"        # Hz integer
    assert form["sf"] == "9"
    assert form["cr"] == "5"
    assert form["txp"] == "17"


def test_wifi_onboarding_leaves_name_and_creds_for_operator():
    w = wf()
    w.steps[2][1](w)
    form = w.onboarding
    # node name + WiFi credentials are blank — operator fills these
    assert form["node_name"] == ""
    assert form["ssid"] == ""
    assert form["psk"] == ""
    assert form["wifi_en"] == "0"


def test_wifi_onboarding_respects_overridden_radio_params():
    p = NodeProfile()
    p.radio.frequency_mhz = 868.0
    p.radio.bandwidth_khz = 250.0
    w = wf(profile=p)
    w.steps[2][1](w)
    assert w.onboarding["freq"] == "868.0"
    assert w.onboarding["bw"] == "250000"


def test_onboarding_captures_gps_but_advertises_nothing_by_default():
    """A GPS fix is knowledge, not consent. The medic records where the node is
    and publishes none of it until the birth screen's share step says so."""
    w = wf(gps_reader=lambda: (-37.814, 144.963))
    w.steps[2][1](w)          # wifi_onboarding
    assert w.gps_fix is not None                 # the medic still knows
    assert w.onboarding["advert_en"] == "0"      # ...and still says nothing
    assert "advert_lat" not in w.onboarding


def test_onboarding_advertises_a_fuzzed_point_when_sharing_was_chosen():
    p = NodeProfile()
    p.share_location = "approx"
    w = wf(profile=p, gps_reader=lambda: (-37.814, 144.963))
    w.steps[2][1](w)          # wifi_onboarding
    assert w.gps_fix is not None
    assert w.onboarding["advert_en"] == "1"
    # fuzzed before it leaves the medic (2026-08-01 audit); exact fix lives
    # only in the birth certificate
    assert w.onboarding["advert_lat"] != "-37.814000"
    assert abs(float(w.onboarding["advert_lat"]) - (-37.814)) < 0.02
    assert w.onboarding["advert_lon"] != "144.963000"
    assert abs(float(w.onboarding["advert_lon"]) - 144.963) < 0.02
    assert w.onboarding["advert_jitter"] == "1"


def test_onboarding_no_gps_disables_advert():
    w = wf(gps_reader=lambda: None)
    w.steps[2][1](w)
    assert w.gps_fix is None
    assert w.onboarding["advert_en"] == "0"
    assert "advert_lat" not in w.onboarding


def test_birth_certificate_records_exact_location():
    w = wf(gps_reader=lambda: (-37.814, 144.963))
    w.run_all()
    assert w.birth_certificate["location"] == {
        "lat": -37.814, "lon": 144.963, "source": "pi_gps",
        # the DECISION travels beside the coordinates it governs
        "share_location": "hidden"}


def test_birth_certificate_location_none_without_gps():
    w = wf(gps_reader=lambda: None)
    w.run_all()
    assert w.birth_certificate["location"] is None


def test_birth_certificate_summarises_node():
    w = wf()
    w.run_all()
    cert = w.birth_certificate
    assert cert["board"] == "Heltec32 V4"
    assert cert["firmware"] == "0.6.2"
    assert cert["identity_hash"] == "11223344556677889900aabbccddeeff"
    assert cert["build_env"] == RTNODE_BUILD_ENV
    assert "frequency_mhz" in cert


def test_flash_failure_stops_run_all():
    c = conn(flash_code=1)
    w = wf(c)
    w.run_all()
    # detect ok (index 0) -> flash fails at index 1, does not advance
    assert w.current_index == 1
    assert w.results[-1].name == "flash_firmware"
    assert w.results[-1].success is False


def test_carried_flash_script_exists_and_is_fixed():
    path = os.path.join(os.path.dirname(__file__), "..", "assets", "scripts",
                        "flash_rtnode2400.sh")
    body = open(path).read()
    assert "set -o pipefail" in body          # git-in-pipe fix
    assert "CLT_WAIT_MAX" in body             # bounded xcode-select wait
    assert "reset --hard" in body             # robust existing-clone refresh
    assert RTNODE_BUILD_ENV in body


# ---- T-Beam Supreme target -----------------------------------------------


def sup(c=None):
    return RTNodeBuildWorkflow(c or conn(), NodeProfile(),
                               gps_reader=lambda: None, target="tbeam_supreme")


def test_supreme_target_selects_its_platformio_env():
    c = conn()
    w = sup(c)
    w.steps[0][1](w)          # detect
    w.steps[1][1](w)          # flash
    flash_cmd = next(cmd for cmd in c.history if "pio run" in cmd)
    assert "tbeam_supreme_boundary-local" in flash_cmd
    assert RTNODE_BUILD_ENV not in flash_cmd     # not the V4 env


def test_supreme_detect_sets_supreme_hardware():
    w = sup()
    w.steps[0][1](w)
    assert w.profile.hardware is NodeHardware.TBEAM_SUPREME


def test_default_target_is_still_heltec_v4():
    w = wf()
    assert w.profile.hardware is not NodeHardware.TBEAM_SUPREME
    w.steps[0][1](w)
    assert w.profile.hardware is NodeHardware.HELTEC_V4


def test_v4_target_skips_sd_overflow_verify():
    w = wf()
    r = w.steps[4][1](w)      # verify_sd_overflow
    assert r.name == "verify_sd_overflow"
    assert r.skipped is True
    assert r.success is True


def test_supreme_sd_verify_defers_without_address():
    w = sup()
    r = w.steps[4][1](w)      # verify_sd_overflow, no node_address set
    assert r.skipped is True and r.success is True
    assert "onboarded" in r.message


def test_supreme_sd_verify_checks_status_when_addressed():
    c = conn()
    c.rule("curl -s -m 5 http://rtnode.local/status", 0,
           '{"sd_overflow":{"mounted":true,"card_mb":61048,"used_kb":12,'
           '"files":["/destination_table","/cache"]}}')
    w = sup(c)
    w.node_address = "rtnode.local"
    r = w.steps[4][1](w)
    assert r.success is True
    assert "SD tier up" in r.message and "path table present" in r.message


def test_supreme_birth_cert_records_supreme_env():
    c = conn()
    w = sup(c)
    w.run_all()
    assert w.birth_certificate["build_env"] == "tbeam_supreme_boundary-local"


def test_check_sd_overflow_mounted_with_path_table():
    ok, detail = check_sd_overflow(
        '{"sd_overflow":{"mounted":true,"card_mb":61048,"used_kb":40,'
        '"files":["/destination_table"]}}')
    assert ok is True and "path table present" in detail


def test_check_sd_overflow_mounted_fresh_card():
    ok, detail = check_sd_overflow(
        '{"sd_overflow":{"mounted":true,"card_mb":61048,"used_kb":0,"files":[]}}')
    assert ok is True and "not written yet" in detail


def test_check_sd_overflow_not_mounted_fails():
    ok, detail = check_sd_overflow('{"sd_overflow":{"mounted":false}}')
    assert ok is False and "not mounted" in detail


def test_check_sd_overflow_absent_object_fails():
    ok, detail = check_sd_overflow('{"wifi_rssi":-61,"lora_online":true}')
    assert ok is False and "sd_overflow" in detail


def test_firmware_provenance_matches_carried_flasher():
    # The programmatic build (rtnode_build.py) and the carried human flasher
    # must target the SAME firmware repo/branch/env, or the two paths drift
    # apart from the actual RTNode-2400 firmware.
    path = os.path.join(os.path.dirname(__file__), "..", "assets", "scripts",
                        "flash_rtnode2400.sh")
    body = open(path).read()
    assert RTNODE_REPO_URL in body
    assert RTNODE_BRANCH in body
    assert RTNODE_BUILD_ENV in body


def test_wifi_onboarding_auto_provisions_with_name_and_medic_wifi():
    from workflows.rtnode_build import RTNodeBuildWorkflow, wifi_onboarding
    from node_profile import NodeProfile
    calls = {}
    def provision(profile, name, ssid, psk, **kw):
        calls["args"] = (name, ssid, psk)
        return (True, "configured")
    w = RTNodeBuildWorkflow(conn(), NodeProfile(), node_name="FAITH B",
                            auto_provision=True, provision=provision,
                            wifi_credentials=lambda: ("Home", "pw"))
    r = wifi_onboarding(w)
    assert r.success
    assert calls["args"] == ("FAITH B", "Home", "pw")


def test_wifi_onboarding_manual_fallback_when_not_auto():
    from workflows.rtnode_build import RTNodeBuildWorkflow, wifi_onboarding
    from node_profile import NodeProfile
    w = RTNodeBuildWorkflow(conn(), NodeProfile(), node_name="N")  # auto_provision off
    r = wifi_onboarding(w)
    assert r.skipped and "RTNode-Setup" in r.message


# ---- single-pass hardening (2026-07-30: believe outcomes, not plumbing) -----

CONFIGURED_STATUS = '{"fork":"RTNode","fw_version":"0.7.0","node_name":"FAITH B"}'
BY_ID_LINE = ("lrwxrwxrwx 1 root root 13 Jul 30 18:00 "
              "usb-Espressif_USB_JTAG_serial_debug_unit_02:00:00:07:00:07-if00 "
              "-> ../../ttyACM1")


def _quiet_sleep(monkeypatch):
    import workflows.rtnode_build as rb
    monkeypatch.setattr(rb, "_sleep", lambda s: None)


def test_helpers_derive_lan_host_from_usb_serial():
    from workflows.rtnode_build import default_lan_host
    assert default_lan_host("02:00:00:07:00:07") == "rtnode0007.local"
    assert default_lan_host("") == ""


def test_flash_retries_transient_failure_then_succeeds(monkeypatch):
    _quiet_sleep(monkeypatch)
    c = conn(port="/dev/ttyACM1")
    calls = {"n": 0}
    orig = c.run

    def run(cmd, timeout=30):
        if "pio run" in cmd:
            calls["n"] += 1
            if calls["n"] == 1:
                return (1, "", "A fatal error occurred: transient USB")
            return (0, "SUCCESS", "")
        return orig(cmd, timeout)

    c.run = run
    w = wf(c)
    w.steps[0][1](w)                     # detect
    r = w.steps[1][1](w)                 # flash_firmware
    assert r.success is True and "attempt 2" in r.message
    assert calls["n"] == 2


def test_flash_gives_up_after_three_attempts(monkeypatch):
    _quiet_sleep(monkeypatch)
    c = conn(port="/dev/ttyACM1", flash_code=1)
    w = wf(c)
    w.steps[0][1](w)
    r = w.steps[1][1](w)
    assert r.success is False


def _auto_wf(c, provision, monkeypatch, creds=("HomeNet", "pw")):
    _quiet_sleep(monkeypatch)
    w = wf(c)
    w.auto_provision = True
    w._provision = provision
    w._wifi_credentials = lambda: creds
    w._join_ap = w._post = w._rejoin = None
    w.node_name = "FAITH B"
    w.steps[0][1](w)                     # detect (sets connection_port)
    return w


def test_onboarding_skips_portal_when_board_already_configured(monkeypatch):
    c = conn(port="/dev/ttyACM1")
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0, BY_ID_LINE, ""))
    c.rules.insert(0, ("curl -s -m 6 http://rtnode0007.local/status", 0,
                       CONFIGURED_STATUS, ""))
    never = {"called": False}

    def provision(*a, **k):
        never["called"] = True
        return (False, "should not run")

    w = _auto_wf(c, provision, monkeypatch)
    r = next(f for n, f in w.steps if n == "wifi_onboarding")(w)
    assert r.success is True
    assert "already configured" in r.message
    assert never["called"] is False


def test_onboarding_trusts_outcome_when_provision_tail_fails(monkeypatch):
    # provision claims failure (rejoin hiccup) but the config actually LANDED:
    # the outcome probe finds the board serving /status -> step succeeds.
    c = conn(port="/dev/ttyACM1")
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0, BY_ID_LINE, ""))
    probe = {"n": 0}
    orig = c.run

    def run(cmd, timeout=30):
        if "curl -s -m 6 http://rtnode0007.local/status" in cmd:
            probe["n"] += 1
            # not configured on the pre-check; configured after provision "failed"
            return (0, "" if probe["n"] == 1 else CONFIGURED_STATUS, "")
        return orig(cmd, timeout)

    c.run = run
    w = _auto_wf(c, lambda *a, **k: (False, "Could not rejoin medic wifi"),
                 monkeypatch)
    r = next(f for n, f in w.steps if n == "wifi_onboarding")(w)
    assert r.success is True
    assert "Config landed" in r.message


def test_onboarding_retries_provision_then_fails_honestly(monkeypatch):
    c = conn(port="/dev/ttyACM1")
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0, BY_ID_LINE, ""))
    c.rules.insert(0, ("curl -s -m 6 http://rtnode0007.local/status", 0, "", ""))
    tries = {"n": 0}

    def provision(*a, **k):
        tries["n"] += 1
        return (False, "No network with SSID 'RTNode-Setup' found.")

    w = _auto_wf(c, provision, monkeypatch)
    r = next(f for n, f in w.steps if n == "wifi_onboarding")(w)
    assert r.success is False
    assert tries["n"] == 2               # full-cycle retry happened


def test_verify_accepts_init_banner_without_announce():
    c = conn(port="/dev/ttyACM1",
             beacon="[HealthBeacon] init dst=8899aabbccddeeff00112233445566ab, first announce in ~30s")
    w = wf(c)
    w.steps[0][1](w)
    r = next(f for n, f in w.steps if n == "verify_beacon")(w)
    assert r.success is True
    assert w.profile.reticulum_identity_hash.startswith("8899aabb")


def test_verify_falls_back_to_lan_probe_when_serial_quiet():
    c = conn(port="/dev/ttyACM1", beacon="")
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0, BY_ID_LINE, ""))
    c.rules.insert(0, ("curl -s -m 6 http://rtnode0007.local/status", 0,
                       CONFIGURED_STATUS, ""))
    w = wf(c)
    w.steps[0][1](w)
    r = next(f for n, f in w.steps if n == "verify_beacon")(w)
    assert r.success is True
    assert "alive on the LAN" in r.message
