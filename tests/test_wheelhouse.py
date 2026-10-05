"""Offline wheelhouse — caching the tool's Python deps for a field clone.

The medic downloads wheels for its own (== the clone target's) platform, then a
--no-index install must resolve the whole stack. These tests pin the commands and
the success/failure reporting without touching the network.
"""
import os

import pytest

from transport.connection import EmulatedConnection
from workflows.wheelhouse import (
    cache_wheels, download_command, verify_command, wheel_count,
    REQUIREMENTS, WHEELHOUSE,
)


def test_download_command_pulls_the_manifest_into_the_wheelhouse():
    cmd = download_command()
    assert cmd == f"pip3 download -r {REQUIREMENTS} -d {WHEELHOUSE}"


def test_verify_command_does_a_no_index_install_in_a_throwaway_venv():
    cmd = verify_command()
    assert "python3 -m venv" in cmd
    assert "--no-index" in cmd and f"--find-links {WHEELHOUSE}" in cmd
    assert f"-r {REQUIREMENTS}" in cmd


def test_wheel_count_parses_ls_wc():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("wc -l", 0, "17")
    assert wheel_count(c) == 17


def _conn(wheels="17", download=0, verify=0):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("pip3 download", download, "Saved ...")
    c.rule("wc -l", 0, wheels)
    c.rule("python3 -m venv", verify, "Successfully installed" if verify == 0
           else "ERROR")
    return c


def test_cache_wheels_downloads_then_verifies_offline_install():
    c = _conn()
    ok, msg = cache_wheels(c)
    assert ok
    assert "17 wheels" in msg and "offline install" in msg
    assert any("pip3 download" in h for h in c.history)
    assert any("--no-index" in h for h in c.history)   # the verify step ran


def test_cache_wheels_can_skip_verification():
    c = _conn()
    ok, msg = cache_wheels(c, verify=False)
    assert ok and "17 wheels" in msg
    assert not any("--no-index" in h for h in c.history)


def test_cache_wheels_fails_when_download_fails():
    c = _conn(download=1)
    ok, msg = cache_wheels(c)
    assert ok is False and "download failed" in msg


def test_cache_wheels_fails_when_no_wheels_land():
    c = _conn(wheels="0")
    ok, msg = cache_wheels(c)
    assert ok is False and "no wheels" in msg


def test_cache_wheels_fails_when_offline_install_check_fails():
    c = _conn(verify=1)
    ok, msg = cache_wheels(c)
    assert ok is False and "offline install check failed" in msg


# ---------------------------------------------------------------------------
# APT packages carried for offline install (2026-09-03)
#
# Dire Wolf is the software modem that lets a salvaged handheld radio carry
# Reticulum traffic. It is in Debian and it was NOT carried — the same trap
# that killed the LUKS vault, where cryptsetup was in apt and nowhere on the
# medic, so a clone with no internet could never enable the feature.
# ---------------------------------------------------------------------------

def test_direwolf_is_carried():
    from workflows.wheelhouse import APT_PACKAGES
    assert "direwolf" in APT_PACKAGES


def test_the_apt_download_needs_no_root():
    """This medic's sudo is deliberately scoped (2026-08-02). `apt-get -d
    install` locks apt's lists and needs root, so it would mean widening root
    access to perform a download. --print-uris needs no privileges."""
    from workflows.wheelhouse import apt_download_command
    cmd = apt_download_command()
    assert "--print-uris" in cmd
    assert "sudo" not in cmd and "apt-get -d" not in cmd


def test_the_download_saves_under_apts_filename_not_the_urls():
    """The URL is percent-encoded (%2b for +) and the filename field is not.
    Saving under the URL basename produces a file the checksum line can never
    match — a verify step that silently checks nothing."""
    from workflows.wheelhouse import apt_download_command
    cmd = apt_download_command()
    assert 'wget -q -O "$f" "$u"' in cmd, "not saving under apt's filename"


def test_the_digest_algorithm_is_read_not_assumed():
    """apt on this Debian prints MD5Sum, not SHA256. A parser keyed on SHA256
    matched no lines, downloaded nothing, and reported success."""
    from workflows.wheelhouse import _uri_lines, verify_debs_command
    assert 'split($4, a, ":")' in _uri_lines(("x",)), "algorithm hard-coded"
    v = verify_debs_command()
    assert "md5sum -c" in v and "sha256sum -c" in v


def test_every_cached_deb_is_verified_before_it_is_trusted():
    from workflows.wheelhouse import verify_debs_command
    assert "-c" in verify_debs_command()




def test_the_clone_installs_only_what_it_needs_and_never_downloads(tmp_path):
    """First real clone (2026-10-06): installing the whole cache dragged in extras
    whose dependencies were not carried, and apt went online (404)."""
    from workflows.wheelhouse import debs_for, offline_install_command, DISPLAY_PACKAGES
    for f in ("cage_1_arm64.deb", "libgl1_1_arm64.deb", "gpsd-clients_3_arm64.deb", "libc6_2_arm64.deb"):
        (tmp_path / f).write_text("x")
    picked = debs_for(("cage", "libgl1"), str(tmp_path), run=lambda c: (0, "cage\nlibgl1\nlibc6\n"))
    names = sorted(p.rsplit("/", 1)[1] for p in picked)
    assert names == ["cage_1_arm64.deb", "libc6_2_arm64.deb", "libgl1_1_arm64.deb"]
    assert "--no-download" in offline_install_command("/tmp/x")
    for pkg in ("cage", "libgl1", "xwayland", "libsdl2-2.0-0"):
        assert pkg in DISPLAY_PACKAGES


# --- planned against the clone's own card (Wi-Fi-off proof clone, 2026-10-06)

def test_the_plan_reads_the_fresh_cards_package_list_not_this_medics():
    from workflows import wheelhouse as w
    cmd = w.plan_uri_lines(w.DISPLAY_PACKAGES, "/x/status")
    assert "Dir::State::status=/x/status" in cmd
    assert "Dir::Cache::archives=/tmp/nm-noarch/" in cmd      # never skip cached debs
    assert "--reinstall" not in cmd and "--no-install-recommends" in cmd
    assert os.path.isfile(w.CLONE_BASE_STATUS)
    body = open(w.CLONE_BASE_STATUS).read()
    assert body.count("Package: ") > 500 and "@" not in body     # no maintainer emails


def test_each_set_gets_its_own_checked_list():
    from workflows import wheelhouse as w
    cmd = w.planned_download_command("display", w.DISPLAY_PACKAGES, "/d", "/s")
    assert "> display.list" in cmd and "md5sum -c" in cmd and "exit 3" in cmd
    assert set(w.PLAN_LISTS) == {"display", "radio"}


def test_the_clone_installs_exactly_the_planned_files(tmp_path):
    from workflows import wheelhouse as w
    for n in ("a_1_arm64.deb", "b_2_arm64.deb", "stale_9_arm64.deb"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "display.list").write_text("a_1_arm64.deb\nb_2_arm64.deb\n")
    got = w.debs_for(w.DISPLAY_PACKAGES, str(tmp_path))
    assert [os.path.basename(f) for f in got] == ["a_1_arm64.deb", "b_2_arm64.deb"]
    (tmp_path / "radio.list").write_text("a_1_arm64.deb\nmissing_1_arm64.deb\n")
    assert w.debs_for(w.APT_PACKAGES, str(tmp_path)) == []        # short cache: say so


def test_the_offline_install_ignores_the_cards_stale_sources():
    from workflows import wheelhouse as w
    cmd = w.offline_install_command("/tmp/nm-debs")
    assert "Dir::Etc::SourceList=/dev/null" in cmd and "--no-download" in cmd
