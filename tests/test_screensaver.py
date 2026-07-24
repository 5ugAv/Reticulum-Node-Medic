"""Screen-saver settings store."""

from provisioning import screensaver as ss


def test_defaults(tmp_path):
    d = ss.load(path=str(tmp_path / "s.json"))
    assert d == {"enabled": True, "style": "swirl", "idle_delay_s": 180}


def test_persist_enabled_style_delay(tmp_path):
    p = str(tmp_path / "s.json")
    ss.set_enabled(False, path=p)
    assert ss.is_enabled(path=p) is False
    ss.set_style("swirl", path=p)
    assert ss.style(path=p) == "swirl"
    ss.set_idle_delay_s(300, path=p)
    assert ss.idle_delay_s(path=p) == 300


def test_unknown_style_falls_back(tmp_path):
    p = str(tmp_path / "s.json")
    ss.set_style("kaleidoscope", path=p)          # not in STYLES yet
    assert ss.style(path=p) == "swirl"


def test_idle_delay_clamped(tmp_path):
    p = str(tmp_path / "s.json")
    assert ss.set_idle_delay_s(5, path=p)["idle_delay_s"] == ss.MIN_IDLE_S
    assert ss.set_idle_delay_s(999999, path=p)["idle_delay_s"] == ss.MAX_IDLE_S


def test_step_idle_walks_presets():
    assert ss.step_idle(180, +1) == 300
    assert ss.step_idle(180, -1) == 120
    assert ss.step_idle(60, -1) == 60
    assert ss.step_idle(1800, +1) == 1800


def test_format_delay():
    assert ss.format_delay(180) == "3 min"
    assert ss.format_delay(30) == "30 s"
    assert ss.format_delay(90) == "1.5 min"


def test_corrupt_file_is_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{bad json")
    assert ss.load(path=str(p)) == ss.DEFAULTS
