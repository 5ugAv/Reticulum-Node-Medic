"""Real-vs-demo factory selection for the medic's own hardware.

Guards the fix for the emulated-flash bug: on a real medic (Linux + a board on
USB) the UI runs a genuine LocalConnection workflow; otherwise the explorable
demo. A Heltec V4 is always routed to the RGB firmware.
"""

from unittest.mock import patch

from transport.connection import EmulatedConnection
from workflows.rnode_boards import get_board
from workflows.rnode_flash import RNodeFlashWorkflow
from workflows.rnode_v4_rgb import HeltecV4RGBWorkflow
from workflows.rtnode_build import RTNodeBuildWorkflow
from workflows.repair import RepairWorkflow
from ui import hw_factories as hw

V4 = get_board("heltec32_v4")


def _demo_flash(board):
    return ("DEMO_FLASH", board)


# -- safety: never flash the medic's own (busy) radio ---------------------

def test_local_board_ports_excludes_busy_ports():
    # the medic's own radio (ttyACM0, held by the splitter) is BUSY -> excluded;
    # a freshly-plugged free board (ttyACM1) is flashable.
    busy = {"/dev/ttyACM0": True, "/dev/ttyACM1": False}
    with patch("glob.glob",
               side_effect=lambda p: ["/dev/ttyACM0", "/dev/ttyACM1"]
               if "ttyACM" in p else []):
        free = hw.local_board_ports(busy_fn=lambda p: busy.get(p, True))
    assert free == ["/dev/ttyACM1"]


def test_port_busy_fails_closed_on_uncertainty():
    assert hw._port_busy("/dev/ttyACM0", runner=lambda a: 0) is True   # held
    assert hw._port_busy("/dev/ttyACM0", runner=lambda a: 1) is False  # free

    def boom(argv):
        raise OSError("fuser missing")
    # can't tell -> treat as busy so we NEVER risk the medic's own radio
    assert hw._port_busy("/dev/ttyACM0", runner=boom) is True


def test_flash_refuses_when_only_the_busy_radio_is_present():
    # no FREE board (only the medic's busy radio) -> demo, never a real flash
    with patch("platform.system", return_value="Linux"):
        got = hw.make_rnode_flash(V4, _demo_flash, ports_fn=lambda: [])
    assert got == ("DEMO_FLASH", V4)


# -- hardware gate --------------------------------------------------------

def test_hardware_present_needs_linux_and_a_port():
    with patch("platform.system", return_value="Linux"):
        assert hw.hardware_present(ports_fn=lambda: ["/dev/ttyACM0"]) is True
        assert hw.hardware_present(ports_fn=lambda: []) is False
    with patch("platform.system", return_value="Darwin"):     # dev box
        assert hw.hardware_present(ports_fn=lambda: ["/dev/ttyACM0"]) is False


# -- fall back to the demo when there's no board --------------------------

def test_rnode_flash_uses_demo_without_hardware():
    with patch("platform.system", return_value="Darwin"):
        got = hw.make_rnode_flash(V4, _demo_flash, ports_fn=lambda: [])
    assert got == ("DEMO_FLASH", V4)


def test_rtnode_and_repair_use_demo_without_hardware():
    with patch("platform.system", return_value="Darwin"):
        assert hw.make_rtnode_build(lambda: "DEMO_RT", ports_fn=lambda: []) == "DEMO_RT"
        assert hw.make_repair_workflow(lambda: "DEMO_RP", ports_fn=lambda: []) == "DEMO_RP"


# -- real workflows on hardware -------------------------------------------

def test_rnode_flash_v4_forces_rgb_on_hardware(monkeypatch):
    monkeypatch.setattr(hw, "rgb_firmware_available", lambda *a, **k: True)
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    wf = hw.make_rnode_flash(V4, _demo_flash, connection=conn,
                             ports_fn=lambda: ["/dev/ttyACM0"])
    assert isinstance(wf, HeltecV4RGBWorkflow)     # never stock for a boxed V4


def test_rnode_flash_v4_stock_when_rgb_not_built(monkeypatch):
    # if the RGB firmware isn't compiled on this medic, fall to the stock flow
    monkeypatch.setattr(hw, "rgb_firmware_available", lambda *a, **k: False)
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    wf = hw.make_rnode_flash(V4, _demo_flash, connection=conn,
                             ports_fn=lambda: ["/dev/ttyACM0"])
    assert isinstance(wf, RNodeFlashWorkflow)


def test_rtnode_and_repair_are_real_on_hardware():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    assert isinstance(hw.make_rtnode_build(lambda: None, connection=conn),
                      RTNodeBuildWorkflow)
    assert isinstance(hw.make_repair_workflow(lambda: None, connection=conn),
                      RepairWorkflow)


def test_real_workflows_use_a_local_connection():
    from transport.connection import LocalConnection
    with patch("platform.system", return_value="Linux"):
        wf = hw.make_repair_workflow(lambda: None,
                                     ports_fn=lambda: ["/dev/ttyACM0"])
    assert isinstance(wf.connection, LocalConnection)
