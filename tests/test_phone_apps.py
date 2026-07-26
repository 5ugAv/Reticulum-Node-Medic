"""Reticulum phone-app cache (Columba + Sideband) — no live network."""

import json

from transport.connection import EmulatedConnection
from workflows.phone_apps import (
    sync_app, sync_all, cached_app, cached_apps, _pick_apk, APPS, APPS_CACHE_DIR,
)

CBA = "columba-universal-release.apk"
SIZE = 12_345_678


def _release(name=CBA, size=SIZE, tag="v0.1"):
    return json.dumps({"tag_name": tag, "assets": [
        {"name": "app-arm64.apk", "browser_download_url": "https://x/a.apk", "size": 9},
        {"name": name, "browser_download_url": f"https://x/{name}", "size": size}]})


def _online(cached_size=None, written=SIZE, release=None):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "HTTP/2 200")
    c.rule("curl -fsSL -m 20", 0, release or _release())
    c.rule("curl -fsSL -m 300 -o", 0, "")
    c.rule("stat -c %s", 1 if cached_size is None else 0,
           "" if cached_size is None else str(cached_size))
    c.rule("wc -c <", 0, str(written))
    return c


def test_catalogue_has_columba_and_sideband():
    assert set(APPS) == {"columba", "sideband"}
    assert all(a["free_redistribution"] for a in APPS.values())


def test_sync_columba_fresh_download():
    c = _online()
    res = sync_app("columba", c)
    assert res.online and res.changed == [CBA] and res.version == "v0.1"
    meta = next(cmd for cmd in c.history if f"{CBA}.meta" in cmd)
    assert "MPL-2.0" in meta and "columba" in meta
    assert any(".columba_version" in cmd for cmd in c.history)


def test_sync_sideband_uses_its_repo_and_licence():
    c = _online(release=_release(name="Sideband.apk", tag="1.9.6"))
    res = sync_app("sideband", c)
    assert res.changed == ["Sideband.apk"]
    assert any("markqvist/Sideband" in cmd for cmd in c.history)     # right repo
    meta = next(cmd for cmd in c.history if "Sideband.apk.meta" in cmd)
    assert "CC-BY-NC-SA" in meta


def test_already_cached_skips_download():
    c = _online(cached_size=SIZE)
    res = sync_app("columba", c)
    assert res.up_to_date == [CBA]
    assert not any("curl -fsSL -m 300 -o" in cmd for cmd in c.history)


def test_corrupt_download_discarded():
    c = _online(written=42)
    res = sync_app("columba", c)
    assert res.failed == [CBA]
    assert any(cmd.startswith("rm -f") for cmd in c.history)


def test_offline_is_noop():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 7, "")
    res = sync_app("columba", c)
    assert res.online is False and not res.changed


def test_unknown_app_rejected():
    res = sync_app("whatsapp", EmulatedConnection())
    assert res.failed == ["whatsapp"]


def test_sync_all_hits_every_app():
    c = _online()
    results = sync_all(c)
    assert set(results) == {"columba", "sideband"}


def test_cached_app_matches_by_filename():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("ls -1", 0, f"{APPS_CACHE_DIR}/columba-2.0.9-universal.apk\n"
                       f"{APPS_CACHE_DIR}/Sideband_1.9.6.apk")
    c.rule(f"cat {APPS_CACHE_DIR}/.sideband_version", 0, "1.9.6")
    c.rule("cat", 0, "2.0.9")
    col = cached_app("columba", c)
    sb = cached_app("sideband", c)
    assert col["file"] == "columba-2.0.9-universal.apk" and col["name"] == "Columba"
    assert sb["file"] == "Sideband_1.9.6.apk" and sb["version"] == "1.9.6"


def test_cached_apps_lists_carried_and_missing():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("ls -1", 0, f"{APPS_CACHE_DIR}/columba-2.0.9-universal.apk")
    c.rule("cat", 0, "2.0.9")
    rows = {r["key"]: r for r in cached_apps(c)}
    assert rows["columba"]["carried"] is True
    assert rows["sideband"]["carried"] is False      # not on disk
    assert rows["sideband"]["name"] == "Sideband"    # still catalogued
