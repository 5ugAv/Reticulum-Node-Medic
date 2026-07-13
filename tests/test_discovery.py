import pytest

from monitor.discovery import discover_hosts, discover_nodes, local_subnet
from monitor.http_status import NodeStatus


def test_local_subnet_from_ip_output():
    run = lambda cmd: "2: wlan0    inet 192.168.1.217/24 brd 192.168.1.255 ..."
    assert local_subnet(run) == "192.168.1"


def test_local_subnet_ignores_loopback():
    run = lambda cmd: "inet 127.0.0.1/8\ninet 10.4.5.6/24"
    assert local_subnet(run) == "10.4.5"


def test_discover_hosts_parses_probe_output():
    # the injected runner returns the IPs whose /status had "fork"
    run = lambda cmd: "192.168.1.51\n192.168.1.180\n\n"
    assert discover_hosts(run, "192.168.1") == ["192.168.1.51", "192.168.1.180"]


def test_discover_hosts_command_probes_status_with_bounded_concurrency():
    seen = {}
    def run(cmd):
        seen["cmd"] = cmd
        return ""
    discover_hosts(run, "192.168.1", timeout=4, concurrency=16)
    assert "/status" in seen["cmd"]
    assert "RTNode" in seen["cmd"]                 # identifies RTNode /status
    assert "-m4" in seen["cmd"]
    assert "xargs -P 16" in seen["cmd"]            # bounded parallelism
    assert "seq 1 254" in seen["cmd"]


def test_discover_hosts_ignores_non_ip_noise():
    run = lambda cmd: "curl: (7) failed\n192.168.1.180\ngarbage line\n"
    assert discover_hosts(run, "192.168.1") == ["192.168.1.180"]


def test_discover_nodes_polls_each_found_host():
    run = lambda cmd: "192.168.1.180\n192.168.1.51\n"
    def fake_poll(host):
        return NodeStatus(reachable=True, status="ok", node_name=f"n-{host[-3:]}")
    found = discover_nodes(run, subnet="192.168.1", poll=fake_poll)
    assert [h for h, _ in found] == ["192.168.1.51", "192.168.1.180"]
    assert all(ns.reachable for _, ns in found)


def test_discover_nodes_resolves_subnet_when_omitted():
    def run(cmd):
        if "curl" in cmd:            # discover_hosts probe
            return "192.168.5.9\n"
        return "inet 192.168.5.217/24"   # local_subnet
    found = discover_nodes(run, poll=lambda h: NodeStatus(True, "ok"))
    assert [h for h, _ in found] == ["192.168.5.9"]


def test_discover_nodes_empty_when_no_subnet():
    assert discover_nodes(lambda cmd: "", poll=lambda h: None) == []
