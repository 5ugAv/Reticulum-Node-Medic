"""Medic system-health self-diagnose checks (disk/service/temp/power/wifi)."""

from monitor.self_diagnose import (
    check_disk_space, check_service, check_cpu_temp, check_throttled, check_wifi,
    SEV_OK, SEV_WARN, SEV_CRIT)

_DF = ("Filesystem     1024-blocks    Used Available Capacity Mounted on\n"
       "/dev/root         30218100 {used}  {avail}      {pct}% /")


def _df(pct):
    return _DF.format(used=1, avail=1, pct=pct)


def test_disk_space_levels():
    assert check_disk_space(_df(50)).severity == SEV_OK
    assert check_disk_space(_df(88)).severity == SEV_WARN
    d = check_disk_space(_df(97))
    assert d.severity == SEV_CRIT and d.fix == "free_space" and d.data["pct"] == 97
    assert check_disk_space("").severity == SEV_OK          # unreadable -> no alarm


def test_service_check():
    assert check_service("rnsd", True).severity == SEV_OK
    down = check_service("rnsd", False)
    assert down.severity == SEV_CRIT and down.fix == "restart_rnsd"
    assert check_service("lxmd", False, critical=False).severity == SEV_WARN


def test_cpu_temp_levels():
    assert check_cpu_temp("temp=48.3'C").severity == SEV_OK
    assert check_cpu_temp("temp=78.0'C").severity == SEV_WARN
    assert check_cpu_temp("temp=85.1'C").severity == SEV_CRIT
    assert check_cpu_temp("").severity == SEV_OK


def test_throttled_bits():
    assert check_throttled("throttled=0x0").severity == SEV_OK
    assert check_throttled("throttled=0x50000").severity == SEV_WARN   # occurred
    assert check_throttled("throttled=0x1").severity == SEV_CRIT       # under-volt now
    assert check_throttled("throttled=0x4").severity == SEV_CRIT       # throttled now
    assert check_throttled("").severity == SEV_OK


def test_wifi_never_critical():
    assert check_wifi("\tsignal: -55 dBm").severity == SEV_OK
    weak = check_wifi("\tsignal: -85 dBm")
    assert weak.severity == SEV_WARN and weak.data["rssi_dbm"] == -85
    assert check_wifi("Not connected.").severity == SEV_OK
    assert check_wifi("").severity == SEV_OK
