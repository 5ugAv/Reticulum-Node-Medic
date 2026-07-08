import os

import pytest

from node_profile import NodeProfile, NodeHardware
from transport.connection import EmulatedConnection
from workflows.rtnode_build import (
    RTNodeBuildWorkflow,
    RTNODE_BUILD_ENV,
)

BEACON_LINE = (
    "[HealthBeacon] announce dst=eabdd142596bcae888242ec1b172d566 "
    "data=010000002400c7cc053b3f000602")

EXPECTED_STEPS = [
    "detect_heltec_v4",
    "flash_firmware",
    "wifi_onboarding",   # must precede verify_beacon: a fresh board is silent
    "verify_beacon",     # until onboarded + rebooted
    "birth_certificate",
]


def conn(port="/dev/cu.usbmodem2101", flash_code=0, beacon=BEACON_LINE):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("^ls /dev/cu", 0 if port else 1, port, ""))
    c.rules.insert(0, ("pio run", flash_code, "SUCCESS" if flash_code == 0 else "err", ""))
    c.rules.insert(0, ("rnm-serial-capture", 0, beacon, ""))
    return c


def wf(c=None, profile=None):
    return RTNodeBuildWorkflow(c or conn(), profile or NodeProfile())


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


def test_detect_fails_when_no_board():
    w = wf(conn(port=""))
    r = w.steps[0][1](w)
    assert r.success is False


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
    assert w.profile.reticulum_identity_hash == "eabdd142596bcae888242ec1b172d566"
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


def test_birth_certificate_summarises_node():
    w = wf()
    w.run_all()
    cert = w.birth_certificate
    assert cert["board"] == "Heltec32 V4"
    assert cert["firmware"] == "0.6.2"
    assert cert["identity_hash"] == "eabdd142596bcae888242ec1b172d566"
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
