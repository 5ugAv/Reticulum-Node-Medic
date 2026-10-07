"""Waveshare UPS Module 3S battery monitor + low-battery safe-shutdown.

The UPS carries an **INA219** on the Pi's GPIO I2C bus (bus 1) at address **0x43**.
It lets the medic read its own pack voltage/current so it can (a) show a battery
gauge and (b) shut ITSELF down cleanly before the 3S Li-ion pack sags and a
brown-out corrupts the SD card — the same corruption class we protect against with
the slide-to-power-off.

Dormant-safe: with no UPS (I2C bus 1 not enabled, or nothing at 0x43) every read
returns ``present=False`` and the guard does nothing, so this is inert on a medic
without the HAT. The INA219 register math + the state-of-charge curve are PURE and
separated from the smbus transport, so all the logic is unit-testable with no
hardware. The smbus2 import is lazy (inside the transport) so this module stays
importable in CI, which has neither smbus2 nor an I2C bus.

SAFETY NOTE: the shutdown trigger is gated on **pack VOLTAGE**, not current
direction. A plugged-in/charging 3S pack sits at 11–12.6 V; only a pack running
low ON battery drops toward ~9.3 V (≈3.1 V/cell). So a voltage gate can't fire
while charging, and it doesn't depend on the INA219 current SIGN (whose convention
we can't verify until the hardware is in hand). Current is used only for the
nicer charging indicator.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

INA219_ADDR = 0x43
#: The INA219's address is set by solder pads and VARIES between Waveshare
#: revisions — HAWKEYE's UPS 3S answers at 0x41 (2026-08-25). Probe the
#: family rather than assume; first answer wins and is remembered.
INA219_CANDIDATES = (0x43, 0x41, 0x42, 0x40)
_found_addr = None
I2C_BUS = 1                     # Pi 5 GPIO header I2C (dtparam=i2c_arm=on)

_REG_CONFIG = 0x00
_REG_BUSVOLT = 0x02
_REG_CURRENT = 0x04
_REG_CALIB = 0x05

#: INA219 calibration for the Waveshare UPS shunt — mirrors Waveshare's reference
#: driver (cal 4096 -> current LSB 0.1 mA/bit). Only affects the current READING;
#: the safety path uses voltage, so an off calibration can't cause a bad shutdown.
_CAL_VALUE = 4096
_CURRENT_LSB_MA = 0.1

#: Per-cell Li-ion open-circuit voltage -> state-of-charge. Rough but fine for a
#: gauge + warnings; the curve is flat in the middle so it's an estimate there.
_SOC_CURVE = [
    (3.00, 0), (3.30, 8), (3.45, 15), (3.55, 25), (3.65, 40), (3.75, 55),
    (3.85, 70), (3.95, 82), (4.05, 92), (4.15, 98), (4.20, 100),
]


# ---- pure register + SoC math (unit-tested, no hardware) --------------------

def bus_voltage(raw_busvolt: int) -> float:
    """INA219 bus-voltage register (already byte-ordered) -> pack volts. Bits
    [15:3] are the value in 4 mV steps."""
    return ((raw_busvolt >> 3) * 4) / 1000.0


def _signed16(raw: int) -> int:
    return raw - 65536 if raw >= 32768 else raw


def current_ma(raw_current: int, lsb_ma: float = _CURRENT_LSB_MA) -> float:
    """Signed pack current in mA (sign convention is hardware-dependent — used for
    the charging indicator only, never for the shutdown decision)."""
    return _signed16(raw_current) * lsb_ma


def pack_percent(voltage: float, cells: int = 3) -> int:
    """Estimate state-of-charge (0–100) from pack voltage via the per-cell curve."""
    if voltage <= 0:
        return 0
    v = voltage / cells
    if v <= _SOC_CURVE[0][0]:
        return 0
    if v >= _SOC_CURVE[-1][0]:
        return 100
    for (v0, p0), (v1, p1) in zip(_SOC_CURVE, _SOC_CURVE[1:]):
        if v0 <= v <= v1:
            frac = (v - v0) / (v1 - v0) if v1 != v0 else 0.0
            return int(round(p0 + frac * (p1 - p0)))
    return 0


@dataclass
class UpsState:
    present: bool
    voltage: float = 0.0        # pack volts
    current_ma: float = 0.0     # signed (sign convention TBD on hardware)
    percent: int = 0
    charging: bool = False      # best-effort, from current sign

    @property
    def discharging(self) -> bool:
        return self.present and not self.charging


#: reader(reg:int) -> raw 16-bit register value (byte-ordered), or a transport.
Reader = Callable[[int], int]


def interpret(v_raw: int, i_raw: int) -> UpsState:
    """Build a UpsState from raw INA219 bus-voltage + current register values."""
    v = bus_voltage(v_raw)
    i = current_ma(i_raw)
    return UpsState(present=True, voltage=round(v, 2), current_ma=round(i, 1),
                    percent=pack_percent(v), charging=i > _CHARGE_MA)


#: |current| above this (mA) reads as charging (positive) — display only.
_CHARGE_MA = 50.0


# ---- transport (lazy smbus2; no-op when absent) -----------------------------

#: ONE bus handle per process. Opening /dev/i2c-1 on every read and never
#: closing it bled Node Medic 2's app dry: 943 of its 1024 descriptors were
#: the I2C bus after eleven hours, and from then on every file read in the
#: app failed (2026-10-07). The handle is shared, guarded, and dropped (closed)
#: on the first I/O error so the next read reopens cleanly.
_shared_reader: Optional[Reader] = None
_shared_bus = None
_shared_lock = threading.Lock()


def _drop_shared() -> None:
    global _shared_reader, _shared_bus
    with _shared_lock:
        b, _shared_reader, _shared_bus = _shared_bus, None, None
    if b is not None:
        try:
            b.close()
        except Exception:
            pass


def _open_reader(bus: int = I2C_BUS, addr: int = None) -> Optional[Reader]:
    """The process's shared INA219 reader, opened on first use; None when
    I2C/the device is absent (no HAT, bus not enabled)."""
    global _shared_reader, _shared_bus
    with _shared_lock:
        if _shared_reader is not None:
            return _shared_reader
        opened = _open_reader_fresh(bus, addr)
        if opened is None:
            return None
        _shared_reader, _shared_bus = opened
        return _shared_reader


def _open_reader_fresh(bus: int = I2C_BUS, addr: int = None):
    """Open the bus and build a reader(reg)->word for the INA219 — returns
    ``(reader, bus_handle)`` or None when I2C/the device is absent (the bus
    handle is CLOSED on every failure path). Writes the calibration once so
    current reads work. With no *addr* given, probes INA219_CANDIDATES and
    remembers the answer."""
    global _found_addr
    try:
        import smbus2
    except Exception:
        return None
    try:
        b = smbus2.SMBus(bus)
    except Exception:
        return None
    if addr is None:
        candidates = ([_found_addr] if _found_addr is not None
                      else list(INA219_CANDIDATES))
        for cand in candidates:
            try:
                b.read_byte(cand)
                _found_addr = addr = cand
                break
            except Exception:
                continue
        if addr is None:
            try:
                b.close()
            except Exception:
                pass
            return None

    def _swap(w: int) -> int:            # smbus is little-endian; INA219 big-endian
        return ((w & 0xFF) << 8) | (w >> 8)

    try:
        b.write_word_data(addr, _REG_CALIB, _swap(_CAL_VALUE))
    except Exception:
        try:
            b.close()
        except Exception:
            pass
        return None

    def read(reg: int) -> int:
        return _swap(b.read_word_data(addr, reg))

    return read, b


def read_ups(reader: Optional[Reader] = None) -> UpsState:
    """Current battery state, or ``UpsState(present=False)`` when no UPS is found.
    Pass a fake *reader* in tests; production opens the real INA219."""
    r = reader if reader is not None else _open_reader()
    if r is None:
        return UpsState(present=False)
    try:
        with _shared_lock:
            v_raw = r(_REG_BUSVOLT)
            i_raw = r(_REG_CURRENT)
    except Exception:
        if reader is None:
            _drop_shared()          # a dead handle is closed, never kept
        return UpsState(present=False)
    return interpret(v_raw, i_raw)


# ---- the safe-shutdown guard ------------------------------------------------

@dataclass
class BatteryGuard:
    """Decides when the medic must warn or shut down on low battery. Voltage-gated
    (see module SAFETY NOTE) with a consecutive-read confirmation so a brief sag
    under load (a flash burst) can't trigger a shutdown."""
    warn_voltage: float = 10.5      # ≈3.5 V/cell, ~20%
    shutdown_voltage: float = 9.3   # ≈3.1 V/cell — above the BMS cutoff, act first
    confirm: int = 3                # consecutive low reads (~90 s at a 30 s poll)
    _low: int = 0
    _warned: bool = False

    def evaluate(self, st: UpsState) -> str:
        """Return 'ok' | 'warn' | 'shutdown' for a freshly-read state."""
        if not st.present or st.charging:
            self._low = 0
            if st.charging and st.percent > 30:
                self._warned = False
            return "ok"
        if st.voltage <= self.shutdown_voltage:
            self._low += 1
            if self._low >= self.confirm:
                return "shutdown"
            return "ok"
        self._low = 0
        if st.voltage <= self.warn_voltage and not self._warned:
            self._warned = True
            return "warn"
        if st.voltage > self.warn_voltage:
            self._warned = False
        return "ok"
