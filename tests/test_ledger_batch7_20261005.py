"""Readiness ledger, 2026-10-05 early, batch seven — privacy leftovers, stale
docs, and small honesty items (map fallback, chat store, outage notice, adopt
screen, APK picking, the suite's own hygiene)."""
import glob
import json
import os

from tests.srcutil import ROOT, src


# -- #113 the picker never starts in the developer's city -------------------

def test_default_map_centre_is_the_fleet_or_nothing():
    from monitor.geo import default_map_centre
    from monitor.registry import NodeRegistry
    assert default_map_centre(None) is None
    reg = NodeRegistry()
    assert default_map_centre(reg, 1000.0) is None
    reg.register("aa" * 16, name="A", lat=-30.0, lon=150.0)
    reg.register("bb" * 16, name="B", lat=-32.0, lon=152.0)
    lat, lon = default_map_centre(reg, 1000.0)
    assert abs(lat + 31.0) < 1e-9 and abs(lon - 151.0) < 1e-9


def test_no_hard_coded_city_remains_in_the_ui():
    for path in glob.glob(os.path.join(ROOT, "ui", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            assert "-37.8136" not in f.read(), path
    assert "start_note: str = \"\"" in src("ui/widgets/confirm_location.py")
    assert "start_note=start_note" in src("ui/app.py")
    assert "start_note=start_note" in src("ui/screens/birth_guide_screen.py")


# -- #114 / docs -----------------------------------------------------------

def test_examples_and_docs_carry_no_fleet_or_stale_claims():
    assert "mynode.local or 192.168.1.50" in src("scripts/push_reporter.py")
    readme = src("README.md")
    assert "Carto" not in readme and "1776" not in readme
    assert "Fixed 2026-09-09" in src("docs/HANDOVER.md")
    assert "invertible, and this is the most urgent" not in src("docs/HANDOVER.md")
    assert src("docs/encrypt-at-rest.md").startswith(
        "# Encrypt-at-rest for the Node Medic\n\n> **Superseded (2026-09-03).**")
    assert "(359, 224)" in src("docs/FRONT_PAGE_BRIEF.md")
    assert "ui/touch_input.py" in src("scripts/start_ui.sh")
    from provisioning.encryption_flow import covered_lines
    assert not any("714" in line for _ok, line in covered_lines())


# -- #23 the slider comes back to its resting words --------------------------

def test_a_refused_power_off_restores_the_resting_hint():
    s = src("ui/widgets/slide_to_power.py")
    assert "self._hint_rest = " in s
    reset = s[s.index("def reset("):s.index("def on_touch_up")]
    assert 'getattr(self, "_hint_rest", self.hint.text)' in reset


# -- #191 a broken record is dropped, not crashed on ------------------------

def test_a_half_written_message_record_is_dropped_and_counted(tmp_path, capsys):
    from monitor.lxmf_chat import MessageStore, IN
    store = MessageStore(str(tmp_path / "chat"))
    good = store.add_outgoing("c" * 32, "hello")
    bad = [{"id": "x"}, {"id": "y", "peer": "d" * 32, "dir": IN, "text": "hi", "ts": "soon"},
           dict(good)]
    with open(store._msg_file, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    fresh = MessageStore(str(tmp_path / "chat"))
    assert [m["id"] for m in fresh.thread("c" * 32)] == [good["id"]]
    convs = fresh.conversations()
    assert convs and convs[0].peer == "c" * 32
    assert "dropped 2 unreadable" in capsys.readouterr().out


# -- #197 / #202 the suite itself ------------------------------------------

def test_the_suite_runs_from_the_repo_root():
    assert os.path.realpath(os.getcwd()) == os.path.realpath(ROOT)


def test_the_app_server_can_take_an_ephemeral_loopback_port():
    from workflows.phone_serve import AppServer
    s = AppServer("/tmp", port=0, bind_host="127.0.0.1")
    assert s.bind_host == "127.0.0.1" and s.port == 0
    assert "self._httpd.server_address[1]" in src("workflows/phone_serve.py")


# -- #63 / #214 / #180 / #69 / #52 / #96 / #138 words --------------------------

def test_the_node_page_says_the_kind_in_words():
    n = src("ui/screens/node_detail_screen.py")
    assert 'tr("Raspberry Pi propagation node")' in n and 'tr("RTNode-2400")' in n
    assert 'tr("heard announcing — device unknown")' in n


def test_copy_that_promised_the_wrong_thing_is_gone():
    assert "just tap Continue" not in src("ui/screens/mitosis_screen.py")
    assert "pick it up again" not in src("ui/setup_flow.py")
    b = src("ui/screens/birth_guide_screen.py")
    assert "Radio:  not reported — adopt keeps the node's own settings" in b
    assert "SF{p.get('sf')}" not in b
    assert "ask whoever maintains it" in src("workflows/build.py")
    rt = src("monitor/self_diagnose_runtime.py")
    assert "auto-recovery is coming" not in rt and "Birth the GPS Tracker" in rt
    assert "Install {NM_USB0_CONF}" not in src("monitor/self_diagnose.py")


def test_the_outage_notice_is_one_held_popup_with_real_ages():
    a = src("ui/app.py")
    assert "def _escalate_nodes(self, devices):" in a
    assert "self._escalate_nodes(ds)" in a
    sentence = a[a.index("def _outage_sentence"):a.index("def _save_node_watch")]
    assert 'device.get("last_seen_hours")' in sentence
    notice = a[a.index("def _outage_notice"):a.index("def _push_outage_alert")]
    assert "auto_dismiss=True" in notice and "schedule_once(lambda dt: p.dismiss()" not in notice


# -- #159 the carried APK is never a pre-release -----------------------------

def test_a_pre_release_apk_is_never_picked_over_the_release():
    from workflows.phone_apps import _pick_apk
    picked = _pick_apk([
        {"name": "Sideband_2.1.1_pre_release-universal.apk", "size": 90_000_000},
        {"name": "Sideband_2.1.0-universal.apk", "size": 88_000_000},
    ])
    assert picked["name"] == "Sideband_2.1.0-universal.apk"
    only = _pick_apk([{"name": "Sideband_2.1.1_pre_release-universal.apk", "size": 1}])
    assert only["name"].endswith(".apk")          # a release offering nothing else still yields


# -- #128 the card-verify labels translate -----------------------------------

def test_the_card_verify_labels_are_in_every_shipped_catalog():
    for code in ("es", "fr", "de", "ja", "ru", "pl", "id", "sv"):
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
            cat = json.load(f)
        for key in ("Name", "Key", "Power", "Battery gauge"):
            assert key in cat, (code, key)
