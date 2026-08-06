from provisioning.gadget import (
    cmdline_with_gadget, config_txt_with_gadget, GADGET_USB_IP, HOST_USB_IP)
from provisioning.link import (
    parse_usb_interfaces, bootstrap_access, discover_peer)

REAL_CMDLINE = ("console=serial0,115200 console=tty1 root=PARTUUID=deadbeef-02 "
                "rootfstype=ext4 fsck.repair=yes rootwait quiet splash "
                "cfg80211.ieee80211_regdom=AU logo.nologo loglevel=3\n")


# -- cmdline.txt transform ------------------------------------------------

def test_cmdline_inserts_modules_after_rootwait():
    out = cmdline_with_gadget(REAL_CMDLINE)
    toks = out.split()
    assert "modules-load=dwc2,g_ether" in toks
    # must come immediately after rootwait (before the rootfs handoff completes)
    assert toks[toks.index("rootwait") + 1] == "modules-load=dwc2,g_ether"
    assert out.endswith("\n")            # preserved trailing newline


def test_cmdline_is_idempotent():
    once = cmdline_with_gadget(REAL_CMDLINE)
    assert cmdline_with_gadget(once) == once


def test_cmdline_merges_existing_modules_load():
    out = cmdline_with_gadget("root=/dev/x rootwait modules-load=foo quiet")
    toks = out.split()
    ml = [t for t in toks if t.startswith("modules-load=")]
    assert len(ml) == 1
    mods = ml[0].split("=")[1].split(",")
    assert set(mods) == {"foo", "dwc2", "g_ether"}


def test_cmdline_appends_when_no_rootwait():
    out = cmdline_with_gadget("root=/dev/x quiet")
    assert out.split()[-1] == "modules-load=dwc2,g_ether"


# -- config.txt transform -------------------------------------------------

def test_config_adds_dwc2_overlay_once():
    out = config_txt_with_gadget("[all]\ndtparam=audio=on\n")
    assert out.count("dtoverlay=dwc2") == 1
    assert config_txt_with_gadget(out) == out          # idempotent


def test_config_handles_empty_file():
    assert "dtoverlay=dwc2" in config_txt_with_gadget("")


# -- host-side interface discovery ---------------------------------------

def test_parse_usb_interfaces_picks_gadget_names():
    ip_link = (
        "1: lo: <LOOPBACK,UP> mtu 65536 qdisc noqueue state UNKNOWN\n"
        "2: eth0: <BROADCAST,MULTICAST,UP> mtu 1500 qdisc mq state UP\n"
        "3: usb0: <BROADCAST,MULTICAST> mtu 1500 qdisc pfifo_fast state DOWN\n"
        "4: enx00e04c534458: <BROADCAST,MULTICAST,UP> mtu 1500 state UP\n"
        "5: wlan0: <BROADCAST,MULTICAST,UP> mtu 1500 state UP\n")
    assert parse_usb_interfaces(ip_link) == ["usb0", "enx00e04c534458"]


def test_parse_usb_interfaces_empty():
    assert parse_usb_interfaces("") == []


def test_discover_peer_returns_gadget_when_port_open():
    calls = []

    def runner(argv, input=None, env=None, timeout=30):
        calls.append(argv)
        if argv[:2] == ["ip", "-o"]:
            return (0, "3: usb0: <BROADCAST> mtu 1500 state DOWN\n", "")
        return (0, "", "")

    ip = discover_peer(runner=runner, timeout=10, poll=0,
                       sleep=lambda *_: None, now=lambda: 0.0,
                       probe=lambda host, port: host == GADGET_USB_IP)
    assert ip == GADGET_USB_IP
    # it claimed the host end of the /29 on the gadget interface
    assert any(HOST_USB_IP in " ".join(a) and "usb0" in a for a in calls)


def test_discover_peer_times_out_to_none():
    t = {"v": 0.0}

    def clock():
        t["v"] += 1.0
        return t["v"]

    ip = discover_peer(runner=lambda *a, **k: (0, "", ""), timeout=3, poll=0,
                       sleep=lambda *_: None, now=clock,
                       probe=lambda *a: False)
    assert ip is None


# -- password bootstrap (sequence, via a fake runner) ---------------------

def test_bootstrap_access_installs_key_then_passwordless_sudo():
    seen = []

    # distinct password (!= username) so we can assert it never leaks into argv
    PW = "s3cr3t-pw"

    def runner(argv, input=None, env=None, timeout=30):
        seen.append((argv, input, env))
        if argv[0] == "sshpass":
            assert env and env.get("SSHPASS") == PW             # never in argv
            assert PW not in " ".join(argv)
            return (0, "", "")
        if argv[-1] == "true":
            return (0, "", "")                                  # key auth works
        if argv[-1] == "sudo -n true":
            return (0, "", "")                                  # sudo works
        return (0, "", "")

    res = bootstrap_access("10.55.0.1", "everywhere", PW, runner=runner)
    assert res.ok and res.key_installed and res.sudo_ok
    # the sudoers write fed the password on stdin, not via echo/argv
    sudo_write = next(c for c in seen if isinstance(c[0][-1], str)
                      and "sudoers.d" in c[0][-1])
    assert sudo_write[1] == PW + "\n"                           # password on stdin
    assert PW not in " ".join(sudo_write[0])                    # not in argv


def test_bootstrap_access_reports_bad_password():
    def runner(argv, input=None, env=None, timeout=30):
        if argv[-1] == "true":
            return (255, "", "Permission denied")               # key never took
        if argv[-1] == "sudo -n true":
            return (1, "", "")
        return (0, "", "")

    res = bootstrap_access("10.55.0.1", "everywhere", "wrongpw", runner=runner)
    assert not res.ok and not res.key_installed
    assert "password" in res.message.lower()


def test_sudo_names_ip_by_the_path_sudoers_actually_permits():
    """THE MEDIC COULD NEVER CLAIM ITS END OF THE LINK.

    The sudoers entry names `/usr/bin/ip`. sudo resolves a bare `ip` through
    secure_path (/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:...) and
    finds /usr/sbin/ip FIRST — a symlink to the very same binary, but sudo
    matches on the path string, not the inode. So the rule does not match and
    the call is refused.

    Both `ip` calls in discover_peer were therefore no-ops on every run: the
    medic never claimed 10.55.0.2, the SSH probe never succeeded, and
    discover_peer returned None while a perfectly healthy gadget sat on the
    other end of the cable answering nothing.

    Verified on the live medic 2026-08-06:
        sudo -n ip addr add ...            -> "sudo: a password is required"
        sudo -n /usr/bin/ip addr add ...   -> succeeds
    `ip` is the ONLY command we sudo whose bare name resolves somewhere other
    than its sudoers path (tee/systemctl/nmcli/iw/rpiboot/uhubctl/mount/
    partprobe/dd were all checked on the device and all match).
    """
    from provisioning import link

    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["ip", "-o"]:
            return (0, "3: usb0: <BROADCAST> mtu 1500 link/ether\n", "")
        return (0, "", "")

    link.discover_peer(runner=runner, timeout=0.01, poll=0,
                       sleep=lambda _s: None, probe=lambda h, p: False)

    sudo_ip = [c for c in calls if c[:2] == ["sudo", "-n"]]
    assert sudo_ip, "discover_peer never tried to claim the medic's end"
    for c in sudo_ip:
        assert c[2] == "/usr/bin/ip", (
            f"sudo called {c[2]!r}; sudoers permits /usr/bin/ip, and a bare "
            "'ip' resolves to /usr/sbin/ip and is refused")


def test_the_clone_link_uses_the_same_absolute_path():
    """direct_link (the cable-clone path) had the identical bug."""
    src = open("provisioning/direct_link.py").read()
    block = src[src.index("def discover_clone_peer"):] \
        if "def discover_clone_peer" in src else src
    assert '"sudo", "-n", "ip"' not in block, "bare 'ip' will be refused by sudo"
    assert "IP_BIN" in block
