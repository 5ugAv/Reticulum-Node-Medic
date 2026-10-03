"""One touch provider for the panel (ui/touch_input.py, 2026-10-03)."""
import configparser
from ui import touch_input as ti


def _sysfs(tmp_path, names):
    for i, n in enumerate(names):
        d = tmp_path / ("event%d" % i) / "device"; d.mkdir(parents=True)
        (d / "name").write_text(n + "\n")
    return str(tmp_path)


def _cfg():
    c = configparser.ConfigParser(); c.add_section("input"); c.set("input", "mouse", "mouse"); return c


def test_finds_the_goodix_by_name_not_by_number(tmp_path):
    s = _sysfs(tmp_path, ["vc4-hdmi-0", "pwr_button", "Goodix Capacitive TouchScreen"])
    assert ti.find_panel(s) == "/dev/input/event2"
    assert ti.find_panel(_sysfs(tmp_path / "none", ["pwr_button"])) is None


def test_mtdev_replaces_the_mouse_provider_when_everything_checks_out(tmp_path):
    s = _sysfs(tmp_path, ["Goodix Capacitive TouchScreen"]); c = _cfg(); logs = []
    verdict = ti.choose(c, sysfs=s, loader=lambda n: object(), readable=lambda p: True, log=logs.append)
    assert verdict == "mtdev /dev/input/event0"
    assert c.get("input", "panel") == "mtdev,/dev/input/event0"
    assert not c.has_option("input", "mouse")          # no second road = no doubled taps
    assert "multitouch" in logs[-1]


def test_any_failed_check_leaves_the_mouse_road_alone(tmp_path):
    s = _sysfs(tmp_path, ["Goodix Capacitive TouchScreen"])
    def boom(n): raise OSError("no lib")
    for kw in ({"loader": boom, "readable": lambda p: True},
               {"loader": lambda n: object(), "readable": lambda p: False}):
        c = _cfg(); v = ti.choose(c, sysfs=s, **kw)
        assert v.startswith("mouse") and c.get("input", "mouse") == "mouse" and not c.has_option("input", "panel")
    c = _cfg(); assert ti.choose(c, sysfs=str(tmp_path / "empty")).startswith("mouse (no panel")


def test_main_chooses_before_any_window_and_the_bridge_is_gone():
    m = open("main.py").read()
    assert m.index("touch_input.choose(") < m.index("from ui.fonts import configure_fonts")
    import os
    assert not os.path.exists("ui/touch_fix.py")
    assert "touch_fix" not in open("ui/app.py").read()
