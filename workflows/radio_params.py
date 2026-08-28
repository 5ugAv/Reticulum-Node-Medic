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
    # rnodeconf's --tnc/-T branch VALIDATES txp with `0 <= txp <= 17` and on
    # any other value falls into an interactive input() prompt — which, under
    # our subprocess timeout, dies as a garbled "failed to save" with nothing
    # saying why (adversarial review vs rnodeconf source, 2026-08-20). The
    # SX1262 boards legally go to 22 dBm, so an operator raising the default
    # in Settings would arm this. Clamp HERE, out loud, rather than hang.
    if cfg.tx_power_dbm > 17:
        raise ValueError(
            f"rnodeconf refuses TX power above 17 dBm ({cfg.tx_power_dbm} "
            "requested) — it would hang on an interactive prompt. Use 17 or "
            "less for provisioning; higher power needs direct KISS commands.")
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
                        timeout: int = 120,
                        mode: str = "host") -> Tuple[bool, str]:
    """Write the radio params and set the board's operating mode.

    ``--tnc <params>`` sends CMD_CONF_SAVE (radio boots ACTIVE standalone with
    these params — a pocket RNode whose LED signals immediately). ``mode``:

    * ``"host"`` (default): follow the save with ``-N`` (CMD_CONF_DELETE) so the
      board is host-controlled — a Pi running rnsd drives the radio. The board
      shows "Missing Config" until its host connects; that is normal.
    * ``"tnc"``: leave the saved config in place — the radio is live on boot.

    Returns ``(ok, human_message)``. (Params must be valid for the provisioned
    model, e.g. TX power within the model's cap, or the radio stays offline.)"""
    cfg = cfg or RadioConfig()
    # HARD GATE at the write boundary — writing radio params to the medic's own
    # radio would break its mesh vantage (house rule; hole found by the
    # 2026-08-01 bug hunt: this boundary had no gate).
    try:
        from ui.onboard_roster import assert_flashable, guard_is_active
        if guard_is_active():
            assert_flashable(port)
    except Exception as e:            # noqa: BLE001
        return False, f"Refusing to write radio params: {e}"
    code, out, err = connection.run(set_params_command(port, cfg), timeout=timeout)
    if code != 0:
        return False, (f"Could not write radio params (exit {code}): "
                       f"{(err or out)[-160:]}")
    summary = (f"{cfg.frequency_mhz:g} MHz / BW{cfg.bandwidth_khz:g} / "
               f"SF{cfg.spreading_factor} / CR{cfg.coding_rate} / "
               f"{cfg.tx_power_dbm} dBm")
    if mode == "tnc":
        return True, (f"Baked radio params at birth: {summary}; radio live "
                      f"(standalone TNC mode).")
    code, out, err = connection.run(normal_mode_command(port), timeout=timeout)
    if code != 0:
        return False, (f"Params written but could not return the board to "
                       f"host-controlled mode (exit {code}): {(err or out)[-160:]}")
    return True, (f"Baked radio params at birth: {summary}, left host-controlled.")


def bluetooth_on_command(port: str) -> str:
    """rnodeconf line that ENABLES Bluetooth on the board, persisted in its
    config. BIRTH POLICY (operator, 2026-08-28): BLE is switched on as an
    explicit provisioning step — never a firmware default — so a keeper's
    board works out of the box while a wild node's firmware stays silent.
    Background (2026-08-27 T-Echo finding): BLE was compiled and healthy on
    every nRF build and simply never enabled; no birth path wrote the byte."""
    return f"rnodeconf {port} --bluetooth-on"


def enable_bluetooth_at_birth(connection: Connection, port: str,
                              timeout: int = 60) -> Tuple[bool, str]:
    """Send the persistent BLE enable at birth. Returns ``(ok, message)``.

    rnodeconf logs "Enabling Bluetooth..." and sends CMD_BT_CTRL
    fire-and-forget — no acknowledgement is echoed — so success here means
    THE COMMAND WAS DELIVERED; the node's BLE row filling on its own screen
    is the visible confirmation. Same write-boundary guard as every other
    board write: the medic's own radio is never touched."""
    try:
        from ui.onboard_roster import assert_flashable, guard_is_active
        if guard_is_active():
            assert_flashable(port)
    except Exception as e:            # noqa: BLE001
        return False, f"Refusing to write to this port: {e}"
    code, out, err = connection.run(
        "export PATH=$HOME/.local/bin:$PATH && "
        f"timeout {timeout} " + bluetooth_on_command(port) + " 2>&1",
        timeout=timeout + 30)
    text = (out or "") + (err or "")
    if code != 0 or "Enabling Bluetooth" not in text:
        return False, (f"Bluetooth enable did not go through (exit {code}): "
                       f"{text.strip()[-160:] or 'no output'}")
    return True, ("Bluetooth enabled at birth (persisted on the board) — "
                  "its BLE row fills when the screen next redraws.")
