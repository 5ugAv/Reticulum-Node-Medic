"""MITOSIS phase 1 — the medic card bake.

Pins: the helper's medic profile writes what it claims (config.txt lines,
rootfs hostname, the cable-IP unit), the unit cannot drift from
provisioning/direct_link.py (same duplicate-on-purpose rule as the gadget
unit), and the driver returns the write-it-down password exactly once.
"""

import importlib.util
import json
import os

import pytest


def _helper():
    path = os.path.join(os.path.dirname(__file__), os.pardir,
                        "assets", "scripts", "prepare_card.py")
    spec = importlib.util.spec_from_file_location("prepare_card_mitosis", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- the cable unit is pinned to the repo's direct_link ----------------------

def test_medic_cable_unit_matches_direct_link():
    from provisioning import direct_link
    pc = _helper()
    assert pc.MEDIC_CABLE_UNIT == direct_link.ETH_LINK_SERVICE
    assert pc.MEDIC_CABLE_UNIT_PATH == direct_link.ETH_LINK_SERVICE_PATH


# -- write_boot medic lines --------------------------------------------------

def _boot_dir(tmp_path, config="dtparam=audio=on\n"):
    (tmp_path / "config.txt").write_text(config)
    (tmp_path / "cmdline.txt").write_text("console=serial0 root=PARTUUID=x\n")
    return str(tmp_path)


def test_medic_boot_config_lines_are_baked_and_idempotent(tmp_path):
    pc = _helper()
    mnt = _boot_dir(tmp_path)
    cfg = {"medic": True, "cable_link": False}
    pc.write_boot(mnt, cfg)
    text = (tmp_path / "config.txt").read_text()
    assert "dtparam=i2c_arm=on" in text
    assert "usb_max_current_enable=1" in text
    pc.write_boot(mnt, cfg)                       # second run adds nothing
    assert (tmp_path / "config.txt").read_text().count("i2c_arm=on") == 1


def test_node_cards_are_untouched_by_the_medic_lines(tmp_path):
    pc = _helper()
    mnt = _boot_dir(tmp_path)
    pc.write_boot(mnt, {"cable_link": False})
    assert "i2c_arm=on" not in (tmp_path / "config.txt").read_text()


# -- write_rootfs medic profile ---------------------------------------------

def _rootfs(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "hosts").write_text(
        "127.0.0.1\tlocalhost\n127.0.1.1\traspberrypi\n")
    return str(tmp_path)


def test_medic_rootfs_gets_hostname_and_cable_unit(tmp_path):
    pc = _helper()
    mnt = _rootfs(tmp_path)
    pc.write_rootfs(mnt, {"medic": True, "cable_link": False,
                          "hostname": "nodemedic2-0"})
    assert (tmp_path / "etc" / "hostname").read_text() == "nodemedic2-0\n"
    hosts = (tmp_path / "etc" / "hosts").read_text()
    assert "127.0.1.1\tnodemedic2-0" in hosts
    assert "raspberrypi" not in hosts
    unit = tmp_path / "etc/systemd/system/nodemedic-cable-ip.service"
    assert unit.is_file()
    assert "10.55.0.1/29" in unit.read_text()   # B claims the PEER side
    link = tmp_path / ("etc/systemd/system/multi-user.target.wants/"
                       "nodemedic-cable-ip.service")
    assert link.is_symlink()


# -- the driver --------------------------------------------------------------

def test_image_medic_card_flags_and_password():
    from workflows import mitosis_card
    calls = {}

    def fake_flash(device, hostname, username, password, **kw):
        calls.update(device=device, hostname=hostname, username=username,
                     password=password, **kw)
        return True, "ok"

    ok, msg, pw = mitosis_card.image_medic_card(
        "/dev/sda", "NodeMedic2.0", flash=fake_flash, wifi=("home", "pass"),
        helper_check=lambda: "")
    assert ok and pw == calls["password"]
    assert calls["hostname"] == "nodemedic2-0"     # hostnameify applied
    assert calls["medic"] is True
    assert calls["cable_link"] is False            # no dwc2 on a medic card
    assert calls["wifi_ssid"] == "home"


def test_memorable_password_shape():
    from workflows.mitosis_card import memorable_password
    for _ in range(20):
        a, b, n = memorable_password().split("-")
        assert a != b and a.isalpha() and b.isalpha() and 10 <= int(n) <= 99


def test_bad_name_refuses_before_touching_the_card():
    from workflows import mitosis_card
    ok, msg, pw = mitosis_card.image_medic_card(
        "/dev/sda", "///", flash=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("flash must not be called")), wifi=("", ""),
        helper_check=lambda: "")
    assert ok is False and pw == ""


# -- carry_the_time: the clone's clock comes from the medic ------------------

class _Conn:
    def __init__(self, clone_epoch="100", sudo_ok=True):
        self.cmds = []
        self.clone_epoch = clone_epoch
        self.sudo_ok = sudo_ok

    def run(self, cmd, timeout=30):
        self.cmds.append(cmd)
        if cmd == "date -u +%s":
            return 0, self.clone_epoch, ""
        if cmd.startswith("sudo -n date"):
            return (0, "", "") if self.sudo_ok else (1, "", "sudo: a password is required")
        return 0, "", ""


def test_carry_the_time_sets_clock_and_enables_ntp():
    from workflows.clone import CloneWorkflow, carry_the_time
    from monitor.registry import NodeRegistry
    conn = _Conn(clone_epoch="100")            # clone thinks it's 1970
    wf = CloneWorkflow(conn, NodeRegistry())
    r = carry_the_time(wf)
    assert r.success
    assert any(c.startswith("sudo -n date -u -s @") for c in conn.cmds)
    assert "sudo -n timedatectl set-ntp true" in conn.cmds
    assert "drift" in r.message                # honest about what it corrected


def test_carry_the_time_sudo_refusal_is_a_named_failure():
    from workflows.clone import CloneWorkflow, carry_the_time
    from monitor.registry import NodeRegistry
    wf = CloneWorkflow(_Conn(sudo_ok=False), NodeRegistry())
    r = carry_the_time(wf)
    assert r.success is False and "sudo" in r.message


def test_carry_the_time_rides_right_after_pi5_check():
    from workflows.clone import _CLONE_STEPS
    names = [n for n, _ in _CLONE_STEPS]
    assert names.index("carry_the_time") == names.index("verify_target_pi5") + 1


# -- adversarial-review round (2026-08-25): the card bakes that save first boot

def _full_boot(tmp_path):
    (tmp_path / "config.txt").write_text("#dtparam=i2c_arm=on\ndtparam=audio=on\n")
    (tmp_path / "cmdline.txt").write_text("console=serial0 root=PARTUUID=x rw\n")
    return str(tmp_path)


def test_commented_config_line_does_not_satisfy_the_medic_bake(tmp_path):
    pc = _helper()
    mnt = _full_boot(tmp_path)          # ships a COMMENTED #dtparam=i2c_arm=on
    pc.write_boot(mnt, {"medic": True, "cable_link": False})
    text = (tmp_path / "config.txt").read_text()
    assert any(ln.strip() == "dtparam=i2c_arm=on" for ln in text.splitlines())


def test_medic_card_gets_the_regdom_despite_no_cable_link(tmp_path):
    # The SolarLove gate: regdom was only baked for cable-birth cards, so
    # medic cards shipped rfkill-blocked. Now every card with a country gets it.
    pc = _helper()
    mnt = _full_boot(tmp_path)
    pc.write_boot(mnt, {"medic": True, "cable_link": False,
                        "wifi_country": "AU"})
    assert "cfg80211.ieee80211_regdom=AU" in (tmp_path / "cmdline.txt").read_text()


def test_medic_card_neutralizes_the_first_boot_wizard(tmp_path):
    pc = _helper()
    mnt = _full_boot(tmp_path)
    pc.write_boot(mnt, {"medic": True, "cable_link": False,
                        "user": "pi", "pwhash": "$6$abc"})
    assert (tmp_path / "userconf.txt").read_text() == "pi:$6$abc\n"


def test_medic_rootfs_unmanages_wired_nic_and_disarms_wizard(tmp_path):
    pc = _helper()
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "hosts").write_text("127.0.0.1\tlocalhost\n")
    wants = tmp_path / "etc/systemd/system/multi-user.target.wants"
    wants.mkdir(parents=True)
    (wants / "userconfig.service").write_text("stub")
    sshd = tmp_path / "etc/ssh/sshd_config.d"
    sshd.mkdir(parents=True)
    (sshd / "rename_user.conf").write_text("stub")
    pc.write_rootfs(str(tmp_path), {"medic": True, "cable_link": False,
                                    "hostname": "hawkeye"})
    nm = tmp_path / "etc/NetworkManager/conf.d/98-nodemedic-wired-clone.conf"
    assert "interface-name:eth*" in nm.read_text()
    assert not (wants / "userconfig.service").exists()
    assert not (sshd / "rename_user.conf").exists()
    assert (sshd / "10-nodemedic-keyonly.conf").read_text() == \
        "PasswordAuthentication no\n"


def test_cable_unit_retries_for_a_slow_nic():
    from provisioning import direct_link
    assert "for t in 1 2 3 4 5 6" in direct_link.ETH_LINK_SERVICE
    assert "sleep 2" in direct_link.ETH_LINK_SERVICE


def test_stale_helper_refuses_before_touching_the_card():
    from workflows import mitosis_card
    ok, msg, pw = mitosis_card.image_medic_card(
        "/dev/sda", "HAWKEYE",
        flash=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("flash must not run on a stale helper")),
        wifi=("", ""), helper_check=lambda: "helper is out of date")
    assert ok is False and "out of date" in msg


# ---------------------------------------------------------------------------
# Reading the card back (2026-09-02)
#
# The card write was never verified. The image can land perfectly while the
# configuration silently does not, and the screen still said "Card written".
# The first anyone would know is a medic that boots nameless and unreachable -
# by which point it is closed up and carried to wherever it is going.
# ---------------------------------------------------------------------------

from workflows import mitosis_card  # noqa: E402


def _fake_shell(rootfs: dict, bootfs: dict):
    """Stand in for the mount/cat/umount shell, emitting the same marker format."""
    def run(cmd):
        table = rootfs if "/dev/sda2" in cmd else bootfs
        out = []
        for path, body in table.items():
            out.append("---RNMFILE---" + path)
            out.append(body)
        return 0, "\n".join(out) + "\n"
    return run


_GOOD_ROOT = {"/etc/hostname": "hawkeye\n",
              "/home/pi/.ssh/authorized_keys": "ssh-ed25519 AAAAC3Nz medic\n"}
_GOOD_BOOT = {"/config.txt": "dtparam=i2c_arm=on\nusb_max_current_enable=1\n"}


def test_a_correctly_baked_card_passes_every_check():
    ok, checks = mitosis_card.verify_medic_card(
        "/dev/sda", "hawkeye", run_shell=_fake_shell(_GOOD_ROOT, _GOOD_BOOT))
    assert ok
    assert [c[0] for c in checks] == ["Name", "Key", "Power", "Battery gauge"]
    assert all(passed for _, passed, _ in checks)


def test_a_card_that_booted_nameless_is_caught():
    """The exact silent failure this exists for: image fine, name missing."""
    root = dict(_GOOD_ROOT, **{"/etc/hostname": "\n"})
    ok, checks = mitosis_card.verify_medic_card(
        "/dev/sda", "hawkeye", run_shell=_fake_shell(root, _GOOD_BOOT))
    assert not ok
    name = next(c for c in checks if c[0] == "Name")
    assert not name[1] and "nameless" in name[2]


def test_a_missing_medic_key_is_caught():
    """Without the key the new medic cannot be reached without the password,
    which is the one thing the operator was told to write down and may not."""
    root = dict(_GOOD_ROOT, **{"/home/pi/.ssh/authorized_keys": ""})
    ok, checks = mitosis_card.verify_medic_card(
        "/dev/sda", "hawkeye", run_shell=_fake_shell(root, _GOOD_BOOT))
    assert not ok
    assert not next(c for c in checks if c[0] == "Key")[1]


def test_the_pi5_usb_power_flag_is_checked():
    """Without usb_max_current_enable the Pi 5 caps USB at 600 mA, which is not
    enough for the boards this tool exists to flash - and nothing else reports
    it. Verified present on the real HAWKEYE card, 2026-09-02."""
    boot = {"/config.txt": "dtparam=i2c_arm=on\n"}
    ok, checks = mitosis_card.verify_medic_card(
        "/dev/sda", "hawkeye", run_shell=_fake_shell(_GOOD_ROOT, boot))
    assert not ok
    assert not next(c for c in checks if c[0] == "Power")[1]


def test_checks_read_the_rootfs_not_custom_toml():
    """custom.toml and cloud-init are INERT on this image - the bake writes to
    the root filesystem directly. A check that read custom.toml would happily
    'verify' a file nothing on the Pi ever reads."""
    seen = []

    def spy(cmd):
        seen.append(cmd)
        return 0, ""
    mitosis_card.verify_medic_card("/dev/sda", "hawkeye", run_shell=spy)
    joined = " ".join(seen)
    assert "/dev/sda2" in joined and "/etc/hostname" in joined
    assert "custom.toml" not in joined


def test_the_card_is_always_unmounted_even_when_reads_fail():
    """The operator is about to be told to pull the card out."""
    seen = []

    def spy(cmd):
        seen.append(cmd)
        return 1, ""
    mitosis_card.verify_medic_card("/dev/sda", "hawkeye", run_shell=spy)
    assert all("umount" in c for c in seen), "a mount was left behind"
