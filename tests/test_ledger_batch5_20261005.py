"""Readiness ledger, 2026-10-05 early — privacy, clone hygiene, a UI freeze:

  #109  SPEC.md named the operator and their city
  #95 #107 #209  the developer's pet name for his radio on a stranger's screen
  #162  exact confirmed coordinates in ~/ui.log
  #161  the medic's internet address sent to two geolocation services at start-up
  #160  ~100 MB of APKs fetched on any connection with no disclosure
  #127 #126  developer scratch rode to every clone; the clone needed the
             developer's personal directories to finish
  #81 #188  rnpath run on the Kivy thread on every chat redraw
"""
import ast
import glob
import json
import os

from tests.srcutil import ROOT, src


def _tr_literals(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "tr" and node.args
                and isinstance(node.args[0], ast.Constant)):
            yield node.args[0].value


# -- #109 ------------------------------------------------------------------

def test_the_original_spec_is_history_and_names_nobody():
    assert not os.path.exists(os.path.join(ROOT, "SPEC.md"))
    assert not os.path.exists(os.path.join(ROOT, "TODO.md"))
    spec = src("docs/history/SPEC.md")
    assert spec.startswith("# Reticulum Node Medic — Specification (original, July 2026)")
    assert "superseded by `README.md`" in spec
    assert "Operator:" not in spec
    assert "docs/history/SPEC.md" in src("README.md")


# -- #95 / #107 / #209 -----------------------------------------------------

def test_no_pet_name_reaches_a_screen_or_an_error():
    for path in glob.glob(os.path.join(ROOT, "ui", "**", "*.py"), recursive=True):
        for lit in _tr_literals(path):
            assert "Jonesey" not in lit, (path, lit)
    roster = src("ui/onboard_roster.py")
    assert "(its own radio / GPS) — never a flash, adopt or PROBE target." in roster
    assert "(Jonesey / GPS)" not in roster
    sd = src("ui/screens/self_diagnose_screen.py")
    assert "Checks this medic's own radio and GPS board" in sd
    # the subtitle grows to its text now — no pinned h=36 on that label
    sub = sd[sd.index("Checks this medic's own radio"):sd.index("self.run_btn = Button(")]
    assert "h=36" not in sub
    for code in ("es", "fr", "de", "ja", "ru", "pl", "id", "sv"):
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
            cat = json.load(f)
        assert not any("Jonesey" in k for k in cat), code


# -- #162 ------------------------------------------------------------------

def test_no_exact_coordinates_are_printed_to_the_log():
    app = src("ui/app.py")
    assert "location confirmed ({source}) -> BIRTH" in app
    for path in glob.glob(os.path.join(ROOT, "ui", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "print(" in line:
                    assert "lat:.6f" not in line and "lon:.6f" not in line, (path, line)


# -- #161 ------------------------------------------------------------------

def test_ip_geolocation_is_one_https_service_and_only_on_request():
    md = src("ui/map_download.py")
    assert "http://ip-api.com" not in md and "https://ipinfo.io/json" in md
    from ui.map_download import ip_geolocate
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"loc": "-37.8,144.9", "city": "Somewhere"})
    assert ip_geolocate(fetch=fetch)[:2] == (-37.8, 144.9)
    assert calls == ["https://ipinfo.io/json"]
    sc = src("ui/screens/scan_screen.py")
    init = sc[sc.index("def __init__(self, nodes=None, tiles=None, gps_reader=None"):
              sc.index("def _reflect_tiles")]
    assert "threading.Thread(target=self._locate_self" not in init   # not at start-up
    assert 'tr("Find my area from the internet")' in sc
    assert "area asks ipinfo.io, which sees this medic's" in sc
    on = sc[sc.index("def _on_download(self):"):sc.index("def _step_radius") if sc.index("def _step_radius") > sc.index("def _on_download(self):") else None]
    assert '== "locate"' in on and "self._locate_self" in on


# -- #160 ------------------------------------------------------------------

def test_the_app_shelf_never_refreshes_on_a_metered_link_and_the_doc_says_so():
    app = src("ui/app.py")
    stock = app[app.index("def _stock_apps():"):app.index("def _build_scan_topology")]
    assert "nmcli -g GENERAL.METERED connection show" in stock
    assert 'startswith("yes")' in stock
    doc = src("docs/PHONE_APPS.md")
    assert "**refreshes** what it carries by itself" in doc and "unmetered" in doc
    assert "It **cannot** refresh what it carries without a connection." not in doc


# -- #127 / #126 -----------------------------------------------------------

def test_developer_scratch_never_rides_to_a_clone():
    from workflows.clone import TOOL_EXCLUDES, CARRIED_TREES
    for name in (".git", ".claude", ".local-backups", "*.log", "typescript", ".kivy",
                 "docs/previews", ".venv", "htmlcov"):
        assert name in TOOL_EXCLUDES, name
    assert [p for p, _w, req in CARRIED_TREES if req] == ["~/pi_os_lite.img.xz"]
    assert any(p == "~/.platformio" for p, _w, _r in CARRIED_TREES)


# -- #81 / #188 ------------------------------------------------------------

def test_the_route_line_is_cached_and_computed_off_the_kivy_thread():
    cs = src("monitor/chat_service.py")
    assert '"bash", "-lc"' not in cs
    assert "ROAD_CACHE_S = 30.0" in cs and "_road_cache" in cs
    assert "def _rnpath_bin()" in cs
    sc = src("ui/screens/chat_screen.py")
    assert "def _fill_route_line(self, label):" in sc
    assert 'tr("finding the route…")' in sc
    assert "_lbl(self._route_line()," not in sc


def test_rnpath_is_asked_once_per_peer_per_window(monkeypatch):
    from monitor import chat_service as m
    calls = []

    class _Svc:
        _run = staticmethod(lambda cmd: calls.append(cmd) or "[]")
        ROAD_CACHE_S = m.ChatService.ROAD_CACHE_S
        _rnsd_road = m.ChatService._rnsd_road
    s = _Svc()
    dh = bytes.fromhex("ab" * 16)
    assert s._rnsd_road(dh) is None
    assert s._rnsd_road(dh) is None
    assert s._rnsd_road(bytes.fromhex("cd" * 16)) is None
    assert len(calls) == 2            # one per peer; the repeat was served from the cache
