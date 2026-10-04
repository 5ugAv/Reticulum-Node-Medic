"""Readiness ledger, 2026-10-05 early, batch six — eighteen small honesty and
hygiene items (Self Diagnose, Communication apps, the language picker, the
birth guide, the node page, the mode toggle, CI and the test suite itself)."""
import json
import os

from tests.srcutil import ROOT, src

SHIPPED = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def _cat(code):
    with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
        return json.load(f)


# -- Self Diagnose ---------------------------------------------------------

def test_the_usb_finding_says_how_boards_are_really_commissioned():
    from monitor.self_diagnose import check_usb_present
    f = check_usb_present(None, "")
    assert "setup_boot.sh" in f.detail and "from Settings" not in f.detail


def test_an_offline_medic_with_a_fresh_gps_sync_has_a_healthy_clock():
    from monitor.self_diagnose import check_clock_sync, SEV_OK, SEV_WARN
    now = 1_800_000_000.0
    assert check_clock_sync("NTP=yes\nNTPSynchronized=yes\n", now).severity == SEV_OK
    gps = check_clock_sync("NTP=yes\nNTPSynchronized=no\n", now,
                           last_sync=now - 600, last_sync_source="GPS")
    assert gps.severity == SEV_OK and "GPS" in gps.detail and "offline" in gps.detail
    off = check_clock_sync("NTP=no\nNTPSynchronized=no\n", now)
    assert off.severity == SEV_WARN and "NTP off" in off.detail
    none = check_clock_sync("NTP=yes\nNTPSynchronized=no\n", now)
    assert none.severity == SEV_WARN and "No time source" in none.detail
    stale = check_clock_sync("NTP=yes\nNTPSynchronized=no\n", now,
                             last_sync=now - 3 * 86400, last_sync_source="GPS")
    assert stale.severity == SEV_WARN
    rt = src("monitor/self_diagnose_runtime.py")
    assert "last_sync=last_sync, last_sync_source=sync_src" in rt


def test_a_refused_repair_is_not_reported_fixed():
    from monitor import self_diagnose_runtime as rt
    ok, msg = rt.run_repair("restart_rnsd", run_rc=lambda c: (1, "sudo: a password is required"))
    assert ok is False and "password" in msg
    ok, _ = rt.run_repair("restart_rnsd", run_rc=lambda c: (0, ""))
    assert ok is True
    ok, _ = rt.run_repair("restart_rnsd", run_rc=lambda c: (1, ""))      # exit code alone
    assert ok is False
    ok, _ = rt.run_repair("restart_rnsd", run=lambda c: "sudo: a password is required")
    assert ok is False                                                      # legacy string runner


def test_the_summary_and_a_failed_fix_tell_the_truth():
    s = src("ui/screens/self_diagnose_screen.py")
    assert 'rt.repair_kind(f.fix) == "auto"' in s
    assert 'tr("  ·  see the notes below")' in s
    assert 'tr("Failed — try again")' in s and "button.disabled = False" in s
    done = s[s.index("def _fix_done"):]
    assert 'color="red"' in done


# -- Communication apps + languages -----------------------------------------

def test_communication_apps_speak_plainly_and_are_translated():
    c = src("ui/screens/comms_screen.py")
    assert "lxmd is started with -p" not in c and 'tr(app["blurb"])' in c
    from workflows.phone_apps import APPS
    for app in APPS.values():
        assert "LXMF client" not in app["blurb"]
    for code in SHIPPED:
        cat = _cat(code)
        for app in APPS.values():
            assert app["blurb"] in cat, (code, app["name"])
            if app.get("install_note"):
                assert app["install_note"] in cat, code


def test_partly_translated_languages_say_so():
    from ui import i18n
    assert i18n.coverage("en") == 1.0
    assert i18n.coverage("es") > 0.95
    assert 0.0 < i18n.coverage("pt") < 0.5
    ls = src("ui/screens/language_screen.py")
    assert "coverage(code)" in ls and "the rest shows in English" in ls
    assert "has a full translation" not in ls


# -- birth guide, flash, chat, node page, toggle ---------------------------

def test_the_three_birth_strings_are_wrapped():
    b = src("ui/screens/birth_guide_screen.py")
    assert 'step.set_status(tr(\n                    "Still not seeing anything.' in b
    assert 'tr("Wiping {name} — keep it plugged in")' in b
    assert 'return b.display_name if b else tr("this radio")' in b


def test_a_board_that_left_usb_is_named_not_blamed_on_the_medics_radio():
    f = src("workflows/rnode_flash.py")
    body = f[f.index("erased, emsg = self._erase_chip()") - 600:f.index("erased, emsg = self._erase_chip()")]
    assert "The board left USB during the flash" in body


def test_chat_gives_up_waiting_for_rnsd_in_words():
    a = src("ui/app.py")
    assert "the mesh service (rnsd) did not come up" in a
    assert "self._chat_waits = 0" in a


def test_the_node_page_shares_its_rows_source_and_names_adoption():
    n = src("ui/screens/node_detail_screen.py")
    assert "dash = record.to_dashboard(now)" in n
    assert 'HexStatus(status=dash["status"]' in n
    assert 'advise(self._dash["status"]' in n
    assert 'tr("Adopted by this medic")' in n and 'tr("Adopted: {born}")' in n
    assert 'self._cert_adopted = bool(cert.get("adopted"))' in n


def test_a_failed_mode_switch_never_leaves_the_toggle_dead():
    a = src("ui/app.py")
    sw = a[a.index("def _set_node_mode"):a.index("def _check_movement")]
    assert "except Exception as e:" in sw and "tog.set_busy(False)" in sw
    rf = a[a.index("def _refresh_node_mode"):a.index("def _set_node_mode")]
    assert "mode_toggle.set_busy(False)" in rf


# -- CI and the suite itself ----------------------------------------------

def test_ci_installs_rns_and_refuses_a_silent_skip():
    ci = src(".github/workflows/ci.yml")
    assert "requirements-test.txt" in ci and "pytest -q -rs" in ci
    assert "could not import 'RNS'" in ci and "set -o pipefail" in ci
    req = src("requirements-test.txt")
    for dep in ("pytest", "PyYAML", "Pillow", "numpy", "cryptography", "rns"):
        assert dep in req
    assert "requirements-test.txt" in src("README.md")


def test_optional_test_deps_skip_instead_of_failing():
    for path in ("tests/test_audit_20260909_wording.py", "tests/test_front_page_vocabulary.py",
                 "tests/test_home_zones.py", "tests/test_pi_images.py", "tests/test_terrain.py"):
        s = src(path)
        assert "    from PIL import Image" not in s and "    import numpy as np" not in s, path
        assert 'pytest.importorskip("PIL.Image")' in s or 'pytest.importorskip("numpy")' in s, path


def test_about_measures_the_test_count():
    from provisioning import about
    assert "-o addopts=" in about._COLLECT_CMD
    assert about.parse_test_count("tests/test_x.py::test_a\n6735 tests collected in 10.5s\n") == 6735
    assert about.test_status(run=lambda c: (0, "")) == "not measured on this device"
