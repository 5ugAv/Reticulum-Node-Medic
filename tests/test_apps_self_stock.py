"""The medic stocks its own app shelf (operator, 2026-09-13).

"This should be part of the initial build — the user shouldn't have to
download these communication apps. And if a device is cloned, they're
already inside that clone."

The clone half was already true: workflows/clone.py rsyncs the whole tool
tree, and the APK cache lives inside it (assets/apps). These tests pin the
other half — a medic that is online and missing its apps fetches them
ITSELF, quietly, sha256-verified by the existing downloader — and guard the
clone half against an "optimisation" that excludes the cache from the copy.
"""
from workflows.phone_apps import should_autosync


def test_missing_apps_and_online_means_sync():
    ok, why = should_autosync(all_carried=False, age_days=0.0, online=True)
    assert ok and "missing" in why


def test_offline_never_syncs_whatever_is_missing():
    ok, why = should_autosync(all_carried=False, age_days=99, online=False)
    assert not ok and "offline" in why


def test_fully_stocked_and_fresh_stays_quiet():
    ok, why = should_autosync(all_carried=True, age_days=2.0, online=True)
    assert not ok


def test_fully_stocked_but_stale_refreshes():
    """Versions move (the operator's screen said 'updated' today); a weekly
    look keeps a clone-parent's carried APKs worth inheriting."""
    ok, why = should_autosync(all_carried=True, age_days=8.0, online=True)
    assert ok and "stale" in why


def test_the_app_wires_the_autosync_at_startup():
    from tests.srcutil import src
    s = src("ui/app.py")
    assert "should_autosync" in s and "sync_all" in s, (
        "the medic must stock its own app shelf — no user download step")


def test_the_clone_never_excludes_the_app_cache():
    from workflows.clone import TOOL_EXCLUDES
    assert not any("apps" in e or "apk" in e for e in TOOL_EXCLUDES), (
        "excluding assets/apps from the clone breaks 'already inside that "
        "clone' (operator, 2026-09-13)")
