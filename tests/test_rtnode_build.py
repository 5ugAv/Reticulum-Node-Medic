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


def test_flash_failure_reported(monkeypatch):
    _quiet_sleep(monkeypatch)          # the flash path retries with real sleeps
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


def test_flash_failure_stops_run_all(monkeypatch):
    _quiet_sleep(monkeypatch)          # the flash path retries with real sleeps
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


# --- the T-Echo target, added 2026-08-19 after the firmware was proven -------

def test_techo_target_exists_with_its_own_mechanism():
    """The T-Echo cannot ride the PlatformIO pipeline: every pio nRF52 env in
    the firmware tree fails on missing Arduino auto-prototypes (79ea489), it
    flashes over serial DFU, and it has no WiFi radio so the portal onboarding
    cannot exist for it. The mechanism field is what routes all of that."""
    from workflows.rtnode_build import RTNODE_TARGETS
    t = RTNODE_TARGETS["techo"]
    assert t.mechanism == "nrf_dfu"
    assert t.verify == "eeprom"
    assert "noalloc" in t.build_env, (
        "the allocator build crashes this board before main() — noalloc is "
        "the image that ships until the init order is fixed")


def test_techo_provision_bytes_match_the_firmware():
    """PRODUCT_TECHO 0x15 / MODEL_17 0x17 / hwrev 1, from the firmware's own
    Boards.h — never guessed (the V4 homebrew-model lesson capped TX power)."""
    from workflows.rtnode_build import TECHO_PROVISION_ARGS
    assert TECHO_PROVISION_ARGS == "--product 15 --model 17 --hwrev 1"


def test_techo_provisioning_resolves_the_raw_port():
    """rnodeconf on nRF52 must NEVER get a by-id symlink: its mid-flow rescan
    resolves a symlink's serial to None and then matches /dev/ttyAMA10 — the
    medic's own UART — and provisions THAT while reporting success (proven with
    a wire spy, 2026-08-19). The step must readlink before every rnodeconf."""
    from tests.srcutil import func_source
    src = func_source("workflows/rtnode_build.py", "_techo_raw_port")
    assert "readlink -f" in src
    resolver = func_source("workflows/rtnode_build.py", "_nrf_reresolve")
    assert "_nrf_ports" in resolver, "the strict resolver must enumerate"
    for fn in ("_onboard_techo", "_verify_techo"):
        body = func_source("workflows/rtnode_build.py", fn)
        assert "_nrf_reresolve" in body, (
            f"{fn} must resolve via the strict resolver (readlinked raw "
            f"ports, refuses on none/many — storm review F1/F5)")
        assert "by-id" not in body.replace("by-id symlink", ""), (
            f"{fn} must not hand rnodeconf a by-id path")


def test_techo_onboarding_bakes_the_canonical_params():
    """Set-radio-params-at-birth: the node must leave the bench running
    915.125/125/SF9/CR5/17 standalone, not wait for a host to set them.

    -T (TNC mode) is the ONLY rnodeconf branch that consumes the five flags.
    An earlier version of this very test asserted "-N" with a comment calling
    it TNC mode — -N is NORMAL (host-controlled) mode and silently ignores all
    five flags, so the test pinned the bug it existed to prevent (adversarial
    review, 2026-08-19, verified in rnodeconf's source). Assert the flag WITH
    its meaning so the two cannot drift apart again."""
    from tests.srcutil import func_source
    src = func_source("workflows/rtnode_build.py", "_onboard_techo")
    assert "-T --freq" in src, "-T/--tnc is the branch that consumes the flags"
    assert "-N --freq" not in src, "-N is host mode; it IGNORES the flags"
    for field in ("frequency_mhz", "bandwidth_khz", "spreading_factor",
                  "coding_rate", "tx_power_dbm"):
        assert field in src, f"params must come from the profile ({field})"


def test_techo_verify_is_honest_about_its_scope():
    """It reads the EEPROM back over USB. It must not claim an over-the-air
    check that never happened — the medic's radio may not even be present."""
    from tests.srcutil import func_source
    src = func_source("workflows/rtnode_build.py", "_verify_techo")
    assert "No over-the" in src   # split across a source line


def test_an_unidentified_nrf52_still_gets_no_rtnode_offer():
    """An ambiguous nRF52 stays RNode-only — offering a build some candidates
    cannot take is the wrong kind of fail-open. The example no-build board
    used to be the RAK4631, which then GAINED a build (2026-08-20) — the
    second board to graduate out of this test, which is exactly why the gate
    keys on the registry and never a hardcoded list."""
    from ui.board_detect import firmware_options
    assert firmware_options("nrf52840") == ["rnode"]
    assert firmware_options("nrf52840", None) == ["rnode"]
    # heltec_t114 graduated 2026-08-20 — the THIRD board out of this test.
    # The remaining honest no-build nRF example is a key with no target.
    assert firmware_options("nrf52840", "xiao_nrf52840") == ["rnode"]
    assert firmware_options("nrf52840", "techo") == ["rtnode2400", "rnode"]
    assert firmware_options("nrf52840", "rak4631") == ["rtnode2400", "rnode"]
    assert firmware_options("nrf52840", "heltec_t114") == ["rtnode2400", "rnode"]


# --- the techo steps EXECUTED against an emulated medic, not just read -------
# The adversarial review's coverage finding was blunt: every techo test was a
# registry lookup or a source-inspection substring, so a wrong rnodeconf flag
# sailed through a green suite — and one did (-N for -T). These run the real
# functions against EmulatedConnection with the shell answers a real medic
# gives, so a broken command line has to actually fail here.

def _techo_wf(conn):
    from workflows.rtnode_build import RTNodeBuildWorkflow
    from node_profile import NodeProfile
    return RTNodeBuildWorkflow(conn, NodeProfile(), target="techo",
                               node_name="testnode")


class _TechoMedic(EmulatedConnection):
    """A medic with a T-Echo attached, stateful where the flow is: the board
    starts in APP mode (its NEW identity, one capital from the bootloader's)
    and only presents the bootloader after the 1200-baud touch."""

    def __init__(self, start_in_bootloader=False):
        super().__init__(default_code=0, default_stdout="")
        self.in_bootloader = start_in_bootloader

    def run(self, cmd, *a, **k):
        self.history.append(cmd)
        if "techo_touch.py" in cmd:
            self.in_bootloader = True
            return 0, "touch sent", ""
        if "kiss_detect.py" in cmd:
            # answers only when the app is running (not in the bootloader)
            return (1 if self.in_bootloader else 0), "detect: answered", ""
        if "udevadm info" in cmd:
            # the PID is the truth: 002a in the bootloader, 8029 running.
            # (The command pipes through grep|cut; the emulator returns what
            # the PIPELINE would print, not the raw udevadm line.)
            return 0, ("002a" if self.in_bootloader else "8029"), ""
        if "for f in /dev/serial/by-id/" in cmd:
            return 0, "/dev/ttyACM1\n", ""
        if "grep -q 'LilyGo_T-Echo'" in cmd:          # bootloader, exact case
            return (0 if self.in_bootloader else 1), "", ""
        if "grep -q 'T-Echo_RTNode-2400'" in cmd:     # the RUNNING app, exact
            return (1 if self.in_bootloader else 0), "", ""
        if "grep -q 'T-Echo'" in cmd:                 # legacy loose pattern
            return 0, "", ""
        if "make firmware-techo" in cmd:
            return 0, "Sketch uses 629796 bytes", ""
        if "make flash-techo" in cmd:
            self.in_bootloader = False                # DFU ends in the app
            return 0, "Device programmed.", ""
        if "partition_hashes from_device" in cmd:
            return 0, ("b9a932741fd128ac7dc137bc733b1b9a"
                       "9189a71eca95fd4ce55d4836de8196bc"), ""
        if "--firmware-hash" in cmd:
            return 0, ("EEPROM checksum correct\nDevice signature validated\n"
                       "Firmware hash set"), ""
        if "rnodeconf" in cmd and " -r " in cmd:
            return 0, "EEPROM Bootstrapping successful!", ""
        if "rnodeconf" in cmd and " -T " in cmd:
            return 0, "Device set to TNC operating mode", ""
        if "techo_bootlog.py" in cmd:
            return 0, ("[STACK] loop task headroom: 3136 / 4096 bytes free\n"
                       "[RTNode] identity=aabbccddeeff00112233445566778899 "
                       "dst=99887766554433221100ffeeddccbbaa\n"
                       "RNS is READY!"), ""
        if "rnodeconf" in cmd and " -K -L" in cmd:
            h = "b9a932741fd128ac7dc137bc733b1b9a9189a71eca95fd4ce55d4836de8196bc"
            return 0, (f"The target firmware hash is: {h}\n"
                       f"The actual firmware hash is: {h}"), ""
        if "rnodeconf" in cmd and " -i" in cmd:
            return 0, ("Current firmware version: 1.85\n"
                       "EEPROM checksum correct\nDevice signature validated\n"
                       "Firmware version   : 1.85"), ""
        return 0, "", ""


def test_flash_techo_survives_the_identity_rename():
    """The image RENAMES the board (LilyGO / T-Echo RTNode-2400). The wait
    after flashing must accept that identity — the first version waited for
    "Nordic", which the new image never presents, so every successful first
    flash would have reported failure (both reviews, independently)."""
    from workflows.rtnode_build import _flash_techo
    c = _TechoMedic()
    res = _flash_techo(_techo_wf(c), "/dev/ttyACM1")
    assert res.success, res.message
    assert not any("grep -qi" in x for x in c.history), (
        "case-insensitive identity greps are how a running board got read as "
        "already-in-bootloader")
    assert not any("'Nordic'" in x for x in c.history), (
        "nothing may wait for the identity the image itself removes")


def test_flash_techo_touches_a_running_board_first():
    """A board in APP mode must get the 1200-baud touch — the old
    case-insensitive check read the app identity as already-in-bootloader,
    skipped the touch, and the flash then refused with the board plugged in."""
    from workflows.rtnode_build import _flash_techo
    c = _TechoMedic(start_in_bootloader=False)
    res = _flash_techo(_techo_wf(c), "/dev/ttyACM1")
    assert res.success, res.message
    assert any("techo_touch.py" in x for x in c.history)


def test_flash_techo_skips_the_touch_when_already_in_bootloader():
    from workflows.rtnode_build import _flash_techo
    c = _TechoMedic(start_in_bootloader=True)
    res = _flash_techo(_techo_wf(c), "/dev/ttyACM1")
    assert res.success, res.message
    assert not any("techo_touch.py" in x for x in c.history)


def test_onboard_techo_uses_tnc_mode_with_all_five_params():
    from workflows.rtnode_build import _onboard_techo
    c = _TechoMedic()
    res = _onboard_techo(_techo_wf(c))
    assert res.success, res.message
    tnc = [x for x in c.history if " -T " in x and "rnodeconf" in x]
    assert tnc, "the params must go through -T (TNC mode)"
    line = tnc[0]
    assert "--freq 915125000" in line and "--bw 125000" in line
    assert "--sf 9" in line and "--cr 5" in line and "--txp 17" in line
    assert " -N " not in line, "-N silently ignores every one of these flags"
    assert "by-id" not in line, "rnodeconf must get the RAW port on nRF52"


def test_onboard_techo_fails_when_tnc_mode_is_not_confirmed():
    """The old exit-code check was dead (`| tail` ate it, no pipefail), so a
    failed params step reported the full green TNC message. A positive
    confirmation is required now."""
    from workflows.rtnode_build import _onboard_techo

    class _NoTnc(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "rnodeconf" in cmd and " -T " in cmd:
                self.history.append(cmd)
                return 0, "some unrelated chatter", ""
            return super().run(cmd, *a, **k)

    res = _onboard_techo(_techo_wf(_NoTnc()))
    assert not res.success
    assert "Radio parameters failed" in res.message


def test_onboard_techo_accepts_an_already_provisioned_board():
    """A re-birth must not fail because the identity already exists — that is
    the identity SURVIVING, which is the design. The read-back decides."""
    from workflows.rtnode_build import _onboard_techo

    class _Provisioned(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "rnodeconf" in cmd and " -r " in cmd:
                self.history.append(cmd)
                return 0, ("EEPROM bootstrap was requested, but a valid "
                           "EEPROM was already present.\n"
                           "No changes are being made."), ""
            return super().run(cmd, *a, **k)

    res = _onboard_techo(_techo_wf(_Provisioned()))
    assert res.success, res.message


def test_verify_techo_reads_back_and_captures_the_firmware_version():
    from workflows.rtnode_build import _verify_techo
    wf = _techo_wf(_TechoMedic())
    res = _verify_techo(wf)
    assert res.success, res.message
    assert "No over-the" in res.message, "the honest scope must be stated"
    assert getattr(wf, "techo_fw_version", None) == "1.85", (
        "the certificate's firmware field comes from here — 'firmware: None' "
        "about an image we just flashed is paperwork rot")


def test_techo_raw_port_never_returns_a_glob_literal():
    """An unmatched glob passes through bash as a literal and readlink -f
    prints it with exit 0 — the first version would have handed
    '/dev/serial/by-id/*Nordic*' to rnodeconf as a port whenever other by-id
    devices existed but the T-Echo did not (adversarial review)."""
    from workflows.rtnode_build import _techo_raw_port
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("for f in /dev/serial/by-id/", 0, "")
    assert _techo_raw_port(_techo_wf(c)) == ""


def test_techo_raw_port_refuses_two_candidates():
    """Guard checks the pinned port; a glob that grabs whichever of two nRF
    boards sorts first would provision the wrong one. Refusal beats a guess."""
    from workflows.rtnode_build import _techo_raw_port
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("for f in /dev/serial/by-id/", 0, "/dev/ttyACM1\n/dev/ttyACM2\n")
    assert _techo_raw_port(_techo_wf(c)) == ""


def test_techo_steps_show_honest_names_on_the_checklist():
    """"wifi_onboarding" on a board with no WiFi radio was a lie in the
    checklist; the workflow now renames its rows and narration per build."""
    from workflows.rtnode_build import RTNodeBuildWorkflow
    from node_profile import NodeProfile
    wf = _techo_wf(_TechoMedic())
    assert wf.step_display.get("wifi_onboarding") == "usb_setup"
    assert wf.step_display.get("verify_beacon") == "verify_usb"
    assert "USB" in wf.step_phase_labels["wifi_onboarding"]
    assert "WiFi" not in wf.step_phase_labels["verify_beacon"]
    # and the pio path keeps its true names — the portal onboarding IS WiFi
    pio = RTNodeBuildWorkflow(EmulatedConnection(), NodeProfile(),
                              target="heltec_v4")
    assert pio.step_display == {}


# --- identity over USB (started 2026-08-19) ----------------------------------

def test_verify_techo_reads_the_identity_from_the_boot_log():
    """The firmware prints "[RTNode] identity=… dst=…" when RNS starts (the
    T-Echo build compiles neither WiFi nor FIREWALL_MODE, so the HealthBeacon
    init line the ESP32 parser reads does not exist there). Without this read,
    the certificate said the node had no mesh address and the newborn appeared
    in VITALS as a stranger."""
    from workflows.rtnode_build import _verify_techo
    wf = _techo_wf(_TechoMedic())
    res = _verify_techo(wf)
    assert res.success, res.message
    assert wf.profile.reticulum_identity_hash == (
        "aabbccddeeff00112233445566778899")
    assert wf.techo_dst == "99887766554433221100ffeeddccbbaa"
    assert "identity aabbccddeeff" in res.message, (
        "what was read must be said — a silent capture is unverifiable")


def test_verify_techo_survives_an_older_firmware_without_the_line():
    """A board running an image from before the identity print must not fail
    verification — the absence is recorded honestly, never invented."""
    from workflows.rtnode_build import _verify_techo

    class _OldFirmware(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "techo_bootlog.py" in cmd:
                self.history.append(cmd)
                return 0, "RNS is READY!", ""     # boots, but no identity line
            return super().run(cmd, *a, **k)

    wf = _techo_wf(_OldFirmware())
    res = _verify_techo(wf)
    assert res.success, res.message
    assert wf.profile.reticulum_identity_hash is None
    assert "did not print its identity" in res.message


def test_the_certificate_carries_both_hashes_when_read():
    from workflows.rtnode_build import _verify_techo, birth_certificate
    wf = _techo_wf(_TechoMedic())
    _verify_techo(wf)
    res = birth_certificate(wf)
    assert res.success
    cert = wf.birth_certificate
    assert cert["identity_hash"] == "aabbccddeeff00112233445566778899"
    assert cert["reticulum_address"] == "99887766554433221100ffeeddccbbaa"


def test_an_esp32_certificate_never_gains_a_none_address_row():
    """The cert page prints every field verbatim — a permanently present key
    would put "reticulum_address: None" on every ESP32 certificate."""
    from workflows.rtnode_build import RTNodeBuildWorkflow, birth_certificate
    from node_profile import NodeProfile
    wf = RTNodeBuildWorkflow(EmulatedConnection(), NodeProfile(),
                             target="heltec_v4")
    birth_certificate(wf)
    assert "reticulum_address" not in wf.birth_certificate


def test_the_identity_regex_matches_the_firmware_print():
    """The exact format the firmware emits (RNode_Firmware.ino, after the
    rnstransport destination is created) — one source of truth per side, this
    test the bridge between them."""
    from workflows.rtnode_build import _TECHO_ID_RE
    line = ("[RTNode] identity=0123456789abcdef0123456789abcdef "
            "dst=fedcba9876543210fedcba9876543210")
    m = _TECHO_ID_RE.search("noise before\n" + line + "\nnoise after")
    assert m and m.group(1).startswith("0123") and m.group(2).startswith("fedc")


def test_onboard_techo_sets_the_firmware_hash():
    """Without it, everything downstream quietly dies: hw_ready stays false,
    RNS refuses to start, and eeprom_conf_save silently drops the TNC params
    while rnodeconf reports success from the host side. The first real birth
    shipped exactly that board (2026-08-20); its own boot log named the cause."""
    from workflows.rtnode_build import _onboard_techo
    c = _TechoMedic()
    res = _onboard_techo(_techo_wf(c))
    assert res.success, res.message
    assert "firmware hash set" in res.message
    hashes = [x for x in c.history if "--firmware-hash" in x]
    assert hashes, "the hash write must happen"
    assert "b9a932741fd128ac" in hashes[0], (
        "the hash written must be the one the DEVICE computed")
    order = [i for i, x in enumerate(c.history)
             if "--firmware-hash" in x or (" -T " in x and "rnodeconf" in x)]
    assert len(order) == 2 and "--firmware-hash" in c.history[order[0]], (
        "hash BEFORE params — conf_save silently refuses while hw_ready is "
        "false, which is exactly the shipped failure")


def test_onboard_techo_fails_honestly_on_an_unreadable_hash():
    from workflows.rtnode_build import _onboard_techo

    class _NoHash(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "partition_hashes from_device" in cmd:
                self.history.append(cmd)
                return 1, "", ""
            return super().run(cmd, *a, **k)

    res = _onboard_techo(_techo_wf(_NoHash()))
    assert not res.success
    assert "hash" in res.message.lower()


# --- the Columba-review merge (2026-08-20) -----------------------------------

def test_rak4631_is_an_nrf_target_with_its_own_identities():
    from workflows.rtnode_build import RTNODE_TARGETS
    t = RTNODE_TARGETS["rak4631"]
    assert t.mechanism == "nrf_dfu" and t.verify == "eeprom"
    assert t.provision_args == "--product 10 --model 12 --hwrev 1", (
        "model 0x12 = 779-928 MHz from rnodeconf's own table — 0x11 is the "
        "433 band, and Columba's COMMENTS have the two inverted")
    assert t.usb_app_id and t.usb_boot_id and t.flash_target


def test_verify_fails_on_the_boards_own_not_ready_verdict():
    """rnodeconf prints "checksum correct / signature validated" from EEPROM
    alone — a firmware-hash mismatch kills hw_ready and RNS while rnodeconf
    keeps answering, so the old verify passed the exact shipped-dead state of
    2026-08-20. The board's own boot log outranks every EEPROM read."""
    from workflows.rtnode_build import _verify_techo

    class _DeadBoard(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "techo_bootlog.py" in cmd:
                self.history.append(cmd)
                return 0, ("[ERR] RNS is inoperable because hardware is not "
                           "ready!"), ""
            return super().run(cmd, *a, **k)

    res = _verify_techo(_techo_wf(_DeadBoard()))
    assert not res.success
    assert "hardware not ready" in res.message


def test_verify_fails_on_target_vs_actual_hash_mismatch():
    from workflows.rtnode_build import _verify_techo

    class _Mismatched(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "rnodeconf" in cmd and " -K -L" in cmd:
                self.history.append(cmd)
                return 0, ("The target firmware hash is: " + "a" * 64 + "\n"
                           "The actual firmware hash is: " + "b" * 64), ""
            return super().run(cmd, *a, **k)

    res = _verify_techo(_techo_wf(_Mismatched()))
    assert not res.success
    assert "hash mismatch" in res.message.lower()


def test_onboarding_refuses_an_all_zeros_firmware_hash():
    """Columba logs a warning and writes the zeros anyway — which bricks
    hw_ready on the next boot. We refuse."""
    from workflows.rtnode_build import _onboard_techo

    class _ZeroHash(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "partition_hashes from_device" in cmd:
                self.history.append(cmd)
                return 0, "0" * 64, ""
            return super().run(cmd, *a, **k)

    res = _onboard_techo(_techo_wf(_ZeroHash()))
    assert not res.success
    assert "all-zeros" in res.message


def test_flash_refuses_two_nrf_boards_even_when_one_is_in_dfu():
    """The >1 refusal used to live only on the touch branch; a second board
    with one already in DFU sailed into the Makefile's head-1 glob."""
    from workflows.rtnode_build import _flash_techo

    class _TwoBoards(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "for f in /dev/serial/by-id/" in cmd:
                self.history.append(cmd)
                return 0, "/dev/ttyACM1\n/dev/ttyACM2\n", ""
            if "grep -q 'LilyGo_T-Echo'" in cmd:
                self.history.append(cmd)
                return 1, "", ""          # NOT in bootloader either
            return super().run(cmd, *a, **k)

    res = _flash_techo(_techo_wf(_TwoBoards()), "/dev/ttyACM1")
    assert not res.success
    assert "more than one" in res.message


def test_txp_above_17_is_refused_before_rnodeconf_can_hang():
    """rnodeconf's -T branch validates 0..17 and otherwise falls into an
    interactive input() — under our subprocess timeout that dies as a garbled
    failure with nothing saying why. SX1262 boards legally reach 22 dBm, so an
    operator raising the Settings default arms this."""
    import pytest
    from workflows.radio_params import set_params_command
    from node_profile import RadioConfig
    cfg = RadioConfig()
    cfg.tx_power_dbm = 18
    with pytest.raises(ValueError, match="17"):
        set_params_command("/dev/ttyACM1", cfg)


def test_the_dfu_pid_table_knows_all_three_bootloaders():
    """One PID mis-read a RAK or T114 sitting in DFU as running. The set is
    Columba's device-verified table cross-checked on our bench (our T-Echo
    has presented BOTH 0x0029 and 0x002a)."""
    from workflows.rnode_flash import NRF_DFU_PIDS
    assert set(NRF_DFU_PIDS) == {"0029", "002a", "0071"}


def test_bootloader_state_is_decided_by_pid_not_name():
    """The RAK4631's app and bootloader by-id names differ by ONE capital
    letter (RAKwireless vs RAKWireless, observed on the bench 2026-08-20); the
    first birth read the running app as already-in-bootloader, skipped the
    touch, and aimed DFU at an application. The PID cannot be spoofed by a
    naming coincidence — a running app (8029) must get the touch."""
    from workflows.rtnode_build import _flash_techo
    c = _TechoMedic(start_in_bootloader=False)
    res = _flash_techo(_techo_wf(c), "/dev/ttyACM1")
    assert res.success, res.message
    assert any("udevadm info" in x for x in c.history), (
        "the bootloader decision must consult the PID")
    assert any("techo_touch.py" in x for x in c.history), (
        "a running app (PID 8029) must be touched into the bootloader")


def test_rak4631_finds_its_stock_app_before_first_flash():
    """A factory RAK presents 'RAKwireless_WisBlock_RAK4631' — the renamed
    RTNode identity doesn't exist yet, so the port resolver must know the
    stock spelling or a virgin board is invisible to its own first birth."""
    from workflows.rtnode_build import RTNODE_TARGETS, _techo_raw_port
    from workflows.rtnode_build import RTNodeBuildWorkflow
    from node_profile import NodeProfile
    t = RTNODE_TARGETS["rak4631"]
    assert "RAKwireless_WisBlock" in t.usb_stock_ids
    assert t.usb_boot_id == "RAKWireless_WisBlock", (
        "capital W = the bootloader, observed not guessed")
    wf = RTNodeBuildWorkflow(_TechoMedic(), NodeProfile(), target="rak4631")
    cmds = []
    class _Spy(_TechoMedic):
        def run(self, cmd, *a, **k):
            cmds.append(cmd)
            return super().run(cmd, *a, **k)
    wf = RTNodeBuildWorkflow(_Spy(), NodeProfile(), target="rak4631")
    _techo_raw_port(wf)
    glob_cmd = next(c for c in cmds if "for f in /dev/serial/by-id/" in c)
    assert "RAKwireless_WisBlock" in glob_cmd and "RAK4631_RTNode-2400" in glob_cmd


def test_the_pipeline_waits_for_a_kiss_answer_not_just_enumeration():
    """USB enumeration is not readiness: a first-ever boot formats LittleFS in
    setup() — tens of seconds with the port present and silent — and rnodeconf
    gives up in ~3s ("RNode did not respond", RAK4631 maiden birth). Flash and
    onboarding must both hold for the firmware's own CMD_DETECT answer."""
    from workflows.rtnode_build import _flash_techo, _onboard_techo
    c = _TechoMedic()
    assert _flash_techo(_techo_wf(c), "/dev/ttyACM1").success
    assert any("kiss_detect.py" in x for x in c.history), (
        "flash must not report 'talking' without a KISS answer")
    c2 = _TechoMedic()
    assert _onboard_techo(_techo_wf(c2)).success
    probes = [x for x in c2.history if "kiss_detect.py" in x]
    assert len(probes) >= 2, (
        "onboarding probes before -r AND after its trailing reset")


def test_a_board_that_never_answers_fails_honestly():
    from workflows.rtnode_build import _flash_techo

    class _Mute(_TechoMedic):
        def run(self, cmd, *a, **k):
            if "kiss_detect.py" in cmd:
                self.history.append(cmd)
                return 1, "", "detect: no answer"
            return super().run(cmd, *a, **k)

    res = _flash_techo(_techo_wf(_Mute()), "/dev/ttyACM1")
    assert not res.success
    assert "never answered" in res.message


# ---- hw_serial: the identifier that survives a reflash ----------------------

def test_hw_serial_from_port_reads_nrf_style_by_id():
    from workflows.rtnode_build import hw_serial_from_port
    c = conn(port="/dev/ttyACM1")
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0,
        "lrwxrwxrwx 1 root root 13 Aug 21 21:21 "
        "usb-Heltec_T114_RTNode-2400_1114000000000001-if00 -> ../../ttyACM1", ""))
    w = wf(c)
    w.profile.connection_port = "/dev/ttyACM1"
    assert hw_serial_from_port(w) == "1114000000000001"


def test_hw_serial_from_port_reads_esp32_mac_style():
    from workflows.rtnode_build import hw_serial_from_port
    c = conn(port="/dev/ttyACM0")
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0,
        "lrwxrwxrwx 1 root root 13 Aug 21 10:00 "
        "usb-Espressif_USB_JTAG_serial_debug_unit_02:00:00:02:00:02-if00 "
        "-> ../../ttyACM0", ""))
    w = wf(c)
    w.profile.connection_port = "/dev/ttyACM0"
    assert hw_serial_from_port(w) == "02:00:00:02:00:02"


def test_hw_serial_from_port_empty_when_no_by_id():
    from workflows.rtnode_build import hw_serial_from_port
    w = wf(conn(port="/dev/ttyACM1"))
    w.profile.connection_port = "/dev/ttyACM1"
    assert hw_serial_from_port(w) == ""


def test_birth_certificate_carries_hw_serial():
    """The cert is how the serial reaches the kin roster — a rebirth of the
    same physical board uses it to retire the previous identity's rows."""
    c = conn(port="/dev/ttyACM1")
    c.rules.insert(0, ("ls -l /dev/serial/by-id/", 0,
        "lrwxrwxrwx 1 root root 13 Aug 21 21:21 "
        "usb-Heltec_T114_RTNode-2400_1114000000000001-if00 -> ../../ttyACM1", ""))
    w = wf(c)
    w.run_all()
    assert w.birth_certificate.get("hw_serial") == "1114000000000001"
