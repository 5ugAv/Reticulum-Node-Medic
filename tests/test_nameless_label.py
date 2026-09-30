"""A kin node with no name says what it is on VITALS, not "(unnamed)"
(the RTNode-2400 on a RAK4631 that sat unnamed at the top, 2026-09-29)."""
from monitor.registry import NodeRecord


def test_nameless_record_without_a_beacon_shows_its_hash():
    r = NodeRecord(dst_hash="5a110011" + "0" * 24)
    assert r._nameless_label() == "(unnamed) 5a110011"


def test_nameless_record_with_a_beacon_shows_its_board(monkeypatch):
    class _B:
        board_label = "RAK4631"                       # a property on the real beacon
    r = NodeRecord(dst_hash="5a110011" + "0" * 24)
    monkeypatch.setattr(NodeRecord, "latest_beacon", property(lambda self: _B()), raising=False)
    assert r._nameless_label() == "RAK4631 · 5a110011"


def test_an_unknown_board_id_does_not_leak_into_the_label(monkeypatch):
    class _B:
        board_label = "unknown(0xff)"
    r = NodeRecord(dst_hash="5a110011" + "0" * 24)
    monkeypatch.setattr(NodeRecord, "latest_beacon", property(lambda self: _B()), raising=False)
    assert r._nameless_label() == "(unnamed) 5a110011"


def test_the_row_uses_it():
    src = open("monitor/registry.py").read()
    assert 'if neighbour else self._nameless_label())' in src
    assert 'if neighbour else "(unnamed)")' not in src


def test_stat_bar_says_wifi_not_sig():
    src = open("ui/widgets/stat_bar.py").read()
    assert 'f"WIFI {int(self.signal_dbm)}dBm"' in src and 'f"SIG {' not in src


def test_links_label_is_kept_current_not_only_on_toggle():
    src = open("ui/screens/scan_screen.py").read()
    assert "Clock.schedule_interval(self._refresh_links_label, 5)" in src


def test_front_page_carries_an_unread_badge_fed_by_the_store():
    home = open("ui/screens/home_screen.py").read()
    app = open("ui/app.py").read()
    assert "class _UnreadBadge(Label)" in home and "def set_unread(self, n: int)" in home
    assert "self.home_screen.set_unread(store.unread_total())" in app


def test_the_live_ghost_beacon_names_its_board():
    """5a110011's actual beacon off the medic, 2026-09-30."""
    from monitor.health_beacon import decode
    b = decode(bytes.fromhex("0200049d6c0013000552510007000000ff308080"))
    assert b.board_label == "RAK4631"
    r = NodeRecord(dst_hash="5a110011" + "0" * 24)
    r.latest_beacon = b
    assert r._nameless_label() == "RAK4631 · 5a110011"
