"""Alerts — settings + which nodes alert + transition detection (Settings item 4)."""

from monitor import alerts


def _n(name, status):
    return {"name": name, "status": status}


def test_settings_default_on_visual_only(tmp_path):
    s = alerts.load_settings(path=str(tmp_path / "a.json"))
    assert s == {"enabled": True, "audible": False}


def test_settings_persist_and_toggle(tmp_path):
    p = str(tmp_path / "a.json")
    alerts.set_enabled(False, path=p)
    assert alerts.is_enabled(path=p) is False
    alerts.set_enabled(True, path=p)
    assert alerts.is_enabled(path=p) is True


def test_audible_channel_reserved_for_later(tmp_path):
    p = str(tmp_path / "a.json")
    alerts.save_settings({"audible": True}, path=p)
    assert alerts.load_settings(path=p)["audible"] is True
    assert alerts.load_settings(path=p)["enabled"] is True   # untouched


def test_alerting_nodes_worst_first():
    nodes = [_n("A", "ok"), _n("B", "warn"), _n("C", "alert"),
             _n("D", "unknown"), _n("E", "warn")]
    got = [n["name"] for n in alerts.alerting_nodes(nodes)]
    assert got == ["C", "B", "E"]          # alert first, then warns by name


def test_alerting_nodes_empty_when_all_ok():
    assert alerts.alerting_nodes([_n("A", "ok"), _n("B", "unknown")]) == []


def test_new_alerts_fires_only_on_rise():
    prev = {"A": "ok", "B": "warn", "C": "alert"}
    nodes = [_n("A", "warn"),    # ok -> warn : NEW
             _n("B", "alert"),   # warn -> alert : NEW (escalated)
             _n("C", "alert"),   # alert -> alert : not new
             _n("D", "alert")]   # unseen -> alert : NEW
    got = {n["name"] for n in alerts.new_alerts(prev, nodes)}
    assert got == {"A", "B", "D"}


def test_new_alerts_recovery_does_not_fire():
    prev = {"A": "alert"}
    assert alerts.new_alerts(prev, [_n("A", "ok")]) == []    # recovered, no alert


def test_status_map_roundtrips_into_new_alerts():
    nodes = [_n("A", "warn"), _n("B", "ok")]
    snap = alerts.status_map(nodes)
    assert snap == {"A": "warn", "B": "ok"}
    # same statuses next poll -> nothing new
    assert alerts.new_alerts(snap, nodes) == []


def test_banner_text():
    assert alerts.banner_text([_n("A", "ok")]) == ""
    t = alerts.banner_text([_n("Rooftop", "alert"), _n("Hill", "warn")])
    assert "2 nodes need attention" in t and "Rooftop" in t and "Hill" in t
    assert "1 node needs attention" in alerts.banner_text([_n("Solo", "alert")])
