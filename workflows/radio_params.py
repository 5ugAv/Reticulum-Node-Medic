"""Bake the canonical LoRa radio parameters into an RNode's EEPROM AT BIRTH.

A freshly flashed / re-provisioned RNode keeps whatever radio config the
autoinstall defaulted to (often 250 kHz / SF11). If that stale config is left in
place, rnsd aborts the interface with **"Radio state mismatch"** — the device's
reported parameters never match the host's configuration. This was mis-diagnosed
once as a broken custom-firmware build; the isolating test (fresh provision +
params-at-birth on the SAME custom firmware) came up clean under rnsd, proving
the fault was the stale stored config, not the firmware. So every flash path must
set the deployment params into the device BEFORE it is handed to rnsd.

rnodeconf only WRITES these params when a mode flag accompanies them: ``--tnc``
(or ``-N``) must be passed TOGETHER with ``--freq/--bw/--sf/--cr/--txp`` —
passing the radio flags alone is a silent no-op (verified live). We write them in
TNC mode, then return the device to ``-N`` (normal / host-controlled) so rnsd
drives the radio. This both satisfies "set params at birth" and leaves the board
in the mode a Pi+RNode propagation node needs.
"""

from __future__ import annotations

from typing import Optional, Tuple

from transport.connection import Connection
from node_profile import RadioConfig


def set_params_command(port: str, cfg: Optional[RadioConfig] = None) -> str:
    """rnodeconf line that writes the canonical radio params into the EEPROM.
    ``--tnc`` is required alongside the flags or rnodeconf silently ignores them.
    ``RadioConfig`` carries MHz/kHz; rnodeconf wants Hz."""
    cfg = cfg or RadioConfig()
    freq_hz = int(round(cfg.frequency_mhz * 1_000_000))
    bw_hz = int(round(cfg.bandwidth_khz * 1_000))
    return (f"rnodeconf {port} --tnc "
            f"--freq {freq_hz} --bw {bw_hz} "
            f"--sf {cfg.spreading_factor} --cr {cfg.coding_rate} "
            f"--txp {cfg.tx_power_dbm}")


def normal_mode_command(port: str) -> str:
    """Return the device to normal (host-controlled) mode so rnsd drives it."""
    return f"rnodeconf {port} -N"


def set_params_at_birth(connection: Connection, port: str,
                        cfg: Optional[RadioConfig] = None,
                        timeout: int = 120) -> Tuple[bool, str]:
    """Write the canonical params, then leave the board host-controlled.

    Returns ``(ok, human_message)``. This is the exact sequence proven on
    hardware to clear rnsd's "Radio state mismatch"."""
    cfg = cfg or RadioConfig()
    code, out, err = connection.run(set_params_command(port, cfg), timeout=timeout)
    if code != 0:
        return False, (f"Could not write radio params (exit {code}): "
                       f"{(err or out)[-160:]}")
    code, out, err = connection.run(normal_mode_command(port), timeout=timeout)
    if code != 0:
        return False, (f"Params written but could not return the board to "
                       f"host-controlled mode (exit {code}): {(err or out)[-160:]}")
    return True, (
        f"Baked radio params at birth: {cfg.frequency_mhz:g} MHz / "
        f"BW{cfg.bandwidth_khz:g} / SF{cfg.spreading_factor} / "
        f"CR{cfg.coding_rate} / {cfg.tx_power_dbm} dBm, left host-controlled.")
