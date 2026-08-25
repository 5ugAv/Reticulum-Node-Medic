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
