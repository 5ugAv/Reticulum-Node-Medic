"""Readiness ledger, 2026-10-04 third batch:

  #87   PROBE printed developer identifiers (serial_responsive, eeprom_valid…)
  #117  the Clone cable page had no way forward with the socket already in use
  #177  a corrupt or empty .mbtiles was a silent black pane
  #2/#178  no map carried: dead controls and an explanation nobody could see
  #187  Send was a dead button with no door; every failure blamed the radio
  #47   a radio on a REMOTE Pi's /dev/ttyACM0 was mistaken for the medic's own
"""
import glob
import os
import re
import sqlite3

import pytest

from tests.srcutil import ROOT, src


# -- #87 -------------------------------------------------------------------

def _declared_checks():
    names = set()
    for path in sorted(glob.glob(os.path.join(ROOT, "diagnostics", "*.py"))):
        with open(path, encoding="utf-8") as f:
            names |= set(re.findall(r'_check\(\s*\n?\s*"([a-z0-9_]+)"', f.read()))
    return names


def test_every_declared_check_has_plain_words():
    from diagnostics.labels import LABELS, label_for
    declared = _declared_checks()
    assert len(declared) > 80, declared
    missing = sorted(n for n in declared if n not in LABELS)
    assert not missing, f"name these in diagnostics/labels.py: {missing}"
    for name, label in LABELS.items():
        assert label and label[0].isupper() and "_" not in label, (name, label)
    assert label_for("some_new_check") == "Some new check"      # readable fallback
    assert label_for("serial_responsive") == "Serial port answers"


def test_probe_renders_labels_and_severity_words():
    from diagnostics.labels import SEVERITY_WORDS
    s = src("ui/screens/probe_screen.py")
    for sev, word in SEVERITY_WORDS.items():
        assert f'"{sev}": tr("{word}")' in s, (sev, word)   # the screen says these words
    assert "from diagnostics.labels import label_for" in s
    assert 'f"  {mark} {label_for(check_name)}"' in s
    assert "name=label_for(event.check_name)" in s
    assert 'f"[{issue.severity}] {issue.description}"' not in s
    assert 'tr("Critical")' in s and 'tr("Warning")' in s and 'tr("Note")' in s


# -- #117 ------------------------------------------------------------------

def test_the_cable_page_offers_a_way_forward_when_the_socket_is_already_up():
    m = src("ui/screens/mitosis_screen.py")
    page = m[m.index("def _show_stage_cable"):m.index("def _on_cable_seen")]
    assert "if self._cable_already_up:" in page
    assert "It's plugged in - carry on →" in page
    assert "go.bind(on_release=lambda *_: self._on_cable_seen(gen))" in page
    assert "network socket is already in use" in page


# -- #177 / #2 / #178 ------------------------------------------------------

def _mbtiles(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, "
                 "tile_row INTEGER, tile_data BLOB)")
    conn.executemany("INSERT INTO tiles VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_find_mbtiles_refuses_a_corrupt_or_empty_file_and_names_it(tmp_path, monkeypatch):
    from ui import map_tiles as mt
    maps = tmp_path / "maps"
    maps.mkdir()
    monkeypatch.setattr(mt, "LEGACY_MAPS_DIR", str(tmp_path / "legacy"))
    # nothing carried
    assert mt.find_mbtiles(str(maps)) is None and mt.LAST_OPEN_PROBLEM is None
    # garbage bytes
    bad = maps / "offline.mbtiles"
    bad.write_bytes(b"this is not sqlite at all" * 40)
    assert mt.find_mbtiles(str(maps)) is None
    assert mt.LAST_OPEN_PROBLEM and mt.LAST_OPEN_PROBLEM[0] == str(bad)
    # a real database with no tiles in it
    bad.unlink()
    _mbtiles(str(bad), [])
    assert mt.find_mbtiles(str(maps)) is None
    assert "no tiles" in mt.LAST_OPEN_PROBLEM[1]
    # a real map
    bad.unlink()
    _mbtiles(str(bad), [(3, 1, 1, b"\x89PNG")])
    tiles = mt.find_mbtiles(str(maps))
    assert tiles is not None and tiles.zoom_levels() == [3]
    assert mt.LAST_OPEN_PROBLEM is None
    tiles.close()


def test_scan_explains_a_missing_or_unreadable_map_where_it_can_be_seen():
    s = src("ui/screens/scan_screen.py")
    body = s[s.index("def _reflect_tiles"):s.index("def _poll_gps")]
    assert "rb.disabled = not have" in body                 # the target button too
    assert "LAST_OPEN_PROBLEM" in body and "Map file could not be read" in body
    assert "self._toggle_offline()" in body                 # the panel opens itself
    # the panel exists before the first reflection
    assert s.index("self._offline_panel = BoxLayout(") < s.index("self._reflect_tiles()")


# -- #187 ------------------------------------------------------------------

def test_chat_shows_the_real_reason_and_offers_try_again():
    c = src("ui/screens/chat_screen.py")
    assert "retry=None" in c and "def _retry_row" in c
    assert 'tr("Chat isn\'t up: {error}").format(error=err)' in c
    assert "Chat is waiting for the mesh service (rnsd)…" in c
    assert "the medic hasn't reached its own radio yet" not in c
    assert 'tr("Try again")' in c
    a = src("ui/app.py")
    assert "retry=self.retry_chat" in a and "def retry_chat(self):" in a
    assert "self._chat_poll_ev = Clock.schedule_interval(self._chat_start_poll, 3)" in a


# -- #47 -------------------------------------------------------------------

class _PiConnection:
    """An SSH'd Pi with one radio on ttyACM0 — NOT a LocalConnection."""

    def run(self, cmd, timeout=None):
        if cmd.startswith("ls /dev/serial/by-id/"):
            return 0, "usb-Espressif_USB_JTAG_serial_debug_unit_0000-if00\n", ""
        if cmd.startswith("readlink -f /dev/serial/by-id/"):
            return 0, "/dev/ttyACM0\n", ""
        if cmd.startswith("ls /dev/ttyACM"):
            return 0, "/dev/ttyACM0\n", ""
        return 1, "", ""


def test_a_radio_on_the_remote_pi_is_never_mistaken_for_the_medics_own(monkeypatch):
    import ui.onboard_roster as roster
    import ui.usb_ports as usb
    from workflows.build import detect_rnode_port
    # the medic's OWN roster says ttyACM0 is its radio — true on the medic,
    # meaningless for a port on the Pi at the far end of an SSH session
    monkeypatch.setattr(roster, "is_onboard", lambda *a, **k: True)
    monkeypatch.setattr(usb, "connection_is_local", lambda c: False)
    assert detect_rnode_port(_PiConnection()) == "/dev/ttyACM0"
    # ...while a LOCAL port that is the medic's own is still refused
    monkeypatch.setattr(usb, "connection_is_local", lambda c: True)
    assert detect_rnode_port(_PiConnection()) is None
