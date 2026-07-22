"""Screen backlight brightness — sysfs mapping, floor, sudo write, persistence."""

import os

from provisioning import brightness as b


def _fake_backlight(tmp_path, cur=128, mx=255):
    dev = tmp_path / "10-0045"
    dev.mkdir(parents=True)
    (dev / "brightness").write_text(str(cur))
    (dev / "max_brightness").write_text(str(mx))
    return str(dev)


def test_no_backlight_is_graceful(tmp_path):
    empty = str(tmp_path / "none" / "*")
    assert b.backlight_device(empty) is None
    assert b.has_control(empty) is False
    assert b.get_brightness(glob_pattern=empty) is None
    ok, msg = b.set_brightness(50, glob_pattern=empty, run=lambda a: (0, ""))
    assert not ok and "no screen" in msg.lower()


def test_get_brightness_maps_to_percent(tmp_path):
    full = _fake_backlight(tmp_path / "full", cur=255, mx=255)
    assert b.get_brightness(device=full) == 100
    half = _fake_backlight(tmp_path / "half", cur=128, mx=256)
    assert b.get_brightness(device=half) == 50


def test_pct_to_raw_floors_and_clamps():
    assert b.pct_to_raw(100, 255) == 255
    assert b.pct_to_raw(0, 255) == b.pct_to_raw(b.MIN_PCT, 255)   # floored, never 0
    assert b.pct_to_raw(-20, 255) >= 1
    assert b.pct_to_raw(200, 255) == 255


def test_set_brightness_writes_scaled_value_via_sudo(tmp_path):
    dev = _fake_backlight(tmp_path, cur=10, mx=200)
    seen = {}
    def run(argv):
        seen["argv"] = argv
        return (0, "")
    ok, msg = b.set_brightness(50, device=dev, run=run)
    assert ok and "50%" in msg
    argv = seen["argv"]
    assert argv[:3] == ["sudo", "-n", "sh"]
    # 50% of max 200 = 100, written to the device's brightness file
    assert f"echo 100 > {os.path.join(dev, 'brightness')}" in argv[-1]


def test_set_brightness_floor_prevents_blackout(tmp_path):
    dev = _fake_backlight(tmp_path, mx=100)
    seen = {}
    def run(argv):
        seen["argv"] = argv
        return (0, "")
    ok, _ = b.set_brightness(0, device=dev, run=run)     # asked for 0%
    assert ok
    # floored at MIN_PCT, so the written raw value is never 0 (screen stays lit)
    assert f"echo {b.pct_to_raw(0, 100)} >" in seen["argv"][-1]
    assert b.pct_to_raw(0, 100) >= b.MIN_PCT


def test_set_brightness_surfaces_failure(tmp_path):
    dev = _fake_backlight(tmp_path)
    ok, msg = b.set_brightness(60, device=dev,
                               run=lambda a: (1, "sudo: a password is required"))
    assert not ok and "password" in msg.lower()


def test_save_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "brightness")
    b.save_pct(73, path=p)
    assert b.load_pct(path=p) == 73


def test_load_missing_is_none(tmp_path):
    assert b.load_pct(path=str(tmp_path / "nope")) is None
