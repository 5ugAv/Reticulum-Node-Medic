"""The UPS gauge must not open /dev/i2c-1 on every read: Node Medic 2's app
held 943 bus handles after eleven hours and every later file read in the app
failed (2026-10-07)."""
import sys
import types

import pytest

from monitor import ups


class _FakeBus:
    opened = 0
    closed = 0
    fail_reads = False
    no_device = False

    def __init__(self, bus):
        _FakeBus.opened += 1
    def read_byte(self, addr):
        if _FakeBus.no_device:
            raise OSError(121, "Remote I/O error")
        return 0
    def write_word_data(self, addr, reg, val):
        return None
    def read_word_data(self, addr, reg):
        if _FakeBus.fail_reads:
            raise OSError(5, "I/O error")
        return 0x1234
    def close(self):
        _FakeBus.closed += 1


@pytest.fixture
def fake_smbus(monkeypatch):
    mod = types.ModuleType("smbus2"); mod.SMBus = _FakeBus
    monkeypatch.setitem(sys.modules, "smbus2", mod)
    _FakeBus.opened = _FakeBus.closed = 0
    _FakeBus.fail_reads = _FakeBus.no_device = False
    monkeypatch.setattr(ups, "_found_addr", None)
    monkeypatch.setattr(ups, "_shared_reader", None)
    monkeypatch.setattr(ups, "_shared_bus", None)
    yield _FakeBus


def test_fifty_reads_open_the_bus_once(fake_smbus):
    for _ in range(50):
        assert ups.read_ups().present
    assert fake_smbus.opened == 1 and fake_smbus.closed == 0


def test_an_io_error_closes_the_handle_and_the_next_read_reopens(fake_smbus):
    assert ups.read_ups().present
    fake_smbus.fail_reads = True
    assert not ups.read_ups().present
    assert fake_smbus.closed == 1
    fake_smbus.fail_reads = False
    assert ups.read_ups().present
    assert fake_smbus.opened == 2 and fake_smbus.closed == 1


def test_no_hat_means_no_handle_kept(fake_smbus):
    """Node Medic 1 after its I2C-enabling reboot: bus present, no INA219."""
    fake_smbus.no_device = True
    for _ in range(20):
        assert not ups.read_ups().present
    assert fake_smbus.opened == 20 and fake_smbus.closed == 20


def test_a_caller_supplied_reader_is_not_cached(fake_smbus):
    st = ups.read_ups(reader=lambda reg: 0x3412)
    assert st.present and fake_smbus.opened == 0
