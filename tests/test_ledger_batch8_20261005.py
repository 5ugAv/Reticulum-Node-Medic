"""Readiness ledger, 2026-10-05 early, batch eight — small honesty items across
MAPS, ANTENNA, Settings, the node page, Chat, the birth guide, the flash path
and the date/time screen, plus one spelling for 'Wi-Fi'."""
import ast
import glob
import json
import os

from tests.srcutil import ROOT, src


def test_maps_range_rings_and_double_tap():
    s = src("ui/screens/scan_screen.py")
    body = s[s.index("def _toggle_boundary"):s.index("def _toggle_links") if s.index("def _toggle_links") > s.index("def _toggle_boundary") else None]
    assert "self._toggle_offline()" in body                       # #7
    up = s[s.index("def on_touch_up"):s.index("# -- optional overlays")]
    assert 'getattr(touch, "is_double_tap", False)' in up         # #8


def test_antenna_card_offers_the_range_test():
    t = src("ui/screens/triage_screen.py")
    assert "self._on_boundary_walk = on_boundary_walk" in t
    assert 'tr("Range test instead (needs a reachable node)")' in t


def test_radio_defaults_status_starts_green_on_every_save():
    r = src("ui/screens/radio_defaults_screen.py")
    commit = r[r.index("def _commit"):]
    assert 'theme.COLORS["green"]' in commit.split("self._status.text")[0]


def test_a_board_without_a_photo_shows_its_name():
    b = src("ui/widgets/board_card.py")
    assert "board_images.image_for(board_key)" in b and "if not photo:" in b
    assert "from ui import theme" in b.split("class BoardCard")[0]


def test_lrzsz_is_not_fetched_on_an_offline_node_that_has_it():
    s = src("workflows/build.py")
    assert 'wf.connection.run("dpkg -s lrzsz")[0] != 0' in s
    assert 'curl -fsI -m 5 https://deb.debian.org' in s


def test_the_node_page_uses_the_rows_colour_scale():
    n = src("ui/screens/node_detail_screen.py")
    assert "_TONE[theme.battery_status(batt)]" in n and "_TONE[theme.signal_status(sig)]" in n
    assert "if batt > 50 else" not in n and "if sig > -90 else" not in n


def test_ping_answered_says_what_the_page_really_does():
    a = src("ui/app.py")
    assert "new readings when you open it again" in a and "on its next refresh" not in a


def test_back_from_communication_apps_returns_to_chat_when_chat_opened_it():
    a = src("ui/app.py")
    assert "def _open_comms_from_chat(self):" in a and 'self._comms_back = "chat"' in a
    assert "back_to=self._comms_back_target" in a
    assert "self.switch_mode(back_to() if callable(back_to) else back_to)" in a


def test_peer_labels_and_never_heard_peers(tmp_path):
    from monitor.lxmf_chat import MessageStore, short_hash
    store = MessageStore(str(tmp_path / "chat"))
    peer = "c" * 32
    assert store.peer_label(peer) == short_hash(peer)                 # no name: the tag alone
    store.remember_peer(peer, name="Marnie", seen=10.0)
    assert store.peer_label(peer) == f"Marnie  {short_hash(peer)}"    # name + tag (#85)
    store.add_outgoing("d" * 32, "hello?")                            # typed, never heard
    assert [p["hash"] for p in store.peers()] == [peer]               # not 'heard' (#79)
    c = src("ui/screens/chat_screen.py")
    assert c.count("peer_label(") >= 2


def test_the_pickers_say_true_things_and_go_back_to_the_right_place():
    b = src("ui/screens/birth_guide_screen.py")
    assert "self._render_pick_board(absent=absent))   # (#70)" in b
    assert "Node Medic looks at the USB again before it flashes" not in b          # #172
    assert 'getattr(self, "_cands_narrowed", False)' in b                           # #73
    cands = b[b.index("def _board_candidates"):b.index("def _board_picked")]
    assert "self._cands_narrowed = True" in cands and "self._cands_narrowed = False" in cands


def test_the_clock_switch_is_off_without_gps_tools():
    d = src("ui/screens/datetime_screen.py")
    assert 'shutil.which("gpspipe") is not None' in d
    assert "active=td.is_autosync() and self._has_gps_tools" in d
    assert "No GPS tools on this medic" in d


def test_online_a_failed_download_falls_back_to_the_cache():
    f = src("workflows/rnode_flash.py")
    online = f[f.index("if has_connectivity(self.connection):"):f.index("# Offline: the carried cache")]
    assert "cached_firmware_version(self.connection, self.version)" in online
    assert "— flashing the " in online and "carried {cached} instead" in online


def test_every_translated_literal_spells_wifi_one_way():
    offenders = []
    for path in glob.glob(os.path.join(ROOT, "ui", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str) and "WiFi" in node.args[0].value):
                offenders.append((os.path.relpath(path, ROOT), node.lineno))
    assert not offenders, offenders
    for code in ("es", "fr", "de", "ja", "ru", "pl", "id", "sv"):
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as fh:
            keys = list(json.load(fh))
        assert not any("WiFi" in k for k in keys), code
