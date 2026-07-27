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
    # with no hostname there is only the static address to try
    assert dl.candidate_targets("") == [dl.PEER_ETH_IP]


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
    addr = [c for c in calls if "addr" in c]
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
