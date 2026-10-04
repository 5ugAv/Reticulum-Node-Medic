"""PROBE names the board it is pointed at and refuses to guess between two
(operator, 2026-10-04: "there's nothing here showing me which board I'm
actually fixing ... perhaps the user might have two boards plugged in at
once"). Pointed at a board on the medic's own USB it runs the board module
only — the other six inspect the medic itself (the fourteen "faults")."""
import json
import os
from unittest.mock import patch

import ui.hw_factories as hw
from diagnostics.radio_firmware import RadioFirmwareCheck
from tests.srcutil import func_source, src
from workflows.repair import BOARD_MODULES, RepairWorkflow

LANGS = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")
NEW_KEYS = (
    "Only the plugged-in board is checked. The medic's own radio is never touched.",
    "no board on USB yet — plug one in",
    "two boards on USB — leave just the one to check",
)


def test_probe_target_has_three_states():
    assert hw.probe_target(lambda: []) == ("none", [])
    assert hw.probe_target(lambda: ["/dev/ttyACM1"]) == ("one", ["/dev/ttyACM1"])
    state, ports = hw.probe_target(lambda: ["/dev/ttyACM1", "/dev/ttyACM2"])
    assert state == "many" and len(ports) == 2


def test_port_label_reads_the_product_name_from_the_by_id_link():
    links = {"/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_"
             "F0:F5:BD:01:02:03-if00": "/dev/ttyACM1"}
    with patch("ui.hw_factories.glob.glob", return_value=list(links)), \
         patch("ui.hw_factories.os.path.realpath",
               side_effect=lambda p: links.get(p, p)):
        assert hw.port_label("/dev/ttyACM1") == \
            "Espressif USB JTAG serial debug unit on /dev/ttyACM1"
    with patch("ui.hw_factories.glob.glob", return_value=[]):
        assert hw.port_label("/dev/ttyACM1") == "/dev/ttyACM1"


def test_probe_target_label_names_one_board_and_words_nothing_else():
    with patch("ui.hw_factories.port_label", return_value="Heltec on /dev/ttyACM1"):
        assert hw.probe_target_label(lambda: ["/dev/ttyACM1"]) == \
            ("one", "Heltec on /dev/ttyACM1")
    assert hw.probe_target_label(lambda: []) == ("none", "")
    assert hw.probe_target_label(lambda: ["a", "b"]) == ("many", "")


def test_two_boards_plugged_in_is_a_refusal_not_a_guess():
    with patch("platform.system", return_value="Linux"):
        wf = hw.make_repair_workflow(
            lambda: None, ports_fn=lambda: ["/dev/ttyACM1", "/dev/ttyACM2"])
    assert isinstance(wf, hw._HonestFailWorkflow)
    assert "Two boards" in wf.message and wf.title == "Which board?"


def test_one_board_on_the_medic_is_probed_board_only_and_named():
    with patch("platform.system", return_value="Linux"), \
         patch("ui.hw_factories.port_label", return_value="Heltec on /dev/ttyACM1"):
        wf = hw.make_repair_workflow(lambda: None, ports_fn=lambda: ["/dev/ttyACM1"])
    assert isinstance(wf, RepairWorkflow)
    assert BOARD_MODULES == [RadioFirmwareCheck]
    assert [type(m) for m in wf.modules] == BOARD_MODULES
    assert wf.target_label == "Heltec on /dev/ttyACM1"
    assert wf.profile.radio.serial_port == "/dev/ttyACM1"


# -- the screen and its wiring (source pins; Kivy is not importable here) -----

def test_the_screen_names_the_target_on_entry_and_on_every_run():
    s = src("ui/screens/probe_screen.py")
    assert "target_fn=None" in s
    enter = func_source("ui/screens/probe_screen.py", "enter", cls="ProbeScreen")
    assert "_refresh_target" in enter
    start = func_source("ui/screens/probe_screen.py", "start", cls="ProbeScreen")
    assert "self._refresh_target(self._workflow)" in start
    assert start.index("self._workflow_factory()") < start.index('tr("Checking {name}...")')
    refresh = func_source("ui/screens/probe_screen.py", "_refresh_target",
                          cls="ProbeScreen")
    assert 'tr("no board on USB yet — plug one in")' in refresh
    assert 'tr("two boards on USB — leave just the one to check")' in refresh
    # the scope line, split over two source lines
    assert "Only the plugged-in board is checked. The " in s
    assert "medic's own radio is never touched." in s


def test_the_header_and_the_issue_rows_grow_with_their_text():
    s = src("ui/screens/probe_screen.py")
    assert "grow_to_text(self.header)" in s
    row = func_source("ui/screens/probe_screen.py", "_issue_row", cls="ProbeScreen")
    assert "grow_to_text(text)" in row and 'setattr(row, "height"' in row


def test_the_app_no_longer_freezes_the_target_name():
    app = src("ui/app.py")
    assert "This node + attached board" not in app
    assert "target_fn=hw.probe_target_label" in app
    assert "on_pre_enter=lambda *_: _probe_screen.enter()" in app


def test_the_new_strings_are_translated_in_every_language():
    for code in LANGS:
        d = json.load(open(os.path.join("assets", "i18n", code + ".json"),
                           encoding="utf-8"))
        for key in NEW_KEYS:
            assert d.get(key) and d[key] != key, (code, key)
