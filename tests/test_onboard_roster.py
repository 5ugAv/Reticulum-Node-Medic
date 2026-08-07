"""The medic's onboard roster: identify its OWN permanent boards by USB serial so
they're never treated as work boards (flash/PROBE/birth targets)."""

import json
from unittest.mock import patch

from ui import onboard_roster as roster
from ui import hw_factories as hw


import pytest


def test_register_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "onboard.json")
    roster.register("jonesey_lora", "A1:B2:C3:D4:E5:F6", path=p)
    roster.register("gps_tracker", "AA:BB:CC:DD:EE:FF", path=p)
    assert roster.load_roster(p) == {
        "jonesey_lora": "A1:B2:C3:D4:E5:F6",
        "gps_tracker": "AA:BB:CC:DD:EE:FF",
    }
    assert roster.onboard_serials(p) == {"A1:B2:C3:D4:E5:F6", "AA:BB:CC:DD:EE:FF"}


def test_load_missing_roster_is_empty(tmp_path):
    assert roster.load_roster(str(tmp_path / "nope.json")) == {}
    assert roster.onboard_serials(str(tmp_path / "nope.json")) == set()


@pytest.mark.onboard_guard   # drives the real lookups
def test_serial_for_port_reads_by_id_symlink():
    link = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_A1:B2:C3:D4:E5:F6-if00"
    with patch("glob.glob", return_value=[link]), \
         patch("os.path.realpath", side_effect=lambda x: "/dev/ttyACM0"
               if x in (link, "/dev/ttyACM0") else x):
        assert roster.serial_for_port("/dev/ttyACM0") == "A1:B2:C3:D4:E5:F6"


@pytest.mark.onboard_guard   # drives the real lookups
def test_is_onboard_matches_by_identity(tmp_path):
    p = str(tmp_path / "onboard.json")
    roster.register("jonesey_lora", "A1:B2:C3:D4:E5:F6", path=p)
    with patch.object(roster, "serial_for_port",
                      side_effect=lambda port: "A1:B2:C3:D4:E5:F6"
                      if port == "/dev/ttyACM0" else "02:00:00:01:00:01"):
        assert roster.is_onboard("/dev/ttyACM0", path=p) is True    # Jonesey
        assert roster.is_onboard("/dev/ttyACM1", path=p) is False   # a work board


def test_local_board_ports_excludes_onboard_even_when_free():
    # The key safety: Jonesey is the medic's own radio. Even if rnsd is stopped so
    # its port is FREE (not busy), the identity roster keeps it off the work list.
    with patch("glob.glob", side_effect=lambda pat: ["/dev/ttyACM0", "/dev/ttyACM1"]
               if "ttyACM" in pat else []):
        free = hw.local_board_ports(
            busy_fn=lambda p: False,                       # nothing busy
            onboard_fn=lambda p: p == "/dev/ttyACM0")      # ACM0 is Jonesey
    assert free == ["/dev/ttyACM1"]                        # only the work board


def test_repair_workflow_targets_the_work_board():
    from transport.connection import EmulatedConnection
    from workflows.repair import RepairWorkflow
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    wf = hw.make_repair_workflow(lambda: "DEMO", connection=conn,
                                 ports_fn=lambda: ["/dev/ttyACM1"])
    assert isinstance(wf, RepairWorkflow)
    # PROBE is pinned to the attached work board, NOT auto-detecting onto Jonesey
    assert wf.profile.radio.serial_port == "/dev/ttyACM1"


# ---- self-enrollment: a clone learns its OWN boards (#82) --------------------

def test_commission_attached_adopts_boards_by_serial(tmp_path):
    p = str(tmp_path / "onboard.json")
    serials = {"/dev/ttyACM0": "A1:B2:C3:D4:E5:F6", "/dev/ttyUSB0": "AA:BB:CC:DD:EE:01"}
    adopted = roster.commission_attached(
        ports=["/dev/ttyACM0", "/dev/ttyUSB0"],
        probe=lambda port: "rnode" if "ACM" in port else "gps",
        serial_fn=lambda port: serials.get(port), path=p)
    assert adopted == {"A1:B2:C3:D4:E5:F6": "rnode", "AA:BB:CC:DD:EE:01": "gps"}
    assert roster.onboard_serials(p) == {"A1:B2:C3:D4:E5:F6", "AA:BB:CC:DD:EE:01"}


def test_clone_starts_empty_then_self_commissions_its_own_serial(tmp_path):
    # A clone begins with NO onboard roster (parent serials are never copied) and
    # adopts its OWN board — a DIFFERENT serial from the parent's Jonesey.
    p = str(tmp_path / "onboard.json")
    assert roster.load_roster(p) == {}
    roster.commission_attached(ports=["/dev/ttyACM0"], probe=lambda _p: "rnode",
                               serial_fn=lambda _p: "99:88:77:66:55:44", path=p)
    assert roster.onboard_serials(p) == {"99:88:77:66:55:44"}


def test_commission_skips_ports_without_a_serial(tmp_path):
    p = str(tmp_path / "onboard.json")
    roster.commission_attached(ports=["/dev/ttyACM0"], probe=lambda _p: "rnode",
                               serial_fn=lambda _p: None, path=p)
    assert roster.load_roster(p) == {}                 # nothing adopted


# ---- two-layer is_onboard + fail-closed -------------------------------------

@pytest.mark.onboard_guard   # drives the real lookups
def test_is_onboard_second_layer_is_service_bound(tmp_path):
    p = str(tmp_path / "onboard.json")                 # empty roster
    with patch.object(roster, "serial_for_port", side_effect=lambda _port: "SVC:1"):
        assert roster.is_onboard("/dev/ttyACM0", path=p) is False
        assert roster.is_onboard("/dev/ttyACM0", path=p,
                                 service_serials={"SVC:1"}) is True   # operates like Jonesey


def test_is_flashable_work_board_fails_closed_on_unknown_serial(tmp_path):
    p = str(tmp_path / "onboard.json")
    with patch.object(roster, "serial_for_port", side_effect=lambda _port: None):
        assert roster.is_flashable_work_board("/dev/ttyACM9", path=p) is False   # refuse unknown
    with patch.object(roster, "serial_for_port", side_effect=lambda _port: "WORK:1"):
        assert roster.is_flashable_work_board("/dev/ttyACM9", path=p) is True    # known, not onboard


# ---- functional "operating like Jonesey" detection --------------------------

def test_service_device_paths_and_serials_from_unit_config():
    # The REAL rnode-splitter unit quotes the path: real_port='.../if00' — the
    # regex must stop at the closing quote/comma, not swallow it (greedy \S+ bug).
    unit = ("ExecStart=/usr/bin/python3 -c \"from monitor.serial_splitter import "
            "run; run(real_port='/dev/serial/by-id/usb-Espressif_x_A1:B2:C3:D4:E5:F6"
            "-if00', symlink='/tmp/rnode-jonesey')\"\n")
    paths = roster.service_device_paths(
        read_unit=lambda u: unit if u == "rnode-splitter" else "",
        units=("rnode-splitter", "rnsd", "gpsd"))
    assert paths == {"/dev/serial/by-id/usb-Espressif_x_A1:B2:C3:D4:E5:F6-if00"}
    ser = roster.service_bound_serials(device_paths=paths,
                                       serial_fn=lambda _p: "A1:B2:C3:D4:E5:F6")
    assert ser == {"A1:B2:C3:D4:E5:F6"}
