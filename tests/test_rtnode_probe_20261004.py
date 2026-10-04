"""PROBE on an RTNode-2400 this medic built (bench, 2026-10-04, node 5A59).

The board enumerates as an "Espressif USB JTAG serial debug unit", never
answers a KISS detect, and prints nothing until it is reset — then its boot
log arrives at 1 s and its health beacon at 6 s. rnodeconf can never read
it; the medic's own reset-and-listen (the way every V4 birth is verified)
can. So PROBE recognises the board from its birth record (the roster keeps
the USB serial), names it, and runs the RTNode module with a real capture.
"""
import json
from unittest.mock import patch

from diagnostics.radio_firmware import RadioFirmwareCheck
from diagnostics.rtnode_2400 import CAPTURE_COMMAND, RTNode2400Check
from monitor import kin_roster
from node_profile import NodeProfile
from tests.srcutil import func_source, src
from transport.connection import EmulatedConnection
import ui.hw_factories as hw
from workflows.repair import BOARD_MODULES, RTNODE_MODULES

SERIAL = "F0:F5:BD:01:02:03"
ROSTER = {"aa" * 16: {"name": "5A59", "type": "rtnode2400", "hw_serial": SERIAL,
                      "device": "aa" * 16},
          "bb" * 16: {"name": "skyfinger", "type": "pi_propagation"}}


# -- the roster knows a board by its hardware serial ---------------------------

def test_node_for_hw_serial_matches_case_and_separator_insensitively(tmp_path):
    p = tmp_path / "kin.json"
    p.write_text(json.dumps(ROSTER))
    hit = kin_roster.node_for_hw_serial("f0-f5-bd-01-02-03", path=str(p))
    assert hit["name"] == "5A59" and hit["type"] == "rtnode2400"
    assert hit["rns_hash"] == "aa" * 16
    assert kin_roster.node_for_hw_serial("00:00:00:00:00:00", path=str(p)) is None
    assert kin_roster.node_for_hw_serial("", path=str(p)) is None
    assert kin_roster.node_for_hw_serial(None, path=str(p)) is None


# -- the header names the node -----------------------------------------------------

def test_a_known_board_is_named_after_its_node_and_kind():
    with patch("ui.onboard_roster.serial_for_port", return_value=SERIAL), \
         patch("monitor.kin_roster.load_roster", return_value=ROSTER), \
         patch("ui.usb_ports.describe_port", return_value="Port 3 (/dev/ttyACM1)"):
        ident = hw.board_identity("/dev/ttyACM1")
        label = hw.board_label("/dev/ttyACM1")
    assert ident == {"hw_serial": SERIAL, "name": "5A59", "type": "rtnode2400", "known": True}
    assert label == "5A59 — RTNode-2400 built by this medic, on Port 3 (/dev/ttyACM1)"


def test_an_unknown_board_falls_back_to_its_product_string():
    with patch("ui.onboard_roster.serial_for_port", return_value="AA:BB:CC:DD:EE:FF"), \
         patch("monitor.kin_roster.load_roster", return_value=ROSTER), \
         patch("ui.hw_factories.port_label",
               return_value="Espressif USB JTAG serial debug unit on /dev/ttyACM1"):
        assert hw.board_identity("/dev/ttyACM1")["known"] is False
        assert hw.board_label("/dev/ttyACM1").startswith("Espressif")


# -- the workflow picks the RTNode module with a REAL capture --------------------------

def test_a_known_rtnode_is_probed_by_listening_not_by_rnodeconf():
    with patch("platform.system", return_value="Linux"), \
         patch("ui.onboard_roster.serial_for_port", return_value=SERIAL), \
         patch("monitor.kin_roster.load_roster", return_value=ROSTER), \
         patch("ui.usb_ports.describe_port", return_value="Port 3 (/dev/ttyACM1)"):
        wf = hw.make_repair_workflow(lambda: None, ports_fn=lambda: ["/dev/ttyACM1"])
    assert RTNODE_MODULES == [RTNode2400Check]
    assert [type(m) for m in wf.modules] == RTNODE_MODULES
    cmd = wf.modules[0].capture_cmd
    assert cmd and "/dev/ttyACM1" in cmd and "s.rts=True" in cmd       # the reset pulse
    assert f"range({hw.RTNODE_LISTEN_S})" in cmd                       # the listen
    assert wf.target_label.startswith("5A59 — RTNode-2400")


def test_an_unknown_board_still_gets_the_rnode_radio_check():
    with patch("platform.system", return_value="Linux"), \
         patch("ui.onboard_roster.serial_for_port", return_value=None), \
         patch("ui.hw_factories.port_label", return_value="x on /dev/ttyACM1"):
        wf = hw.make_repair_workflow(lambda: None, ports_fn=lambda: ["/dev/ttyACM1"])
    assert [type(m) for m in wf.modules] == BOARD_MODULES == [RadioFirmwareCheck]


# -- the module runs the capture it is handed, inside its first check ------------------

def _log():
    return ("ESP-ROM:esp32s3-20210327\n[Boundary] modem installed\n"
            "[HealthBeacon] announce dst=11223344556677889900aabbccddeeff "
            "data=010000002400c7cc053b3f000602\n")


def test_the_capture_command_is_the_one_handed_over_with_a_long_timeout():
    c = EmulatedConnection().rule("python3 -c \"import serial", code=0, stdout=_log())
    chk = RTNode2400Check(c, NodeProfile())
    chk.capture_cmd = "python3 -c \"import serial,time; ...\""
    issues = chk.run()
    assert "boot_log_captured" not in {i.check_name for i in issues}
    assert "beacon_received" not in {i.check_name for i in issues}
    ran = [h for h in c.history if h.startswith("python3 -c")]
    assert ran, "the handed-over capture was never run"
    assert not any(h.startswith(CAPTURE_COMMAND) for h in c.history)


def test_a_silent_board_is_one_issue_and_nothing_else_is_guessed():
    c = EmulatedConnection().rule(CAPTURE_COMMAND, code=0, stdout="")
    issues = RTNode2400Check(c, NodeProfile()).run()
    assert [i.check_name for i in issues] == ["boot_log_captured"]
    assert issues[0].severity == "critical" and "reset pulse" in issues[0].description


def test_the_listen_happens_inside_the_first_check_so_the_screen_sees_it():
    run = func_source("diagnostics/rtnode_2400.py", "run", cls="RTNode2400Check")
    assert '"boot_log_captured", _capture' in run
    assert run.index("def _capture") < run.index("_parse_beacon(log)")
    screen = src("ui/screens/probe_screen.py")
    assert 'event.type == "check_start"' in screen
    assert 'tr("Checking {name}...").format(' in screen
