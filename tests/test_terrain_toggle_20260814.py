"""SCAN's Terrain toggle looked dead in the field (operator, 2026-08-14).

Tapping it did nothing visible and the label never left "Terrain  off". The
binding was fine and the handler ran — the fault was WHERE it spoke.
``_toggle_terrain`` refuses, honestly, when no terrain is cached ("No terrain
cached for this area yet…"), but it says so through ``_set_status``, which
writes to ``dl_status`` — a label INSIDE the "Offline maps" panel, and that
panel starts collapsed (height 0, opacity 0). So on a device with no terrain
file the honest sentence rendered into an invisible widget, every tap, since
the overlay shipped on 2026-08-04. The docstring promised the exact opposite:
"rather than appearing to do nothing". A message nobody can see IS doing
nothing.

The fix keeps the honesty and makes it visible: a refused tap opens the
offline-maps panel first, so the explanation appears next to the download
button that cures it.

Second fault, found on the same read: ``_terrain_store`` memoises its answer —
None included — and ``_download_done`` never forgot the memo. Tap Terrain once
before downloading and the cached None kept refusing after the terrain
arrived, until an app restart.

Kivy is not importable here, so the real methods run against a featherweight
stand-in carrying only the attributes they touch (the ``test_guide_resume``
pattern), with the module imported under the process-global Kivy stubs that
``test_scan_lines`` installs.
"""

import types

import pytest

from tests.srcutil import func_source

# Installs stand-ins for every Kivy submodule scan_screen imports, via
# sys.modules.setdefault — idempotent, and shared with test_scan_lines so the
# two files cannot disagree about what a stub looks like.
from tests.test_scan_lines import _install_kivy_stubs

_install_kivy_stubs()

import ui.screens.scan_screen as scan  # noqa: E402
from ui.map_download import terrain_dest  # noqa: E402

SCREEN = "ui/screens/scan_screen.py"


class _Plot:
    """Records what the screen pushed at the map widget."""

    def __init__(self):
        self.terrain_calls = []
        self.tiles = "old"

    def set_terrain(self, store):
        self.terrain_calls.append(store)

    def set_tiles(self, tiles):
        self.tiles = tiles


class _Btn:
    def __init__(self, text=""):
        self.text = text
        self.disabled = False
        self.height = 10
        self.opacity = 1


class _Screen:
    """Only what ``_toggle_terrain`` / ``_download_done`` actually touch."""

    def __init__(self, store=None, offline_open=False, tiles=None):
        self._store = store
        self._offline_open = offline_open
        self._terrain_on = False
        self._downloading = True
        self._tiles = tiles
        self._nodes = []
        self.plot = _Plot()
        self.terrain_btn = _Btn("Terrain  off")
        self.dl_button = _Btn()
        self.center_input = _Btn()
        self.statuses = []
        self.offline_toggles = 0

    # -- the collaborators, recording instead of drawing --------------------
    def _terrain_store(self):
        return self._store

    def _set_status(self, text, status="unknown"):
        self.statuses.append((text, status))

    def _toggle_offline(self):
        # The real one also fixes opacity/label/height; what matters here is
        # that the refusal goes through IT, so production gets all of that.
        self.offline_toggles += 1
        self._offline_open = not self._offline_open

    def _refresh_header(self):
        pass


def _use_real_terrain_store(s):
    """Swap the stand-in's stub for the real memoising lookup."""
    s._terrain_store = scan.ScanScreen._terrain_store.__get__(s)


# ---- the field bug: a refusal nobody could see -------------------------------

def test_refusal_with_no_terrain_opens_the_panel_and_says_why():
    s = _Screen(store=None)
    scan.ScanScreen._toggle_terrain(s)
    assert s._terrain_on is False              # honestly refused, not forced on
    assert s.plot.terrain_calls == []          # nothing painted
    assert s.terrain_btn.text == "Terrain  off"
    # the explanation must be somewhere the operator can SEE: the panel that
    # holds dl_status is opened before the status is written
    assert s.offline_toggles == 1 and s._offline_open is True
    assert len(s.statuses) == 1
    text, level = s.statuses[0]
    assert "terrain" in text.lower() and level == "alert"


def test_refusal_does_not_close_a_panel_that_is_already_open():
    s = _Screen(store=None, offline_open=True)
    scan.ScanScreen._toggle_terrain(s)
    assert s._offline_open is True             # still open — message visible
    assert s.offline_toggles == 0              # an unconditional flip would hide it
    assert s.statuses and s.statuses[0][1] == "alert"


def test_toggle_switches_on_and_off_when_terrain_exists():
    store = object()
    s = _Screen(store=store)
    scan.ScanScreen._toggle_terrain(s)
    assert s._terrain_on is True
    assert s.plot.terrain_calls == [store]
    assert s.terrain_btn.text == "Terrain  on"
    assert s.offline_toggles == 0              # no lecture when it just works
    scan.ScanScreen._toggle_terrain(s)
    assert s._terrain_on is False
    assert s.plot.terrain_calls == [store, None]
    assert s.terrain_btn.text == "Terrain  off"


# ---- the memoised None: downloaded terrain must not need a restart -----------

def _summary(fetched=3):
    return {"fetched": fetched, "skipped": 0, "failed": 0}


def test_download_done_forgets_the_memoised_terrain_store(tmp_path, monkeypatch):
    base = str(tmp_path / "offline.mbtiles")
    open(base, "w").close()
    s = _Screen(tiles=base)
    _use_real_terrain_store(s)
    # Operator taps Terrain before any terrain exists: None is memoised.
    assert s._terrain_store() is None
    assert s._terrain_cache is None
    # The download then delivers terrain beside the basemap…
    open(terrain_dest(base), "w").close()
    monkeypatch.setattr(scan, "find_mbtiles", lambda: base)
    scan.ScanScreen._download_done(s, _summary())
    # …and the very next tap must find it, in THIS session.
    got = s._terrain_store()
    assert got is not None and got.path == terrain_dest(base)


def test_download_done_repoints_a_live_overlay_at_the_fresh_terrain(
        tmp_path, monkeypatch):
    old = str(tmp_path / "old.mbtiles")
    new = str(tmp_path / "offline.mbtiles")
    for p in (old, terrain_dest(old), new, terrain_dest(new)):
        open(p, "w").close()
    s = _Screen(tiles=old)
    _use_real_terrain_store(s)
    scan.ScanScreen._toggle_terrain(s)         # overlay ON, reading old terrain
    assert s._terrain_on is True
    monkeypatch.setattr(scan, "find_mbtiles", lambda: new)
    scan.ScanScreen._download_done(s, _summary())
    assert s._terrain_on is True
    assert s.plot.terrain_calls[-1].path == terrain_dest(new)


def test_download_done_turns_the_overlay_off_when_the_new_map_has_no_terrain(
        tmp_path, monkeypatch):
    old = str(tmp_path / "old.mbtiles")
    new = str(tmp_path / "offline.mbtiles")   # deliberately no terrain beside it
    for p in (old, terrain_dest(old), new):
        open(p, "w").close()
    s = _Screen(tiles=old)
    _use_real_terrain_store(s)
    scan.ScanScreen._toggle_terrain(s)
    assert s._terrain_on is True
    monkeypatch.setattr(scan, "find_mbtiles", lambda: new)
    scan.ScanScreen._download_done(s, _summary())
    # "Terrain on" over a map with no terrain would be the button lying.
    assert s._terrain_on is False
    assert s.plot.terrain_calls[-1] is None
    assert s.terrain_btn.text == "Terrain  off"


# ---- wiring guard: the button stays bound to the handler ---------------------

def test_terrain_button_is_bound_to_the_toggle():
    body = func_source(SCREEN, "__init__", cls="ScanScreen")
    assert "terrain_btn.bind" in body
    assert "_toggle_terrain" in body
