import pytest

from monitor.service import MonitorService
from monitor.http_status import NodeStatus


def ns(name, status="ok", reachable=True, rssi=-60):
    return NodeStatus(reachable=reachable, status=status, node_name=name,
                      wifi_rssi_dbm=rssi, lora_online=True)


# a fake LAN: subnet sweep returns two hosts; poll returns per-host status
STATUSES = {"192.168.1.180": ns("MEDIC-TEST"), "192.168.1.51": ns("FAITH", "warn", rssi=-76)}


def fake_run(cmd):
    if "curl" in cmd and "xargs" in cmd:          # discover_hosts probe
        return "192.168.1.51\n192.168.1.180\n"
    return "inet 192.168.1.217/24"                # local_subnet


def fake_poll(host):
    return STATUSES.get(host, NodeStatus(reachable=False, status="unreachable"))


def clock(t=[1000.0]):
    return t[0]


def test_discover_registers_and_remembers_hosts():
    svc = MonitorService(run=fake_run, poll=fake_poll, now=clock)
    n = svc.discover()
    assert n == 2
    assert set(svc.hosts.values()) == {"192.168.1.180", "192.168.1.51"}
    names = {r.name for r in svc.dashboard()}
    assert names == {"MEDIC-TEST", "FAITH"}


def test_dashboard_alert_first_and_status_from_http():
    svc = MonitorService(run=fake_run, poll=fake_poll, now=clock)
    svc.cycle(rediscover=True)
    dash = svc.dashboard()
    # FAITH is warn (rssi -76), MEDIC-TEST ok -> warn sorts before ok
    assert [r.name for r in dash] == ["FAITH", "MEDIC-TEST"]
    assert svc.registry.summary(clock()) == {"ok": 1, "warn": 1, "alert": 0, "unknown": 0}


def test_key_is_stable_across_ip_change():
    svc = MonitorService(run=fake_run, poll=fake_poll, now=clock)
    assert svc.node_key(ns("MEDIC-TEST"), "192.168.1.180") == "rtnode:MEDIC-TEST"
    # same node at a new DHCP address maps to the same key
    assert svc.node_key(ns("MEDIC-TEST"), "192.168.1.99") == "rtnode:MEDIC-TEST"


def test_poll_cycle_updates_known_hosts_only():
    calls = []
    def counting_poll(host):
        calls.append(host)
        return STATUSES.get(host, NodeStatus(reachable=False, status="unreachable"))
    svc = MonitorService(run=fake_run, poll=counting_poll, now=clock)
    svc.discover()
    calls.clear()
    svc.poll_cycle()
    assert sorted(calls) == ["192.168.1.180", "192.168.1.51"]   # no re-sweep


def test_unreachable_node_stops_refreshing_last_seen():
    times = [1000.0]
    svc = MonitorService(run=fake_run, poll=fake_poll, now=lambda: times[0])
    svc.discover()
    rec = svc.registry.get("rtnode:MEDIC-TEST")
    assert rec.last_seen == 1000.0
    # node goes offline; a later poll returns unreachable
    STATUSES["192.168.1.180"] = NodeStatus(reachable=False, status="unreachable")
    times[0] = 2000.0
    svc.poll_cycle()
    assert rec.last_seen == 1000.0                # not refreshed
    STATUSES["192.168.1.180"] = ns("MEDIC-TEST")  # restore for other tests


def test_run_rediscovers_on_schedule():
    sweeps = [0]
    def counting_run(cmd):
        if "xargs" in cmd:
            sweeps[0] += 1
        return fake_run(cmd)
    svc = MonitorService(run=counting_run, poll=fake_poll, now=clock)
    svc.run(cycles=5, discover_every=2, sleep=lambda s: None)
    # cycles 0,2,4 rediscover -> 3 sweeps
    assert sweeps[0] == 3
