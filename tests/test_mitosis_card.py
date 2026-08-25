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
        "/dev/sda", "NodeMedic2.0", flash=fake_flash, wifi=("home", "pass"))
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
            AssertionError("flash must not be called")), wifi=("", ""))
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
