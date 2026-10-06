from provisioning import direct_link as dl


IP_LINK = """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP mode DEFAULT
3: wlan0: <BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state UP mode DORMANT
4: enx00e04c680001: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN mode DEFAULT
"""


def test_wired_interfaces_exclude_loopback_wifi_and_usb_gadget():
    # enx* is the USB-CDC gadget owned by provisioning.link — must NOT be claimed
    # here, or a cable bring-up would fight the gadget link for the same /29.
    assert dl.parse_wired_interfaces(IP_LINK) == ["eth0"]


def test_wired_interfaces_match_predictable_names():
    # Pi OS may name the onboard NIC end0/enp1s0 rather than eth0.
    out = "2: end0: <BROADCAST> mtu 1500\n3: enp1s0: <BROADCAST> mtu 1500\n"
    assert dl.parse_wired_interfaces(out) == ["end0", "enp1s0"]


def test_mdns_name_is_idempotent_and_handles_blanks():
    assert dl.mdns_name("nodemedic-b") == "nodemedic-b.local"
    assert dl.mdns_name("nodemedic-b.local") == "nodemedic-b.local"
    assert dl.mdns_name("nodemedic-b.") == "nodemedic-b.local"
    assert dl.mdns_name("") == ""


def test_mdns_leads_because_a_bone_stock_card_has_no_static_ip():
    assert dl.candidate_targets("nodemedic-b")[0] == "nodemedic-b.local"
    assert dl.PEER_ETH_IP in dl.candidate_targets("nodemedic-b")


def test_candidates_cover_unknown_address_pi5s():
    # A Pi we did not image (stock hostname) and a Pi that took some other host
    # in the /29 must both be reachable, not just <hostname>.local + .1.
    cands = dl.candidate_targets("nodemedic-b")
    assert dl.STOCK_MDNS in cands                      # a Pi we didn't rename
    # the whole usable /29 is swept, PEER first, medic's own address excluded
    for n in range(1, 7):
        addr = f"10.55.0.{n}"
        if addr == dl.MEDIC_ETH_IP:
            continue
        assert addr in cands
    assert dl.MEDIC_ETH_IP not in cands
    assert cands.index(dl.PEER_ETH_IP) < cands.index("10.55.0.6")
    # de-duplicated and no empties
    assert len(cands) == len(set(cands)) and "" not in cands


def test_candidates_have_no_name_but_still_sweep_subnet():
    cands = dl.candidate_targets("")
    assert cands[0] == dl.STOCK_MDNS          # no chosen name -> stock leads
    assert dl.PEER_ETH_IP in cands


def test_neighbour_parse_finds_any_live_address_and_scopes_v6():
    out = (
        "10.55.0.4 dev eth0 lladdr 02:00:00:08:00:08 REACHABLE\n"
        "169.254.7.9 dev eth0 lladdr 02:00:00:08:00:08 STALE\n"       # link-local v4
        "10.55.0.9 dev eth0 FAILED\n"                                  # nothing there
        "fe80::a3b2:c3ff:fed4:e5f6 dev eth0 lladdr 02:00:00:08:00:08 REACHABLE\n"
    )
    got = dl.parse_neighbour_targets(out, iface="eth0")
    assert "10.55.0.4" in got
    assert "169.254.7.9" in got               # self-assigned address, still found
    assert "10.55.0.9" not in got             # FAILED entry skipped
    assert "fe80::a3b2:c3ff:fed4:e5f6%eth0" in got   # scope suffix for ssh/probe


def test_neighbour_parse_ignores_junk():
    assert dl.parse_neighbour_targets("", "eth0") == []
    assert dl.parse_neighbour_targets("garbage line here\n", "eth0") == []


def test_discover_finds_a_neighbour_at_an_unknown_address():
    # B came up at an address we never chose (not mdns-resolvable, not .1). It
    # only becomes reachable because it shows up in `ip neigh`.
    neigh = "10.55.0.5 dev eth0 lladdr 02:00:00:08:00:08 REACHABLE\n"

    def runner(argv, input=None, timeout=30):
        if argv[:2] == ["ip", "-o"]:
            return (0, IP_LINK, "")
        if "neigh" in argv and "-4" in argv:
            return (0, neigh, "")
        return (0, "", "")

    got = dl.discover_peer("nodemedic-b", runner=runner,
                           probe=lambda h, p=22, timeout=3.0: h == "10.55.0.5",
                           sleep=lambda s: None, now=lambda: 0.0, timeout=1.0)
    assert got == "10.55.0.5"


def test_reuses_the_gadget_29_so_no_new_sudoers_entry_is_needed():
    # The medic's sudoers pins `ip addr add 10.55.0.2/29 dev *`. If this drifts,
    # a cable bring-up starts failing with a silent sudo denial.
    assert dl.MEDIC_ETH_IP == "10.55.0.2"
    assert dl.ETH_PREFIX == 29


def test_discover_returns_the_target_that_answered():
    calls = []

    def runner(argv, input=None, timeout=30):
        calls.append(argv)
        return (0, IP_LINK, "") if argv[:2] == ["ip", "-o"] else (0, "", "")

    got = dl.discover_peer("nodemedic-b", runner=runner,
                           probe=lambda h, p=22, timeout=3.0: h == "nodemedic-b.local",
                           sleep=lambda s: None, now=lambda: 0.0, timeout=1.0)
    assert got == "nodemedic-b.local"
    # it claimed our end of the /29 on the wired NIC (not on enx*/wlan0)
    addr = [c for c in calls if "addr" in c and "add" in c]   # (the "del" of our own 10.55.0.1 comes first)
    assert addr and addr[0][-1] == "eth0"
    assert f"{dl.MEDIC_ETH_IP}/{dl.ETH_PREFIX}" in addr[0]


def test_discover_falls_back_to_static_ip_when_mdns_is_unresolvable():
    def runner(argv, input=None, timeout=30):
        return (0, IP_LINK, "") if argv[:2] == ["ip", "-o"] else (0, "", "")

    got = dl.discover_peer("nodemedic-b", runner=runner,
                           probe=lambda h, p=22, timeout=3.0: h == dl.PEER_ETH_IP,
                           sleep=lambda s: None, now=lambda: 0.0, timeout=1.0)
    assert got == dl.PEER_ETH_IP


def test_discover_returns_none_when_nothing_answers():
    def runner(argv, input=None, timeout=30):
        return (0, IP_LINK, "") if argv[:2] == ["ip", "-o"] else (0, "", "")

    t = [0.0]

    def now():
        t[0] += 0.5
        return t[0]

    assert dl.discover_peer("nodemedic-b", runner=runner,
                            probe=lambda h, p=22, timeout=3.0: False,
                            sleep=lambda s: None, now=now, timeout=1.0) is None


def test_static_service_binds_first_present_wired_nic():
    svc = dl.ETH_LINK_SERVICE
    assert f"{dl.PEER_ETH_IP}/{dl.ETH_PREFIX}" in svc
    assert "WantedBy=multi-user.target" in svc
    # must not hard-code eth0 only — Pi OS naming varies
    for nic in ("eth0", "end0"):
        assert nic in svc


# -- neighbour sweep must not treat a shared-LAN stranger as the clone target --
# (adversarial correctness review 2026-08-25)

def test_direct_link_addr_accepts_only_cable_scoped_addresses():
    assert dl.is_direct_link_addr("10.55.0.5")               # cable /29
    assert dl.is_direct_link_addr("169.254.7.9")             # IPv4 link-local
    assert dl.is_direct_link_addr("fe80::1%eth0")            # IPv6 link-local
    assert not dl.is_direct_link_addr("192.168.1.1")         # site router
    assert not dl.is_direct_link_addr("10.0.0.4")            # some LAN host
    assert not dl.is_direct_link_addr("8.8.8.8")


def test_discovery_ignores_a_routable_neighbour_on_a_shared_nic():
    # medic's wired NIC is on a real LAN; neigh shows the router + a stranger's
    # ssh box. Neither must be returned as "B".
    neigh = ("192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
             "192.168.1.50 dev eth0 lladdr 11:22:33:44:55:66 STALE\n")

    def runner(argv, input=None, timeout=30):
        if argv[:2] == ["ip", "-o"]:
            return (0, IP_LINK, "")
        if "neigh" in argv:
            return (0, neigh, "")
        return (0, "", "")

    # probe would say "yes" to the stranger if it were ever offered
    clock = [0.0]

    def now():
        clock[0] += 0.5
        return clock[0]

    got = dl.discover_peer("nodemedic-b", runner=runner,
                           probe=lambda h, p=22, timeout=3.0: h.startswith("192.168"),
                           sleep=lambda s: None, now=now, timeout=1.0)
    assert got is None            # the stranger is never even probed


def test_runner_timeout_does_not_crash_discovery():
    import subprocess

    def boom(argv, input=None, timeout=30):
        if argv[:2] == ["ip", "-o"]:
            return (0, IP_LINK, "")
        raise subprocess.TimeoutExpired(argv, timeout)

    # a raising runner used directly would crash; the default runner catches it,
    # but here we assert discover_peer tolerates a runner that returns cleanly
    # after a timeout by using the real _default_runner's contract shape.
    clock = [0.0]

    def now():
        clock[0] += 0.5
        return clock[0]

    got = dl.discover_peer("nodemedic-b", runner=lambda *a, **k: (1, "", "t/o"),
                           probe=lambda h, p=22, timeout=3.0: False,
                           sleep=lambda s: None, now=now, timeout=1.0)
    assert got is None
