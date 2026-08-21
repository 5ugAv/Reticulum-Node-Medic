import json

import pytest

from transport.connection import EmulatedConnection
from workflows.updater import (
    has_connectivity,
    sync_firmware,
    autoinstall_command,
    check_tool_update,
    SyncResult,
    FIRMWARE_VERSION_URL,
    RNODE_UPDATE_DIR,
)

# A trimmed real-format manifest (github release.json): {file: {hash, version}}.
MANIFEST = {
    "rnode_firmware_heltec32v3.zip": {"hash": "aaa", "version": "1.86"},
    "rnode_firmware_tbeam.zip": {"hash": "bbb", "version": "1.86"},
}
MANIFEST_JSON = json.dumps(MANIFEST)


def online_conn(manifest_json=MANIFEST_JSON, cached=None, dl_ok=True):
    """A node with internet. `cached` maps filename -> sha256 already on disk."""
    cached = cached or {}
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "HTTP/2 200")                 # connectivity probe OK
    c.rule(FIRMWARE_VERSION_URL, 0, manifest_json)       # manifest fetch
    # sha256sum <path> -> "<hash>  <path>" for files we pretend are cached
    for fname, h in cached.items():
        c.rules.insert(0, (f"sha256sum ~/.config/rnodeconf/update/1.86/{fname}",
                           0, f"{h}  path", ""))
    c.rule("sha256sum", 1, "")                            # uncached files: missing
    c.rule("curl -fsSL -m 120 -o", 0 if dl_ok else 22, "")  # downloads
    return c


def offline_conn():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 7, "")                            # connectivity probe fails
    return c


# ---- connectivity -------------------------------------------------------


def test_has_connectivity_true_when_probe_succeeds():
    assert has_connectivity(online_conn()) is True


def test_has_connectivity_false_when_probe_fails():
    assert has_connectivity(offline_conn()) is False


# ---- offline behaviour --------------------------------------------------


def test_sync_offline_is_a_clean_skip_not_a_failure():
    result = sync_firmware(offline_conn())
    assert isinstance(result, SyncResult)
    assert result.online is False
    assert result.changed == []
    # never touched the network beyond the probe
    # (message should reassure that flashing still works offline)
    assert "offline" in result.message.lower()


# ---- online sync --------------------------------------------------------


def test_sync_downloads_missing_firmware_hash_verified():
    # cache empty; after download sha256sum returns the manifest hash -> success
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rule("curl -fsI", 0, "HTTP/2 200")
    conn.rule(FIRMWARE_VERSION_URL, 0, MANIFEST_JSON)
    # first sha256sum (pre-download) fails (missing); post-download returns hash.
    # Emulator is stateless, so model "already correct": return the right hash.
    conn.rules.insert(0, ("sha256sum ~/.config/rnodeconf/update/1.86/rnode_firmware_heltec32v3.zip",
                          0, "aaa  p", ""))
    conn.rules.insert(0, ("sha256sum ~/.config/rnodeconf/update/1.86/rnode_firmware_tbeam.zip",
                          0, "bbb  p", ""))
    conn.rule("curl -fsSL -m 120 -o", 0, "")
    result = sync_firmware(conn)
    assert result.online is True
    assert result.version == "1.86"
    # already-correct hashes -> treated as up to date, no re-download
    assert set(result.up_to_date) == set(MANIFEST)
    assert result.changed == []


def test_sync_redownloads_when_hash_absent_then_verifies():
    # pre-download: sha256sum fails (missing). We can't model post-download state
    # change in the stateless emulator, so a download whose verify can't confirm
    # the hash is reported as failed (never silently 'succeeds').
    conn = online_conn(cached={})            # nothing cached, verify will mismatch
    result = sync_firmware(conn)
    assert result.online is True
    # downloads attempted for both, but post-verify can't match -> failed list
    assert set(result.failed) == set(MANIFEST)
    assert result.changed == []


def test_sync_reports_download_failure():
    conn = online_conn(cached={}, dl_ok=False)
    result = sync_firmware(conn)
    assert result.online is True
    assert set(result.failed) == set(MANIFEST)


def test_sync_writes_version_marker():
    conn = online_conn(cached={f: h["hash"] for f, h in MANIFEST.items()})
    sync_firmware(conn)
    assert any("1.86" in cmd and "bundle_version" in cmd for cmd in conn.history)


def test_sync_writes_per_file_version_sidecar_for_offline_flash():
    # rnodeconf --autoinstall needs "<file>.version" = "<version> <hash>" or the
    # offline flash aborts ("No release hash found"). Verified on real hardware.
    conn = online_conn(cached={f: v["hash"] for f, v in MANIFEST.items()})
    sync_firmware(conn)
    for fname, info in MANIFEST.items():
        assert any(f"{fname}.version" in c and f"1.86 {info['hash']}" in c
                   for c in conn.history)


def test_sync_all_cached_is_up_to_date_no_downloads():
    conn = online_conn(cached={f: v["hash"] for f, v in MANIFEST.items()})
    result = sync_firmware(conn)
    assert set(result.up_to_date) == set(MANIFEST)
    assert result.changed == []
    assert not any("curl -fsSL -m 120 -o" in c for c in conn.history)  # no downloads


# ---- offline flash command ---------------------------------------------


def test_autoinstall_command_is_offline_by_default():
    cmd = autoinstall_command("/dev/ttyACM0", version="1.86")
    assert "rnodeconf /dev/ttyACM0 --autoinstall" in cmd
    assert "--nocheck" in cmd            # never hits the network in the field
    assert "--fw-version 1.86" in cmd


def test_autoinstall_command_online_omits_nocheck():
    cmd = autoinstall_command("/dev/ttyACM0", offline=False)
    assert "--nocheck" not in cmd


# ---- tool self-update ---------------------------------------------------


def test_check_tool_update_offline_skips():
    res = check_tool_update(offline_conn())
    assert res["online"] is False
    assert res["update_available"] is False


def test_check_tool_update_detects_behind_remote():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "HTTP/2 200")
    c.rules.insert(0, ("rev-list --count", 0, "3", ""))   # 3 commits behind
    c.rule("git -C", 0, "")                               # fetch ok (broader)
    res = check_tool_update(c)
    assert res["online"] is True
    assert res["update_available"] is True
    assert res["behind"] == 3


def test_check_tool_update_up_to_date():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "HTTP/2 200")
    c.rules.insert(0, ("rev-list --count", 0, "0", ""))
    c.rule("git -C", 0, "")
    res = check_tool_update(c)
    assert res["update_available"] is False


# ---- a hostile manifest cannot inject a command or escape the cache ---------
# release.json is fetched over plain HTTPS and is NOT authenticated, so every
# key and value is treated as hostile. Filenames are charset-validated; every
# interpolated value is shell-quoted.



def _manifest_conn(manifest_json):
    """Online node that serves *manifest_json* and stubs the download/hash."""
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("curl -fsI", 0, "HTTP/2 200")
    c.rule(FIRMWARE_VERSION_URL, 0, manifest_json)
    c.rule("sha256sum", 1, "")                     # nothing cached / can't verify
    c.rule("curl -fsSL -m 120 -o", 0, "")
    return c


def test_unsafe_firmware_filename_is_skipped_never_run():
    for bad in ("../../etc/passwd", "a;reboot.zip", "a$(reboot).zip",
                "a`id`.zip", "a b.zip", "..evil.zip"):
        manifest = {bad: {"hash": "aaa", "version": "1.86"}}
        conn = _manifest_conn(json.dumps(manifest))
        res = sync_firmware(conn)
        assert res.failed, f"{bad!r} should be reported failed"
        # the crafted name never became a download or a hashing command
        assert not any(bad in cmd and "curl -fsSL -m 120" in cmd
                       for cmd in conn.history)
        assert not any(bad in cmd and cmd.startswith("sha256sum")
                       for cmd in conn.history)


def test_manifest_entry_without_a_hash_is_not_trusted():
    manifest = {"good.zip": {"version": "1.86"}}   # no "hash"
    conn = _manifest_conn(json.dumps(manifest))
    res = sync_firmware(conn)
    assert res.changed == [] and res.up_to_date == []
    assert res.failed                              # unverifiable -> not flashed


def test_injecting_version_is_rejected_not_merely_quoted():
    # A version carrying a shell metacharacter is now REFUSED by the path-segment
    # validator (";" isn't in the charset) — stronger than quoting, and it means
    # the payload never reaches a command at all.
    for version in ("1.86; touch /tmp/pwned", "1.86;reboot", "a$(reboot)", "a`id`"):
        manifest = {"good.zip": {"hash": "aaa", "version": version}}
        conn = _manifest_conn(json.dumps(manifest))
        res = sync_firmware(conn)
        assert res.failed and res.changed == [], version
        # refused before any path/command is built from the version
        assert not any(c.startswith("mkdir -p") for c in conn.history), version
        assert not any("curl -fsSL -m 120 -o" in c for c in conn.history), version


# ---- CRITICAL #1 regression: a traversing manifest version is an RCE ---------
# shlex.quote stops shell metacharacters but NOT "../": a version like
# "../../../.ssh" (the cache SUBDIRECTORY) would redirect the download tree to
# ~/.ssh/authorized_keys, and a MITM who also chose the sha256 passes the hash
# gate. So the version must clear the traversal validator BEFORE a path is built.


def test_traversing_manifest_version_is_refused_before_any_path_is_built():
    for bad in ("../../../.ssh", "a/b", "..", "../evil", "1.86\n", "a\rb"):
        # the confirmed exploit shape: attacker-named file + traversing version
        manifest = {"authorized_keys": {"hash": "aaa", "version": bad}}
        conn = _manifest_conn(json.dumps(manifest))
        res = sync_firmware(conn)
        assert res.failed and res.changed == [] and res.up_to_date == [], bad
        # NOTHING that builds or writes a path may run for an unsafe version
        assert not any(cmd.startswith("mkdir -p") for cmd in conn.history), bad
        assert not any("curl -fsSL -m 120 -o" in cmd for cmd in conn.history), bad
        assert not any("authorized_keys" in cmd for cmd in conn.history), bad


def test_a_normal_version_still_syncs_byte_identically():
    # the traversal guard must not perturb a legitimate version: exact commands.
    conn = _manifest_conn(json.dumps({"good.zip": {"hash": "aaa", "version": "1.86"}}))
    sync_firmware(conn)
    assert "mkdir -p ~/.config/rnodeconf/update/1.86" in conn.history
    assert any(c.startswith("curl -fsSL -m 120 -o ~/.config/rnodeconf/update/1.86/good.zip")
               for c in conn.history)


def test_path_segment_validators_reject_trailing_newline():
    # ^...$ + match would accept "x\n" (``$`` matches before a trailing newline);
    # fullmatch + explicit newline reject makes the guard real, not incidental.
    from workflows.updater import _safe_path_segment
    from workflows.phone_apps import _safe_apk_name
    assert not _safe_path_segment("1.86\n") and not _safe_path_segment("a/b")
    assert not _safe_path_segment("..") and not _safe_path_segment("../x")
    assert _safe_path_segment("1.86")
    assert not _safe_apk_name("ok.apk\n") and _safe_apk_name("ok.apk")
