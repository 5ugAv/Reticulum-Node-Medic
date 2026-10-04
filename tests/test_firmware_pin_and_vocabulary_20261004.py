"""Two operator decisions of 2026-10-04.

1. RNode firmware is PINNED at 1.86 ("a lot of work was done on 1.86 — the
   health data, the RGB lights. If it ain't broke, don't fix it. Lock it
   down."): Field readiness repairs that bundle and reports a newer upstream
   release without fetching it.
2. The medic is called a propagation node everywhere the operator reads —
   "post office" is gone from the screens, the catalogs and the docs.
"""
import ast
import json
import os

from diagnostics.radio_firmware import LATEST_FIRMWARE
from transport.connection import EmulatedConnection
from workflows import updater
from workflows.updater import (FIRMWARE_LATEST_URL, FIRMWARE_VERSION_URL,
                               PINNED_FIRMWARE, sync_firmware)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGS = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def _manifest(version):
    return json.dumps({"rnode_firmware_heltec32v3.zip": {"hash": "aaa", "version": version}})


def _conn(pinned_manifest, latest_manifest=None):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "HTTP/2 200")
    c.rule(FIRMWARE_VERSION_URL, 0, pinned_manifest)
    if latest_manifest is not None:
        c.rule(FIRMWARE_LATEST_URL, 0, latest_manifest)
    c.rules.insert(0, (f"sha256sum ~/.config/rnodeconf/update/{PINNED_FIRMWARE}/"
                       "rnode_firmware_heltec32v3.zip", 0, "aaa  p", ""))
    return c


# -- the pin -------------------------------------------------------------------

def test_the_pin_is_one_version_everywhere():
    assert PINNED_FIRMWARE == "1.86" == LATEST_FIRMWARE
    assert f"/releases/download/{PINNED_FIRMWARE}/release.json" in FIRMWARE_VERSION_URL
    assert "/releases/latest/" not in FIRMWARE_VERSION_URL
    assert "/releases/latest/" in FIRMWARE_LATEST_URL


def test_sync_fetches_only_the_pinned_release_and_reports_a_newer_one():
    conn = _conn(_manifest("1.86"), latest_manifest=_manifest("1.87"))
    res = sync_firmware(conn)
    assert res.version == "1.86" and res.up_to_date == ["rnode_firmware_heltec32v3.zip"]
    assert res.newer_available == "1.87"
    assert "not fetched" in res.message and "1.87" in res.message
    # nothing from the newer release was downloaded
    assert not any("curl -fsSL -m 120 -o" in c and "1.87" in c for c in conn.history)


def test_no_newer_release_means_no_notice():
    res = sync_firmware(_conn(_manifest("1.86"), latest_manifest=_manifest("1.86")))
    assert res.newer_available is None and "not fetched" not in res.message
    # an older or unreadable "latest" is not newer either
    assert sync_firmware(_conn(_manifest("1.86"), latest_manifest=_manifest("1.9"))).newer_available is None
    assert sync_firmware(_conn(_manifest("1.86"))).newer_available is None


def test_a_pinned_manifest_naming_another_version_is_refused():
    conn = _conn(_manifest("1.87"))
    res = sync_firmware(conn)
    assert res.failed and "pinned 1.86" in res.failed[0]
    assert "refusing" in res.message
    # the refusal comes before any write: no cache dir made, nothing fetched
    assert not any("mkdir -p" in c or "curl -fsSL -m 120 -o" in c for c in conn.history)


def test_field_readiness_passes_the_notice_through(monkeypatch):
    from workflows import phone_apps, wheelhouse
    from workflows.carry import carry_all
    from workflows.updater import SyncResult
    monkeypatch.setattr(updater, "sync_firmware",
                        lambda c, force=False: SyncResult(online=True, version="1.86",
                                                          up_to_date=["x"],
                                                          newer_available="1.87"))
    monkeypatch.setattr(phone_apps, "sync_all", lambda c: {})
    monkeypatch.setattr(wheelhouse, "wheel_count", lambda c, dest=None: 17)
    monkeypatch.setattr(wheelhouse, "cache_wheels", lambda c, **k: (True, "ok"))
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "")
    rep = carry_all(c)
    assert rep.topped_up == [] and "RNode firmware" in rep.checked
    assert any("1.87" in n and "not fetched" in n for n in rep.notes)
    assert "1.87" in rep.message


# -- the vocabulary -----------------------------------------------------------

def _tr_literals():
    found = set()
    for base, _d, files in os.walk(os.path.join(ROOT, "ui")):
        for fn in files:
            if fn.endswith(".py"):
                tree = ast.parse(open(os.path.join(base, fn), encoding="utf-8").read())
                for n in ast.walk(tree):
                    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                            and n.func.id == "tr" and n.args
                            and isinstance(n.args[0], ast.Constant)
                            and isinstance(n.args[0].value, str)):
                        found.add(n.args[0].value)
    return found


def test_no_screen_string_says_post_office():
    assert not [s for s in _tr_literals() if "post office" in s.lower()]


def test_no_catalog_key_and_no_doc_says_post_office():
    for code in LANGS:
        d = json.load(open(os.path.join(ROOT, "assets", "i18n", code + ".json"), encoding="utf-8"))
        assert not [k for k in d if "post office" in k.lower()], code
    for rel in ("docs/CHAT.md", "docs/PHONE_APPS.md"):
        assert "post office" not in open(os.path.join(ROOT, rel), encoding="utf-8").read().lower(), rel


def test_the_sync_log_line_names_the_propagation_node():
    s = open(os.path.join(ROOT, "monitor", "chat_service.py"), encoding="utf-8").read()
    assert "asked the propagation node for held messages" in s
    assert "post office" not in s.lower()
