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


# --- APK variant selection ---------------------------------------------------
# Columba v2.0.9 shipped 28 assets in one release. The old rule took the first
# name containing "universal", which was an EXPERIMENTAL build — chosen by
# alphabetical order rather than by anyone. These pin the order of preference.

def _asset(name, size=1000):
    return {"name": name, "browser_download_url": f"https://x/{name}", "size": size}


def test_pick_apk_rejects_experimental_even_when_it_sorts_first():
    picked = _pick_apk([
        _asset("columba-2.0.9-EXPERIMENTAL-reticulum-kt-universal.apk", 56_857_930),
        _asset("columba-2.0.9-official-rns-py-universal.apk", 105_321_979),
    ])
    assert "official" in picked["name"]
    assert "EXPERIMENTAL" not in picked["name"]


def test_pick_apk_prefers_universal_over_arch_split():
    picked = _pick_apk([
        _asset("app-arm64-v8a.apk", 99_000_000),      # bigger, but installs on fewer phones
        _asset("app-universal.apk", 50_000_000),
    ])
    assert picked["name"] == "app-universal.apk"


def test_pick_apk_prefers_the_build_without_crash_telemetry():
    picked = _pick_apk([
        _asset("app-universal.apk", 105_321_979),
        _asset("app-universal-no-sentry.apk", 102_994_600),
    ])
    assert picked["name"] == "app-universal-no-sentry.apk"


def test_pick_apk_falls_back_rather_than_returning_nothing():
    # A release offering ONLY a pre-release build still yields it: narrowing must
    # never empty the list, or the medic carries nothing at all.
    picked = _pick_apk([_asset("app-beta-arm64.apk", 5)])
    assert picked is not None and "beta" in picked["name"]


def test_pick_apk_ignores_play_store_bundles():
    # .aab is a Play Store bundle. It cannot be sideloaded, so it must never win.
    picked = _pick_apk([_asset("app-universal.aab", 99_000_000),
                        _asset("app-arm64.apk", 10)])
    assert picked["name"].endswith(".apk")


def test_pick_apk_takes_the_largest_of_equals():
    picked = _pick_apk([_asset("a-arm64.apk", 10), _asset("b-x86_64.apk", 99)])
    assert picked["name"] == "b-x86_64.apk"


# --- disk-space guard --------------------------------------------------------
# These APKs are 100 MB and up. A download that fills the card fails late, having
# already written most of the file, and leaves the medic worse off than before.

class _SpaceConn(EmulatedConnection):
    """Online, with a controllable amount of free space and a download counter."""

    def __init__(self, free_kb, release_json):
        super().__init__()
        self.free_kb = free_kb
        self.release_json = release_json
        self.downloads = 0

    def run(self, cmd, *a, **k):
        if cmd.startswith("curl -fsI"):          # connectivity probe
            return 0, "", ""
        if "api.github.com" in cmd:
            return 0, self.release_json, ""
        if cmd.startswith("df -Pk"):
            return 0, f"{self.free_kb}\n", ""
        if cmd.startswith("stat -c"):            # nothing cached yet
            return 1, "", ""
        if cmd.startswith("curl -fsSL"):
            self.downloads += 1
            return 0, "", ""
        return 0, "", ""


def test_sync_app_refuses_when_the_card_would_fill():
    rel = json.dumps({"tag_name": "v1", "assets": [
        {"name": "columba-universal.apk", "size": 100 * 1024 * 1024,
         "browser_download_url": "https://x/c.apk"}]})
    c = _SpaceConn(free_kb=120 * 1024, release_json=rel)   # 120 MB free, needs 100 MB
    res = sync_app("columba", c)
    assert res.failed, "a 100 MB download with 120 MB free must be refused"
    assert c.downloads == 0, "must refuse BEFORE downloading, not after"
    assert "free" in res.message.lower()


def test_sync_app_proceeds_when_there_is_room():
    rel = json.dumps({"tag_name": "v1", "assets": [
        {"name": "columba-universal.apk", "size": 100 * 1024 * 1024,
         "browser_download_url": "https://x/c.apk"}]})
    c = _SpaceConn(free_kb=2 * 1024 * 1024, release_json=rel)   # 2 GB free
    sync_app("columba", c)
    assert c.downloads == 1
