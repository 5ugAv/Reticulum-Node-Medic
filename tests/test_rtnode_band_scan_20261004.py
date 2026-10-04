"""2026-10-04: a split-band network was refused as '5 GHz only' mid-build because
the band check read NetworkManager's scan snapshot, which thins to the connected AP.
The check now asks the driver (iw), and NM's list is only the fallback."""
import pytest

from workflows import rtnode_portal as rp

HOME = "HomeNet_5g"

IW_SPLIT = """BSS aa:aa:aa:aa:aa:01(on wlan0) -- associated
\tfreq: 5200
\tsignal: -52.00 dBm
\tSSID: HomeNet_5g
BSS aa:aa:aa:aa:aa:02(on wlan0)
\tfreq: 2447
\tsignal: -40.00 dBm
\tSSID: HomeNet_5g
BSS bb:bb:bb:bb:bb:01(on wlan0)
\tfreq: 2412
\tSSID: Neighbour
BSS cc:cc:cc:cc:cc:01(on wlan0)
\tfreq: 2437
\tSSID: \\x00\\x00\\x00
BSS dd:dd:dd:dd:dd:01(on wlan0)
\tfreq: 5745
\tSSID: FiveOnly
"""

IW_5G_ONLY = """BSS aa:aa:aa:aa:aa:01(on wlan0) -- associated
\tfreq: 5200
\tSSID: HomeNet_5g
BSS bb:bb:bb:bb:bb:01(on wlan0)
\tfreq: 2412
\tSSID: Neighbour
"""

NM_ONLY_CONNECTED = "HomeNet_5g:5200 MHz\n"


def fake(iw="", nm="", status="wlan0:wifi\nlo:loopback\n"):
    calls = []

    def run(argv):
        calls.append(list(argv))
        if argv[:3] == ["sudo", "-n", "iw"]:
            return iw
        if "status" in argv:
            return status
        return nm
    run.calls = calls
    return run


def test_the_stale_networkmanager_list_no_longer_decides():
    run = fake(iw=IW_SPLIT, nm=NM_ONLY_CONNECTED)
    assert sorted(rp.ssid_bands_mhz(HOME, run=run)) == [2447, 5200]
    assert rp.esp32_can_join(HOME, run=run) is True


def test_a_network_that_really_is_5ghz_only_is_still_refused_with_alternatives():
    run = fake(iw=IW_5G_ONLY, nm=NM_ONLY_CONNECTED)
    assert rp.esp32_can_join(HOME, run=run) is False
    assert rp.visible_24ghz_ssids(run=run) == ["Neighbour"]


def test_hidden_and_escaped_names_are_handled():
    rows = rp._parse_iw_bss(IW_SPLIT)
    assert (HOME, 5200) in rows and (HOME, 2447) in rows and ("Neighbour", 2412) in rows
    assert rp._iw_ssid("caf\\xc3\\xa9") == "café"
    assert "" not in rp.visible_24ghz_ssids(run=fake(iw=IW_SPLIT))


def test_iw_unusable_falls_back_to_the_networkmanager_list_and_fails_open():
    split = "HomeNet_5g:5200 MHz\nHomeNet_5g:2447 MHz\n"
    assert rp.esp32_can_join(HOME, run=fake(iw="", nm=split)) is True
    assert rp.esp32_can_join(HOME, run=fake(iw="", nm="")) is True      # saw nothing -> never block
    assert rp.esp32_can_join(HOME, run=fake(iw="", nm=NM_ONLY_CONNECTED)) is False


def test_a_busy_driver_is_asked_twice(monkeypatch):
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    outs = iter(["command failed: Device or resource busy (-16)", IW_SPLIT])
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[:3] == ["sudo", "-n", "iw"]:
            return next(outs)
        return "wlan0:wifi\n" if "status" in argv else ""
    assert rp.esp32_can_join(HOME, run=run) is True
    assert sum(1 for c in calls if c[:3] == ["sudo", "-n", "iw"]) == 2


def test_the_scan_goes_to_the_wifi_device_nmcli_names():
    run = fake(iw=IW_SPLIT, status="eth0:ethernet\nwlp1s0:wifi\np2p-dev-wlp1s0:wifi-p2p\n")
    rp.ssid_bands_mhz(HOME, run=run)
    assert ["sudo", "-n", "iw", "dev", "wlp1s0", "scan"] in run.calls
