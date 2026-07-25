"""Columba offline APK cache + GitHub sync — no live network (EmulatedConnection)."""

import json

from transport.connection import EmulatedConnection
from workflows.columba import (
    sync_columba,
    cached_columba,
    _pick_apk,
    COLUMBA_LATEST_API,
    COLUMBA_LICENSE,
    APPS_CACHE_DIR,
)

APK = "columba-universal-release.apk"
SIZE = 12_345_678
RELEASE = {
    "tag_name": "v0.10.5-beta",
    "assets": [
        {"name": "columba-arm64-release.apk",
         "browser_download_url": "https://x/arm64.apk", "size": 9_000_000},
        {"name": APK, "browser_download_url": f"https://x/{APK}", "size": SIZE},
    ],
}
RELEASE_JSON = json.dumps(RELEASE)


def _online(cached_size=None, dl_ok=True, written=SIZE, release_json=RELEASE_JSON):
    """A node with internet. `cached_size` = size stat reports for the cached apk
    (None -> not cached). `written` = size the downloaded file ends up (for the
    post-download integrity check)."""
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "HTTP/2 200")                 # connectivity OK
    c.rule("curl -fsSL -m 20", 0, release_json)          # release API fetch
    c.rule("curl -fsSL -m 300 -o", 0 if dl_ok else 22, "")   # download
    if cached_size is None:
        c.rule("stat -c %s", 1, "")                      # not cached
    else:
        c.rule("stat -c %s", 0, str(cached_size))        # cached, this size
    c.rule("wc -c <", 0, str(written))                   # downloaded size
    return c


def _offline():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 7, "")                           # connectivity fails
    return c


# ---- offline -----------------------------------------------------------------

def test_offline_is_a_clean_noop():
    res = sync_columba(_offline())
    assert res.online is False
    assert not res.changed and not res.failed
    assert "offline" in res.message.lower()


# ---- online: fresh download --------------------------------------------------

def test_online_fresh_download_caches_apk():
    c = _online(cached_size=None, written=SIZE)
    res = sync_columba(c)
    assert res.online is True
    assert res.version == "v0.10.5-beta"
    assert res.changed == [APK]                          # the universal apk
    # wrote the version marker + a .meta sidecar carrying the licence
    assert any(".columba_version" in cmd for cmd in c.history)
    meta_cmd = next(cmd for cmd in c.history if f"{APK}.meta" in cmd)
    assert COLUMBA_LICENSE in meta_cmd and "Columba" in meta_cmd


def test_online_already_cached_skips_download():
    c = _online(cached_size=SIZE)                         # already the right size
    res = sync_columba(c)
    assert res.up_to_date == [APK]
    assert not res.changed
    assert not any("curl -fsSL -m 300 -o" in cmd for cmd in c.history)  # no download


def test_corrupt_download_is_discarded():
    c = _online(cached_size=None, written=999)            # wrong size after DL
    res = sync_columba(c)
    assert res.failed == [APK]
    assert not res.changed
    assert any(cmd.startswith("rm -f") for cmd in c.history)   # bad file removed


def test_download_failure_reported():
    c = _online(cached_size=None, dl_ok=False)
    res = sync_columba(c)
    assert res.failed == [APK] and not res.changed


def test_unreadable_release_is_handled():
    c = _online(release_json="not json")
    res = sync_columba(c)
    assert res.online is True and not res.changed
    assert "could not read" in res.message.lower()


# ---- apk selection -----------------------------------------------------------

def test_pick_apk_prefers_universal():
    a = _pick_apk(RELEASE["assets"])
    assert a["name"] == APK


def test_pick_apk_falls_back_to_largest_when_no_universal():
    assets = [
        {"name": "columba-armv7.apk", "size": 5},
        {"name": "columba-arm64.apk", "size": 9},
        {"name": "notes.txt", "size": 100},
    ]
    assert _pick_apk(assets)["name"] == "columba-arm64.apk"


def test_pick_apk_none_when_no_apk():
    assert _pick_apk([{"name": "readme.md", "size": 1}]) is None


# ---- cached_columba ----------------------------------------------------------

def test_cached_columba_reports_carried_apk():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("ls -1", 0, f"{APPS_CACHE_DIR}/{APK}")
    c.rule("cat", 0, "v0.10.5-beta")
    info = cached_columba(c)
    assert info["file"] == APK
    assert info["version"] == "v0.10.5-beta"
    assert info["license"] == COLUMBA_LICENSE


def test_cached_columba_none_when_empty():
    c = EmulatedConnection(default_code=2, default_stdout="")
    c.rule("ls -1", 2, "")
    assert cached_columba(c) is None
