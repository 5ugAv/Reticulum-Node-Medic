"""The readiness ledger's "Blocks the clone test" group, 2026-10-04 evening.

#118 Home during the card write wedged the flow; #119 the write erased
whichever single disk was present, unnamed (the vault key stick included);
#203 three explainers clipped to one line; #125 raw step names in the
ladder; #176 the map download thread died silently; #194 a test overwrote
the real last-imaged-Pi record; #97 the touch fallback was silent; and the
partial blockers #115 (no deb preflight / top-up) and #116 (a clone's own
card helper and SSH key unverified).
"""
import json
import os

from monitor import self_diagnose as sd
from node_profile import NodeProfile
from provisioning import pi_discover
from tests.srcutil import func_source, src
from transport.connection import EmulatedConnection
from workflows import mitosis_card
from workflows.clone import _CLONE_STEPS, CloneWorkflow, final_verification

MIT = "ui/screens/mitosis_screen.py"
LANGS = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


# -- #118: Home and re-entry during a write -------------------------------------

def test_home_and_re_entry_refuse_during_a_write_and_the_flag_is_released():
    begin = func_source(MIT, "begin", cls="MitosisScreen")
    assert 'getattr(self, "_writing", False)' in begin
    home = func_source(MIT, "handle_home", cls="MitosisScreen")
    assert "requirement_popup(" in home and 'switch_mode("home")' in home
    assert "still being written" in home and "still running" in home
    s = src(MIT)
    assert "self._write_gen = gen" in s
    done = s[s.index("def done(_dt, g=gen):"):s.index("Clock.schedule_once(done, 0)")]
    # the latest write lets go of the flag BEFORE the generation check
    assert done.index("self._writing = False") < done.index("if g != self._stage_gen:")
    assert 'g == getattr(self, "_write_gen", g)' in done


# -- #119: name the card, refuse the vault key, say when debs are missing -------

def test_the_write_is_confirmed_by_name_and_the_vault_key_is_refused():
    s = src(MIT)
    assert "self._confirm_write(self._chosen_password)" in s
    confirm = func_source(MIT, "_confirm_write", cls="MitosisScreen")
    assert "pi_imager.card_status()" in confirm
    assert "mitosis_card.holds_vault_key(" in confirm
    assert "mitosis_card.debs_missing()" in confirm
    assert 'tr("Erase & write")' in confirm and 'tr("Cancel")' in confirm
    assert "self._show_stage_write(password)" in confirm
    # the imager call carries the deb check too
    assert "deb_check=debs_missing" in s


def test_holds_vault_key_matches_the_stick_by_device_not_partition():
    mounts = lambda: ["/media/nodemedic/KEY", "/media/nodemedic/PHOTOS"]
    keys = {"/media/nodemedic/KEY": True, "/media/nodemedic/PHOTOS": False}
    sources = {"/media/nodemedic/KEY": "/dev/sdb1\n", "/media/nodemedic/PHOTOS": "/dev/sdc1\n"}
    src = lambda mnt: sources[mnt]
    assert mitosis_card.holds_vault_key("/dev/sdb", mounts, keys.get, src) is True
    assert mitosis_card.holds_vault_key("/dev/sdb2", mounts, keys.get, src) is True
    assert mitosis_card.holds_vault_key("/dev/sdc", mounts, keys.get, src) is False
    assert mitosis_card.holds_vault_key("", mounts, keys.get, src) is False
    # a mount that cannot be read is skipped, never a crash
    boom = lambda mnt: (_ for _ in ()).throw(OSError("no findmnt"))
    assert mitosis_card.holds_vault_key("/dev/sdb", mounts, keys.get, boom) is False
    # no shell anywhere in it (audit C5)
    assert "shell=True" not in src_text()


def src_text():
    return open("workflows/mitosis_card.py", encoding="utf-8").read()


def test_image_medic_card_refuses_when_the_deb_check_says_so():
    flashed = []
    ok, msg, pw = mitosis_card.image_medic_card(
        "/dev/sdb", "NodeMedic2", password="pw", flash=lambda *a, **k: flashed.append(1) or (True, "ok"),
        wifi=("", ""), helper_check=lambda: "", deb_check=lambda: "no debs carried")
    assert ok is False and msg == "no debs carried" and flashed == []
    ok, msg, pw = mitosis_card.image_medic_card(
        "/dev/sdb", "NodeMedic2", password="pw", flash=lambda *a, **k: flashed.append(1) or (True, "ok"),
        wifi=("", ""), helper_check=lambda: "", deb_check=lambda: "")
    assert ok is True and flashed == [1]


# -- #203 / #125: the ladder names every step -------------------------------------

def test_every_clone_step_has_a_title_and_an_estimate():
    s = src(MIT)
    ns = {}
    exec(s[s.index("STEP_TITLES = ["):s.index("#: Rough dd+config seconds")], ns)
    titles = dict(ns["STEP_TITLES"])
    est = ns["STEP_EST_S"]
    names = [name for name, _fn in _CLONE_STEPS] + ["find_new_medic"]
    missing_titles = [n for n in names if n not in titles]
    missing_est = [n for n in names if n not in est]
    assert missing_titles == [], missing_titles
    assert missing_est == [], missing_est
    assert "install_carried_packages" in titles and est["install_carried_packages"] >= 60


# -- #176: the map download never dies silently --------------------------------

def test_the_map_download_worker_always_reports_back():
    run = func_source("ui/screens/scan_screen.py", "_run_download", cls="ScanScreen")
    assert "self._download_all(lat, lon, dest)" in run
    assert "except Exception" in run and '"error": str(e)' in run
    assert "self._download_done(summary)" in run
    done = func_source("ui/screens/scan_screen.py", "_download_done", cls="ScanScreen")
    assert 'summary.get("error")' in done and "Download failed" in done


# -- #194: the real last-imaged-Pi record is never touched by a test ------------

def test_the_imaged_pi_record_default_path_is_resolved_at_call_time(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    monkeypatch.setattr(pi_discover, "STATE_PATH", str(target))
    assert pi_discover.record_imaged_pi("faithpi", "pi") in (True, False)
    if target.exists():
        assert json.load(open(target))["hostname"] == "faithpi"
    assert pi_discover.last_imaged_pi().get("hostname", "faithpi") == "faithpi"
    real = os.path.expanduser("~/.reticulum-node-medic/last_imaged_pi.json")
    assert str(target) != real


def test_conftest_points_every_test_away_from_the_real_record():
    c = src("tests/conftest.py")
    assert "def _hermetic_imaged_pi(" in c and "autouse=True" in c
    assert 'monkeypatch.setattr(pi_discover, "STATE_PATH"' in c


# -- #116: a clone's own card helper and SSH key are verified ---------------------

def test_final_verification_demands_the_card_helper_and_an_ssh_key():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rule("test -x", 1, "")
    conn.rule("test -f ~/.ssh/id_ed25519.pub", 1, "")
    wf = CloneWorkflow.__new__(CloneWorkflow)
    wf.connection = conn
    wf.fresh_identity_generated = True
    res = final_verification(wf)
    assert res.success is False
    assert "card-writing helper missing" in res.message and "SSH keypair missing" in res.message


# -- #97: the touch fallback is said -----------------------------------------------

def test_touch_verdict_is_recorded_and_diagnosed():
    m = src("main.py")
    assert "/dev/shm/nodemedic-touch" in m and "_touch = touch_input.choose(" in m
    assert sd.check_touch("mtdev /dev/input/event5").ok
    warn = sd.check_touch("mouse (no panel found)")
    assert not warn.ok and "single-finger" in warn.detail and "no panel found" in warn.detail
    assert not sd.check_touch("").ok
    rt = src("monitor/self_diagnose_runtime.py")
    assert "sd.check_touch(run(\"cat /dev/shm/nodemedic-touch 2>/dev/null\"))" in rt


# -- the words are in every language --------------------------------------------------

def test_the_new_clone_words_are_translated():
    keys = ("Not yet", "Which card?", "Not that one", "Can't write yet",
            "Erase [b]{label}[/b] at [b]{path}[/b] and write the new medic's card to it?",
            "Download failed — {why}. Nothing was changed; check the connection and the card's free space, then try again.")
    for code in LANGS:
        d = json.load(open(os.path.join("assets", "i18n", code + ".json"), encoding="utf-8"))
        for k in keys:
            assert d.get(k) and d[k] != k, (code, k)
            for ph in ("{label}", "{path}", "{why}"):
                assert (ph in k) == (ph in d[k]), (code, k, ph)
