"""Cross-language wire-contract test: the RTNode-2400 firmware packer
(`firmware/rtnode-2400/HealthBeaconPack.h`, pure stdint) must produce the exact
same bytes as the tool's Python encoder (`monitor.health_beacon.encode`). Both
v1 and v2 are locked to golden vectors so the firmware and the decoder can never
drift apart silently.

The packer header is compiled off-device with g++ (it has no Arduino/RNS deps).
Skipped cleanly where g++ is unavailable (e.g. minimal CI images)."""

import os
import shutil
import subprocess

import pytest

from monitor.health_beacon import encode

FW_DIR = os.path.join(os.path.dirname(__file__), os.pardir,
                      "firmware", "rtnode-2400")

HARNESS = r"""
#include <cstdio>
#include "HealthBeaconPack.h"
int main() {
    uint8_t v1[HEALTH_BEACON_LEN];
    health_pack_beacon(v1, 7200, 140, -62, 0,
        true,true,true,true,true,true,false,false, 0x3F, 0,6,2);
    for (int i=0;i<HEALTH_BEACON_LEN;i++) printf("%02x", v1[i]);
    printf("\n");

    uint8_t v2[HEALTH_BEACON_LEN_V2];
    uint8_t pflags = HB_PWR_ON_BATTERY | HB_PWR_CHARGING | HB_PWR_SOLAR;
    health_pack_beacon_v2(v2, 7200, 140, -62, 0,
        true,true,true,true,true,true,false,false, 0x3F, 0,7,0,
        3940, 78, pflags, 6, -92);
    for (int i=0;i<HEALTH_BEACON_LEN_V2;i++) printf("%02x", v2[i]);
    printf("\n");
    return 0;
}
"""


def _firmware_hex(tmp_path):
    src = tmp_path / "harness.cpp"
    src.write_text(HARNESS)
    exe = tmp_path / "harness"
    subprocess.run(
        ["g++", "-std=c++11", "-I", FW_DIR, "-o", str(exe), str(src)],
        check=True, capture_output=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
    return out.stdout.split()


pytestmark = pytest.mark.skipif(shutil.which("g++") is None,
                                reason="g++ not available to compile the firmware packer")


def test_firmware_packer_matches_python_v1_and_v2(tmp_path):
    fw_v1, fw_v2 = _firmware_hex(tmp_path)

    py_v1 = encode(7200, 140, -62, 0, wifi_up=True, lora_up=True,
                   tcp_backbone_up=True, local_tcp_server_up=True, wdt_armed=True,
                   psram=True, fault=False, board_id=0x3F, fw=(0, 6, 2)).hex()
    py_v2 = encode(7200, 140, -62, 0, wifi_up=True, lora_up=True,
                   tcp_backbone_up=True, local_tcp_server_up=True, wdt_armed=True,
                   psram=True, fault=False, board_id=0x3F, fw=(0, 7, 0),
                   battery_mv=3940, battery_pct=78, on_battery=True, charging=True,
                   on_solar=True, lora_snr_db=6, lora_rssi_dbm=-92).hex()

    assert fw_v1 == py_v1, f"v1 firmware {fw_v1} != python {py_v1}"
    assert fw_v2 == py_v2, f"v2 firmware {fw_v2} != python {py_v2}"
    assert len(fw_v1) == 28   # 14 bytes
    assert len(fw_v2) == 40   # 20 bytes
