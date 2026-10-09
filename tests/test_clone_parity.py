"""Clone parity: a cloned medic must be able to do everything its parent does.

The keeper, 2026-10-08: "Anything cloned needs to be able to do everything
that the original did." A sweep that diffed Node Medic 1 against a real clone
found an EoRa-S3 firmware tree that no clone ever received, five programs the
code runs that no clone had, and an About screen that read "unknown" on every
clone. These tests hold the clone to what the code builds from and runs, so
a new dependency cannot be left behind again.
"""
import os
import re
import subprocess

import pytest

from workflows import clone, wheelhouse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _carried(path):
    """A home path travels unless its top folder is on the never list."""
    return path.startswith("~/") and path[2:].split("/")[0] not in clone.HOME_NEVER


def test_every_firmware_folder_the_code_builds_from_is_carried():
    from workflows import (rnode_boards, rnode_flash, rnode_nrf52_rgb,
                           rnode_v4_rgb, rtnode_build)
    need = {rnode_flash.TRACKER_BUILD_DIR, rnode_boards.DEFAULT_FIRMWARE_DIR,
            rnode_v4_rgb.FIRMWARE_DIR, rnode_nrf52_rgb.FIRMWARE_DIR,
            rtnode_build.RTNODE_PROJECT_DIR, rtnode_build.TECHO_PROJECT_DIR}
    need |= {b.build_dir for b in rnode_boards.RNODE_BOARDS.values()
             if getattr(b, "build_dir", "")}
    missing = sorted(p for p in need if p.startswith("~/") and not _carried(p))
    assert not missing, f"no clone receives: {missing}"


def test_the_eora_s3_tree_travels():
    assert _carried("~/EoRa-S3/RNode_Firmware_CE")


def _fresh_card_packages():
    path = os.path.join(ROOT, "assets", "clone_base", "dpkg_status")
    with open(path, encoding="utf-8", errors="replace") as f:
        return set(re.findall(r"^Package: (\S+)$", f.read(), re.M))


def test_every_listed_program_reaches_a_clone():
    base = _fresh_card_packages()
    for prog, pkg in wheelhouse.PROGRAM_PACKAGES.items():
        assert pkg in wheelhouse.ALL_PACKAGES or pkg in base, (
            f"{prog} comes from {pkg}, which is neither on a fresh card nor carried")


def test_the_programs_the_sweep_found_missing_are_carried():
    for pkg in ("sshpass", "rpiboot", "acl", "python3-pip", "git",
                "wlr-randr", "uhubctl", "python3-pexpect"):
        assert pkg in wheelhouse.ALL_PACKAGES, pkg


#: Programs the code runs that every fresh Pi OS Lite card already has.
FRESH_CARD_PROGRAMS = {
    "sudo", "bash", "nmcli", "lsusb", "ssh-keygen", "ssh", "lsblk", "ip",
    "systemctl", "findmnt", "mount", "umount", "timedatectl", "sfdisk", "ping",
    "partprobe", "getent", "blockdev", "xz", "which", "vcgencmd", "udevadm",
    "sync", "resize2fs", "e2fsck", "raspi-config", "python3", "openssl", "ls",
    "iw", "fuser", "dpkg-query", "dmesg", "cat",
}
#: Programs that travel inside a carried tree rather than as a package.
CARRIED_PROGRAMS = {"rnodeconf", "esptool", "esptool.py", "pio",
                    "arduino-cli", "adafruit-nrfutil"}


def test_every_program_the_code_runs_reaches_a_clone():
    """A program run in list form must be on a fresh card, carried in a tree,
    or named in wheelhouse.PROGRAM_PACKAGES. A new one fails here until the
    clone is taught to bring it."""
    out = subprocess.run(
        ["git", "grep", "-h", "-o", "-E",
         r"(run|runner|Popen|check_output|check_call|call|_run|safe_shell\.run)"
         r"\(\s*\[\s*[\"'][A-Za-z0-9_.+-]+[\"']", "--", "*.py", ":!tests/*"],
        capture_output=True, text=True, cwd=ROOT).stdout
    progs = {m for m in re.findall(r"\[\s*[\"']([A-Za-z0-9_.+-]+)[\"']", out)
             if not m.startswith("-")}
    assert progs, "the scan found nothing - the pattern no longer matches the code"
    unknown = sorted(progs - FRESH_CARD_PROGRAMS - CARRIED_PROGRAMS
                     - set(wheelhouse.PROGRAM_PACKAGES))
    assert not unknown, f"no clone would have: {unknown}"


def test_about_names_a_clones_build_from_its_carried_stamp(tmp_path):
    from provisioning import about
    path = tmp_path / about.RELEASE_STAMP_NAME
    path.write_text('{"hash": "abc1234", "branch": "main", '
                    '"remote": "https://github.com/x/y"}', encoding="utf-8")
    no_git = lambda cmd: (1, "")
    stamp = about.release_stamp(str(path))
    assert about.git_hash(no_git, stamp=stamp) == "abc1234"
    assert about.software_version(no_git, stamp=stamp) == "abc1234 (main)"
    assert about.repo_link(no_git, stamp=stamp) == "https://github.com/x/y"
    assert about.software_version(no_git, stamp={}) == "unknown"
    assert about.release_stamp(str(tmp_path / "absent.json")) == {}


def test_git_still_wins_over_a_stamp():
    from provisioning import about
    git = lambda cmd: (0, "def5678\n") if "short" in cmd else (0, "main\n")
    assert about.git_hash(git, stamp={"hash": "abc1234"}) == "def5678"


def test_the_clone_hands_on_its_version():
    from tests.srcutil import func_source
    assert "_stamp_release(wf)" in func_source("workflows/clone.py", "transfer_tool")


def test_every_firmware_source_tree_travels_without_its_history():
    """Unpublished commits keep the address they were made under; two on Node
    Medic 1 held a personal e-mail. Source trees go without .git, toolchains
    keep theirs (a git-installed PlatformIO platform may need it)."""
    for tree in ("overlay_test", "RNode_Firmware", "MeshPocket", "EoRa-S3",
                 "RTNode-2400", "rnm-assets", "Arduino"):
        assert tree not in clone.HOME_NEVER and ".git" in clone._home_entry_skips(tree)
    assert ".git" not in clone._home_entry_skips(".platformio")


def test_the_carry_step_drops_git_history_from_firmware_trees(monkeypatch):
    from tests.test_clone import wf
    w = wf()
    sent = {}
    w.connection.push_tree = lambda local, remote, exclude=(): (
        sent.__setitem__(remote, tuple(exclude)) or True)
    w.connection.push_file = lambda local, remote: True
    monkeypatch.setattr("os.listdir", lambda p: [".platformio", "EoRa-S3", "rnm-assets",
                                                  "pi_os_lite.img.xz"])
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr("os.path.islink", lambda p: False)
    monkeypatch.setattr("os.path.isdir", lambda p: not p.endswith(".xz"))
    monkeypatch.setattr("os.path.isfile", lambda p: p.endswith(".xz"))
    idx = next(i for i, (n, _) in enumerate(w.steps) if n == "carry_the_toolchain")
    assert w.steps[idx][1](w).success
    assert ".git" in sent["~/EoRa-S3"] and ".git" in sent["~/rnm-assets"]
    assert ".git" not in sent["~/.platformio"]


def _records_the_code_writes():
    """Every record name the code reaches under ~/.reticulum-node-medic: a
    path written out in full, or a *_FILE constant in a module whose *_DIR
    constant is that folder (the boundary-walk modules)."""
    out = subprocess.run(["git", "ls-files", "*.py"], capture_output=True,
                         text=True, cwd=ROOT).stdout.split()
    names = set()
    for rel in out:
        if rel.startswith("tests/"):
            continue
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
            src = f.read()
        names |= set(re.findall(r"~/\.reticulum-node-medic/([A-Za-z0-9_.-]+)", src))
        # a path built in pieces: join(home, ".reticulum-node-medic", "name")
        names |= set(re.findall(r"\.reticulum-node-medic[\"']\s*,\s*[\"']([A-Za-z0-9_.-]+)[\"']",
                                src))
        if re.search(r"^\s*_?[A-Z_]*DIR\s*=\s*[\"']~/\.reticulum-node-medic[\"']", src, re.M):
            names |= set(re.findall(r"^\s*_?[A-Z][A-Z_]*_FILE\s*=\s*[\"']([A-Za-z0-9_.-]+)[\"']",
                                    src, re.M))
    return {n for n in names if n not in ("", ".", "..")}


def test_the_never_lists_name_only_real_records_each_with_a_reason():
    """Records travel with the fleet by default now (keeper, 2026-10-09); the
    never list is the only list, so it must stay honest: every entry names a
    record the code really writes, and says why it stays."""
    names = _records_the_code_writes()
    assert {"walk_observations.jsonl", "walk_anchors.json", "registry.json"} <= names, names
    stale = sorted(set(clone.RECORDS_NEVER) - names)
    assert not stale, f"never-list entries the code no longer writes: {stale}"
    assert all(why.strip() for why in clone.RECORDS_NEVER.values())
    assert all(why.strip() for why in clone.HOME_NEVER.values())


def test_what_the_fleet_has_learned_travels_and_identity_never_does():
    learned = {"walk_anchors.json", "walk_observations.jsonl", "walk_failures.jsonl",
               "walk_diagnoses.jsonl", "certificates", "firmware_backups",
               "known_hosts", "forgotten.json", "node_watch.json", "relay_census.json"}
    assert not learned & (set(clone.RECORDS_NEVER) | set(clone.RECORDS_OWN_STEP))
    for own in (".ssh", ".reticulum", ".gitconfig", "gps_state.json", ".lxmd"):
        assert own in clone.HOME_NEVER, own


def _records_run(monkeypatch, fresh):
    from tests.test_clone import wf
    w = wf(); w.fresh_fleet = fresh
    trees, files = {}, []
    w.connection.push_tree = lambda local, remote, exclude=(): (
        trees.__setitem__(remote, tuple(exclude)) or True)
    w.connection.push_file = lambda local, remote: files.append(remote) or True
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("os.path.isfile", lambda p: True)
    note = clone._carry_settings(w)
    return note, trees, files


def test_a_fleet_clone_takes_every_record_but_this_medics_own(monkeypatch):
    note, trees, files = _records_run(monkeypatch, fresh=False)
    skips = trees[clone.CLONE_DIR]
    assert "/trust.json" in skips and "/chat" in skips and "/registry.json" in skips
    assert not any("walk_" in s for s in skips) and "*.bak*" not in skips
    assert "*.log" in skips and not files and "records" in note


def test_a_new_communitys_clone_takes_no_fleet_record(monkeypatch):
    note, trees, files = _records_run(monkeypatch, fresh=True)
    assert not trees
    assert sorted(f.rsplit("/", 1)[1] for f in files) == sorted(clone.SETTINGS_ALWAYS)


def test_a_new_communitys_clone_gets_no_loose_personal_file(monkeypatch):
    from tests.test_clone import wf
    w = wf(); w.fresh_fleet = True
    sent = []
    w.connection.push_tree = lambda local, remote, exclude=(): sent.append(remote) or True
    w.connection.push_file = lambda local, remote: sent.append(remote) or True
    monkeypatch.setattr("os.listdir", lambda p: ["pi_os_lite.img.xz", "bench-notes.md",
                                                  "ui.log", "overlay_test", ".gitconfig"])
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr("os.path.islink", lambda p: False)
    monkeypatch.setattr("os.path.isdir", lambda p: p.endswith("overlay_test"))
    monkeypatch.setattr("os.path.isfile", lambda p: not p.endswith("overlay_test"))
    idx = next(i for i, (n, _) in enumerate(w.steps) if n == "carry_the_toolchain")
    assert w.steps[idx][1](w).success
    assert sorted(sent) == ["~/overlay_test", "~/pi_os_lite.img.xz"]


def test_clutter_never_travels_and_the_arduino_downloads_stay_behind():
    for kind in ("*.log", "*.bak", "*.bak-*", "__pycache__", ".cache"):
        assert kind in clone.CLUTTER, kind
    assert "/staging" in clone._home_entry_skips(".arduino15")


# ---- packages: everything installed after imaging travels -----------------

def test_dpkg_log_installs_reads_plain_and_rotated_logs(tmp_path):
    import gzip
    from workflows import wheelhouse as wh
    (tmp_path / "dpkg.log").write_text(
        "2026-10-01 10:00:00 install sshpass:arm64 <none> 1.10-1\n"
        "2026-10-01 10:00:01 upgrade gpsd:arm64 3.25 3.26\n"
        "2026-10-01 10:00:02 status installed sshpass:arm64 1.10-1\n")
    with gzip.open(tmp_path / "dpkg.log.2.gz", "wt") as fh:
        fh.write("2026-07-14 09:00:00 install rpiboot:arm64 <none> 20250908\n")
    assert wh.dpkg_log_installs(str(tmp_path / "dpkg.log*")) == {"sshpass", "rpiboot"}


def test_added_packages_keeps_a_ledger_and_leaves_out_what_every_card_has(tmp_path):
    from workflows import wheelhouse as wh
    log = tmp_path / "dpkg.log"
    log.write_text("x y install libusb-1.0-0-dev:arm64 <none> 1\n"
                   "x y install unicode-data:all <none> 1\n"
                   "x y install wayvnc:arm64 <none> 1\n"
                   "x y install bash:arm64 <none> 1\n"
                   "x y install sshpass:arm64 <none> 1\n")
    ledger = tmp_path / "packages_added.txt"
    ledger.write_text("# old\nisympy3\ngone-package\n")
    got = wh.added_packages(logs=str(log), ledger=str(ledger), base={"bash"},
                            installed={"libusb-1.0-0-dev", "unicode-data", "wayvnc",
                                       "bash", "sshpass", "isympy3"})
    # bash: on every card; sshpass: a baseline package; wayvnc: never; gone: removed
    assert got == ("isympy3", "libusb-1.0-0-dev", "unicode-data")
    kept = ledger.read_text()
    assert "isympy3" in kept and "gone-package" not in kept and "libusb-1.0-0-dev" in kept


def test_the_ledger_reaches_every_clone():
    assert "packages_added.txt" in clone.SETTINGS_ALWAYS


def _install(monkeypatch, store, c=None):
    from tests.test_clone import wf, _run
    monkeypatch.setattr(clone, "_carried_debs", lambda: store)
    return _run(wf(c), "install_carried_packages")


@pytest.mark.real_package_store
def test_an_empty_store_stops_the_clone_instead_of_skipping(monkeypatch):
    r = _install(monkeypatch, ([], (), [], []))
    assert r.success is False and "Field readiness" in r.message


@pytest.mark.real_package_store
def test_a_package_the_parent_cannot_hand_on_is_named(monkeypatch):
    r = _install(monkeypatch, (["/c/a.deb"], ("libusb-1.0-0-dev",), [], ["libusb-1.0-0-dev"]))
    assert r.success is False and "libusb-1.0-0-dev" in r.message and "internet" in r.message


@pytest.mark.real_package_store
def test_a_package_that_does_not_install_stops_the_clone(monkeypatch):
    from tests.test_clone import conn
    c = conn()
    c.rules.insert(0, ("dpkg-query -W -f='${Status}' direwolf", 1, "", "not installed"))
    r = _install(monkeypatch, (["/c/a.deb"], (), [], []), c)
    assert r.success is False and "direwolf" in r.message


@pytest.mark.real_package_store
def test_everything_installed_means_the_step_passes(monkeypatch):
    r = _install(monkeypatch, (["/c/a.deb"], ("unicode-data",), ["/c/u.deb"], []))
    assert r.success is True and "1 it added" in r.message


def test_ensure_added_plan_fetches_only_when_the_store_falls_short(tmp_path, monkeypatch):
    from workflows import wheelhouse as wh
    calls = []

    class C:
        def run(self, cmd, timeout=30):
            calls.append(cmd)
            return (0, "", "")
    monkeypatch.setattr(wh, "carried_package_names", lambda d=None: {"unicode-data"})
    monkeypatch.setattr(wh, "planned_debs", lambda name, d=None: [])
    _files, short = wh.ensure_added_plan(("unicode-data",), str(tmp_path), connection=C())
    assert not calls and short == []
    _files, short = wh.ensure_added_plan(("unicode-data", "isympy3"), str(tmp_path),
                                         connection=C())
    assert calls and ".plan-added" in calls[0] and short == ["isympy3"]


def test_the_store_refresh_also_plans_what_was_added(monkeypatch):
    from workflows import wheelhouse as wh
    ran = []

    class C:
        def run(self, cmd, timeout=30):
            ran.append(cmd)
            return (0, "3", "") if "wc -l" in cmd else (0, "", "")
    monkeypatch.setattr(wh, "added_packages", lambda *a, **k: ("unicode-data",))
    monkeypatch.setattr(wh, "deb_count", lambda c, d=None: 5)
    ok, msg = wh.cache_debs(C())
    assert ok and any(".plan-added" in c for c in ran) and "added since imaging" in msg
