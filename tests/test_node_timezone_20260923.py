"""A node born by the medic keeps the medic's timezone.

2026-09-23: ELSEWHERE and SKYFINGER both printed 00:10 BST while the medic
read 09:10 AEST — the same instant. Their clocks were RIGHT (NTP synced);
their ZONE was the stock image's Europe/London, because neither the card
writer nor the build ever said which zone the node lives in. Fixed in place
by hand on both, and here at birth on both roads: the card's custom.toml
carries the medic's zone for first boot, and the build sets it over SSH and
reads it back (a card imaged elsewhere still ends up right). The
certificate records it. When the medic's own zone cannot be read, the
step says so and skips — it never invents one.
"""
import os

from node_profile import NodeProfile
from tests.srcutil import ROOT, func_source
from tests.test_build_workflow import build_conn, wf, _run_step
from workflows import build


def _w(tz):
    conn = build_conn(rnode=False)
    conn.rules.insert(0, ("^hostname", 0, "node1", ""))
    conn.rules.insert(0, ("^hostname -I", 0, "192.168.1.9", ""))
    conn.rules.insert(0, ("RNS.Identity.from_file", 0, "", ""))
    p = NodeProfile()
    p.timezone = tz
    w = wf(conn, p)
    w.steps[0][1](w)
    return w


def test_the_step_sets_the_zone_and_reads_it_back():
    w = _w("Australia/Sydney")
    w.connection.rules.insert(0, ("timedatectl show -p Timezone --value", 0,
                                  "Australia/Sydney\n", ""))
    r = _run_step(w, "set_node_timezone")
    assert r.success and not getattr(r, "skipped", False)
    assert "Australia/Sydney" in r.message
    assert any("timedatectl set-timezone Australia/Sydney" in c
               for c in w.connection.history)


def test_a_read_back_that_disagrees_is_a_failure_not_a_claim():
    w = _w("Australia/Sydney")
    w.connection.rules.insert(0, ("timedatectl show -p Timezone --value", 0,
                                  "Europe/London\n", ""))
    r = _run_step(w, "set_node_timezone")
    assert not r.success
    assert "Europe/London" in r.message and "Australia/Sydney" in r.message


def test_no_medic_zone_means_skip_and_say_so():
    w = _w("")
    r = _run_step(w, "set_node_timezone")
    assert r.success and getattr(r, "skipped", False)
    assert "image default" in r.message
    assert not any("set-timezone" in c for c in w.connection.history)


def test_a_zone_that_is_not_a_zone_name_never_reaches_the_shell():
    w = _w("Australia/Sydney; rm -rf /")
    r = _run_step(w, "set_node_timezone")
    assert not r.success
    assert not any("set-timezone" in c for c in w.connection.history)


def test_the_step_runs_after_the_hostname_and_before_verification():
    names = [n for n, _ in build._BUILD_STEPS]
    assert names.index("set_hostname") < names.index("set_node_timezone") \
        < names.index("final_verification")


def test_the_certificate_records_the_zone():
    body = func_source("workflows/build.py", "birth_certificate")
    assert '"timezone": wf.profile.timezone or None' in body


def test_the_birth_screen_reads_the_medics_own_zone_into_the_profile():
    body = func_source("ui/screens/birth_screen.py", "_make_workflow", cls="BirthScreen")
    assert "prof.timezone = " in body and "current_timezone(" in body


def test_the_card_writer_carries_the_medics_zone_for_first_boot():
    from provisioning import pi_imager as pi
    toml = pi.build_custom_toml("h", "pi", "pw", timezone="Australia/Sydney",
                                pw_hasher=lambda p: "x")
    assert '[locale]' in toml and 'timezone = "Australia/Sydney"' in toml
    assert "[locale]" not in pi.build_custom_toml("h", "pi", "pw", pw_hasher=lambda p: "x")
    src = open(os.path.join(ROOT, "provisioning/pi_imager.py"), encoding="utf-8").read()
    flash = src[src.index("def flash("):src.index("def flash(") + 4000]
    assert "timezone: str = \"\"" in flash
    assert "timezone=timezone" in flash, "flash must hand its zone to the toml"
    screen = open(os.path.join(ROOT, "ui/screens/pi_imager_screen.py"), encoding="utf-8").read()
    i = screen.index("pi_imager.flash(")
    assert "timezone=" in screen[i:i + 900] and "current_timezone" in screen
