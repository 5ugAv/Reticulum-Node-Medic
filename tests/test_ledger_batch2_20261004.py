"""Readiness ledger, 2026-10-04 late batch — what a first-time user meets:

  #34   a failed Home/Backpack switch still lit and persisted the new mode
  #207  the card-erase warning and password refusals could never be translated
  #204  the medic's self-test had three names
  #175  'keeping watch — you don't need to press anything' over a keypad
  #29   an abandoned antenna comparison seeded the next node's SUSPECT baseline
  #36   a failed power-off left the slider parked on OFF, reason unreadable
  #122  a non-Latin Clone name failed only after password + Wi-Fi, unfixably
  #121  the Clone screen promised a login the new medic never asks for
"""
import glob
import json
import os

from tests.srcutil import ROOT, src

SHIPPED = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def _catalog(code):
    with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
        return json.load(f)


# -- #34 -------------------------------------------------------------------

def test_a_failed_switch_does_not_persist_the_requested_mode():
    from transport.connection import EmulatedConnection
    from workflows.node_mode import set_mode, MODE_FILE
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("systemctl restart rnsd", 1, "", "failed"))
    res = set_mode("home", c)
    assert res.ok is False
    assert not any(f"> {MODE_FILE}" in cmd for cmd in c.history), (
        "the marker was written for a switch that failed")
    # the normal path still persists it — AFTER the restart proved the switch
    c2 = EmulatedConnection(default_code=0, default_stdout="ok")
    assert set_mode("home", c2).ok is True
    i_restart = next(i for i, cmd in enumerate(c2.history) if "restart rnsd" in cmd)
    i_marker = next(i for i, cmd in enumerate(c2.history) if f"> {MODE_FILE}" in cmd)
    assert i_marker > i_restart


# -- #207 ------------------------------------------------------------------

def test_password_refusals_are_templates_the_screen_can_translate():
    from provisioning.pi_imager import (
        password_problem, validate_new_password, PASSWORD_EMPTY,
        PASSWORD_MISMATCH, PASSWORD_SHORT, MIN_PASSWORD_LEN)
    assert password_problem("") == PASSWORD_EMPTY
    assert password_problem("abcdefgh", "abcdefgi") == PASSWORD_MISMATCH
    assert password_problem("ab") == PASSWORD_SHORT and "{n}" in PASSWORD_SHORT
    assert password_problem("a" * MIN_PASSWORD_LEN) is None
    ok, msg = validate_new_password("ab")
    assert not ok and msg == PASSWORD_SHORT.format(n=MIN_PASSWORD_LEN)
    scr = src("ui/screens/pi_imager_screen.py")
    assert "tr(pw_problem).format(n=MIN_PASSWORD_LEN)" in scr
    assert "[color={red}]This ERASES everything on that card.[/color]" in scr
    assert 'theme.COLORS["red"].lstrip("#") + "]This ERASES' not in scr


def test_the_erase_warning_and_refusals_are_in_every_shipped_catalog():
    for code in SHIPPED:
        c = _catalog(code)
        assert any("[color={red}]This ERASES everything on that card." in k for k in c), code
        assert not any("[color=ff5555]" in k for k in c), code
        for k in ("Enter a login password.", "Use at least {n} characters.",
                  "The two passwords don't match — retype them.", "Self Diagnose"):
            assert k in c, (code, k)
        assert "Self-check" not in c, code


# -- #204 ------------------------------------------------------------------

def test_the_medics_self_test_has_one_name():
    for path in glob.glob(os.path.join(ROOT, "ui", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert 'tr("Self-check")' not in text and "run Self-check" not in text, path
    assert 'tr("Self Diagnose")' in src("ui/screens/vitals_screen.py")
    assert src("ui/screens/scan_screen.py").count("run Self Diagnose ") == 2


# -- #175 ------------------------------------------------------------------

def test_the_setup_wizard_shows_no_keeping_watch_pulse():
    ws = src("ui/widgets/wizard_step.py")
    hide = ws[ws.index("def hide_next"):ws.index("def _start_heartbeat")]
    assert "heartbeat: bool = True" in hide and "if heartbeat:" in hide
    assert "w.hide_next(heartbeat=False)" in src("ui/screens/setup_wizard_screen.py")
    # the guided birth's hardware waits keep their pulse
    assert "step.hide_next()" in src("ui/screens/birth_guide_screen.py")


# -- #29 -------------------------------------------------------------------

def test_an_abandoned_antenna_comparison_does_not_seed_the_next():
    s = src("ui/screens/antenna_test_screen.py")
    body = s[s.index("def begin_screen"):s.index("def sleep")]
    assert 'self._stage == "done" or self.session.count > 0' in body
    assert "self.session = at.AntennaSession()" in body
    assert "The unfinished comparison from last time was " in body   # the notice


# -- #36 -------------------------------------------------------------------

def test_a_failed_power_off_puts_the_slider_back_to_on():
    sl = src("ui/widgets/slide_to_power.py")
    reset = sl[sl.index("def reset("):sl.index("def on_touch_up")]
    assert "self.knob.x = self._left()" in reset and 'COLORS["red"]' in reset
    home = src("ui/screens/home_screen.py")
    assert "self.power_slider.reset(" in home
    assert "self.power_slider.hint.text = (" not in home
    st = src("ui/screens/settings_screen.py")
    assert "self._power_slider = SlideToPowerOff(" in st
    assert "self._power_slider.reset()" in st


# -- #122 / #121 -----------------------------------------------------------

def test_the_clone_name_is_checked_where_it_is_typed():
    m = src("ui/screens/mitosis_screen.py")
    cont = m[m.index("def _name_continue"):m.index("# -- stage 3: PASSWORD")]
    assert "if not self._hostname_for(self._name):" in cont and "return" in cont
    assert "def _refresh_name_note" in m
    assert "On the network it will be called {host}" in m
    assert "Use letters a-z or digits - this becomes its network name." in m
    failed = m[m.index("def _show_stage_write_failed"):m.index("def _mark_activity")]
    assert 'self._show_stage_name if "hostname" in (msg or "")' in failed
    from provisioning.pi_imager import hostnameify
    assert hostnameify("Медик") == "" and hostnameify("Faith Pi") == "faith-pi"


def test_the_clone_screen_no_longer_promises_a_login():
    m = src("ui/screens/mitosis_screen.py")
    assert "log in as 'pi'" not in m and "type this to log in" not in m
    assert "no login, nothing to type from here" in m
    assert "never asks for this on its own screen" in m
    for code in SHIPPED:
        c = _catalog(code)
        assert not any("log in as 'pi'" in k for k in c), code
        assert any(k.startswith("[b]Done - you have made a Node Medic.[/b]") for k in c), code
