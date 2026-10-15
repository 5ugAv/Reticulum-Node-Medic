"""Medic system-health self-diagnose checks (disk/service/temp/power/wifi)."""

from monitor.self_diagnose import (
    check_disk_space, check_service, check_cpu_temp, check_throttled, check_wifi,
    check_clock_sync, check_rns_responding, check_lxmd,
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
    assert check_disk_space("").severity == SEV_WARN        # unreadable -> unknown, not a green tick


def test_service_check():
    assert check_service("rnsd", True).severity == SEV_OK
    down = check_service("rnsd", False)
    assert down.severity == SEV_CRIT and down.fix == "restart_rnsd"
    assert check_service("lxmd", False, critical=False).severity == SEV_WARN


def test_cpu_temp_levels():
    assert check_cpu_temp("temp=48.3'C").severity == SEV_OK
    assert check_cpu_temp("temp=78.0'C").severity == SEV_WARN
    assert check_cpu_temp("temp=85.1'C").severity == SEV_CRIT
    assert check_cpu_temp("").severity == SEV_WARN          # unreadable -> unknown, not "cool"


def test_throttled_bits():
    assert check_throttled("throttled=0x0").severity == SEV_OK
    assert check_throttled("throttled=0x50000").severity == SEV_WARN   # occurred
    assert check_throttled("throttled=0x1").severity == SEV_CRIT       # under-volt now
    assert check_throttled("throttled=0x4").severity == SEV_CRIT       # throttled now
    # THE browning-out case: can't read the throttle register -> unknown, WARN,
    # never a green ":) healthy" on a medic that may be under-volting right now.
    assert check_throttled("").severity == SEV_WARN


def test_wifi_never_critical():
    assert check_wifi("*:78:HomeNet").severity == SEV_OK
    weak = check_wifi("*:25:HomeNet")
    assert weak.severity == SEV_WARN and weak.data["signal_pct"] == 25
    # picks the in-use AP (starts with *), ignores other scanned networks
    assert check_wifi(":90:OtherNet\n*:80:HomeNet").severity == SEV_OK
    assert check_wifi("").severity == SEV_OK                 # not connected -> no alarm
    # connected AP but the signal % won't parse -> unknowable, WARN (not a
    # green tick); being offline stays OK, this is a different branch.
    assert check_wifi("*:notanumber:HomeNet").severity == SEV_WARN
    assert "HomeNet" in check_wifi("*:80:HomeNet").detail    # names the AP


def test_clock_sync():
    good = 1_704_070_000.0                                   # a real 2024+ time
    assert check_clock_sync("NTPSynchronized=yes", good).severity == SEV_OK
    assert check_clock_sync("NTPSynchronized=no", good).severity == SEV_WARN
    # a bogus pre-2024 clock (no RTC + power loss) is critical, offers a fix
    bad = check_clock_sync("NTPSynchronized=yes", 50000.0)
    assert bad.severity == SEV_CRIT and bad.fix == "sync_clock"


def test_rns_responding():
    ok = check_rns_responding("Shared Instance[37428]\n  Status  : Up")
    assert ok.severity == SEV_OK and ok.data["interfaces_up"] == 1
    down = check_rns_responding("Could not connect to a local or shared instance")
    assert down.severity == SEV_CRIT and down.fix == "restart_rnsd"
    assert check_rns_responding("").severity == SEV_CRIT      # empty = not answering
    assert check_rns_responding("Reticulum up but nothing").severity == SEV_WARN  # no iface Up


def test_lxmd_is_mode_aware():
    assert check_lxmd(True, wants_propagation=True).severity == SEV_OK
    assert check_lxmd(True, wants_propagation=False).severity == SEV_OK
    # off is fine in backpack / transport-only...
    assert check_lxmd(False, wants_propagation=False).severity == SEV_OK
    # ...but a warning when propagation is actually wanted
    warn = check_lxmd(False, wants_propagation=True)
    assert warn.severity == SEV_WARN and warn.fix == "restart_lxmd"


# ---- the screen panel's history (2026-10-15: Node Medic 2 came up dark) ------

def _boot(label, inits, own=0):
    kernel = "\n".join("drm-rp1-dsi 1f00130000.dsi: [drm] rp1dsi: Nominal Byte clock 125000000"
                       for _ in range(inits))
    marks = "\n".join("re-initialised at start: Panel DSI-2 re-initialised." for _ in range(own))
    return (label, kernel, marks)


def test_panel_history_counts_only_cures_done_by_hand():
    from monitor.self_diagnose import check_panel_reinits, SEV_OK, SEV_WARN
    # boot init only: nothing by hand
    f = check_panel_reinits([_boot("2026-10-14 23:54", 1)])
    assert f.severity == SEV_OK and "this boot" in f.detail and "not kept" in f.detail
    # boot init + the medic's own start re-init: still nothing by hand
    f = check_panel_reinits([_boot("b", 2, own=1), _boot("a", 2, own=1)])
    assert f.severity == SEV_OK and "over the 2 boots" in f.detail
    # boot init + one hand cure (Node Medic 2 on 2026-10-15)
    f = check_panel_reinits([_boot("2026-10-15 00:59", 1), _boot("2026-10-14 23:54", 2)])
    assert f.severity == SEV_WARN and f.data["by_hand"] == 1
    assert "1 time(s)" in f.detail and "2026-10-14 23:54" in f.detail
    # own re-init AND a hand cure in the same boot: the hand cure counts
    f = check_panel_reinits([_boot("c", 3, own=1)])
    assert f.severity == SEV_WARN and f.data["by_hand"] == 1
    # no journal at all
    f = check_panel_reinits([])
    assert f.severity == SEV_WARN and "Cannot read the journal" in f.detail
