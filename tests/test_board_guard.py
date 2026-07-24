"""The medic must NEVER write/erase/reset its own radio (Jonesey, or a clone's).

Two guarantees locked here:
  1. behavioural — ``assert_flashable`` refuses an onboard board (by roster serial
     OR by service-binding) and fails CLOSED on an unresolvable serial.
  2. architectural — the known hardware-WRITE paths (RTNode flash, adopt serial
     reset) actually route through the gate, so a refactor can't silently drop it.
"""

import json
import os

import pytest

from ui import onboard_roster as R
from ui.onboard_roster import ProtectedBoardError, assert_flashable


@pytest.fixture
def roster_file(tmp_path):
    p = tmp_path / "onboard.json"
    p.write_text(json.dumps({"jonesey_lora": "A1:B2:C3:D4:E5:F6"}))
    return str(p)


def _stub_serial(monkeypatch, mapping):
    monkeypatch.setattr(R, "serial_for_port", lambda port: mapping.get(port))


def test_work_board_passes(monkeypatch, roster_file):
    _stub_serial(monkeypatch, {"/dev/ttyACM0": "02:00:00:04:00:04"})   # FAITH
    assert assert_flashable("/dev/ttyACM0", path=roster_file,
                            service_serials=set()) is True


def test_onboard_by_roster_is_refused(monkeypatch, roster_file):
    _stub_serial(monkeypatch, {"/dev/ttyACM1": "A1:B2:C3:D4:E5:F6"})   # Jonesey
    with pytest.raises(ProtectedBoardError):
        assert_flashable("/dev/ttyACM1", path=roster_file, service_serials=set())


def test_onboard_by_service_binding_is_refused(monkeypatch, tmp_path):
    # empty roster (e.g. a fresh clone) but rnsd holds the radio -> still refused
    empty = tmp_path / "onboard.json"
    empty.write_text("{}")
    _stub_serial(monkeypatch, {"/dev/ttyACM1": "A1:B2:C3:D4:E5:F6"})
    with pytest.raises(ProtectedBoardError):
        assert_flashable("/dev/ttyACM1", path=str(empty),
                         service_serials={"A1:B2:C3:D4:E5:F6"})


def test_unresolvable_serial_fails_closed(monkeypatch, roster_file):
    _stub_serial(monkeypatch, {})            # serial_for_port -> None
    with pytest.raises(ProtectedBoardError):
        assert_flashable("/dev/ttyACM9", path=roster_file, service_serials=set())


def test_error_message_names_the_board(monkeypatch, roster_file):
    _stub_serial(monkeypatch, {"/dev/ttyACM1": "A1:B2:C3:D4:E5:F6"})
    with pytest.raises(ProtectedBoardError) as ei:
        assert_flashable("/dev/ttyACM1", path=roster_file, service_serials=set())
    assert "onboard" in str(ei.value).lower()


# -- architectural: the write paths must route through the gate ---------------

def _src(rel):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, rel)) as f:
        return f.read()


def test_rtnode_flash_calls_the_gate():
    assert "assert_flashable" in _src("workflows/rtnode_build.py"), (
        "flash_firmware must call assert_flashable before uploading")


def test_adopt_serial_read_calls_the_gate():
    assert "assert_flashable" in _src("ui/adopt_live.py"), (
        "read_board_banner must call assert_flashable before resetting a port")
