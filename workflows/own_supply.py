"""The medic's OWN power, checked before anyone is sent to chase cables.

Node Medic 2 spent an afternoon failing to write its Tracker's firmware: the
board dropped off USB at every sustained write, on three cables and two
sockets, and the screens said "check the cable". The cause was the medic's
own battery running down (it died of it at 20:30, 2026-10-06). A sagging
supply gives way exactly when a chip draws its biggest burst — a flash erase.

So: a flash or radio failure first asks THIS medic how its power is, and if
it is low, says so, before any word about cables or boards.
"""
from __future__ import annotations

import re
from typing import Optional

#: The Pi 5 PMIC's reading of its 5 V input; below this a USB load browns out.
LOW_5V = 4.8
#: Three 18650 cells in series (the Waveshare UPS 3S); ≤ 9.9 V ≈ 3.3 V/cell = low.
LOW_PACK_V = 9.9


def supply_warning(run) -> str:
    """One plain sentence when this medic's own power looks low, else "".

    *run* takes a shell string and returns ``(code, stdout, stderr)`` (a
    Connection's ``run``). Best-effort: a medic without ``vcgencmd`` or a
    battery monitor simply returns ""."""
    try:
        code, out, _e = run("vcgencmd get_throttled 2>/dev/null")
        m = re.search(r"0x([0-9a-fA-F]+)", out or "")
        bits = int(m.group(1), 16) if (code == 0 and m) else 0
        # bit 0 under-voltage now, bit 16 under-voltage has occurred
        if bits & 0x1:
            return ("This medic's own power is low right now (the Pi reports "
                    "under-voltage). Plug it into its mains supply before trying again.")
        code, out, _e = run("vcgencmd pmic_read_adc EXT5V_V 2>/dev/null")
        m = re.search(r"=\s*([0-9.]+)V", out or "")
        if code == 0 and m and float(m.group(1)) < LOW_5V:
            return (f"This medic's own supply is sagging ({float(m.group(1)):.2f} V). "
                    "Plug it into its mains supply before trying again.")
        pack = _pack_voltage(run)
        if pack is not None and pack <= LOW_PACK_V:
            return (f"This medic's battery is low ({pack:.1f} V). Put it on charge "
                    "before trying again.")
        if bits & 0x10000:
            return ("This medic's power dipped a short while ago (the Pi reports a "
                    "past under-voltage). Check it is on its mains supply.")
    except Exception:                                      # noqa: BLE001
        pass
    return ""


def _pack_voltage(run) -> Optional[float]:
    """The UPS pack voltage via the medic's own battery reader, if any."""
    try:
        from monitor.ups import read_ups
        st = read_ups()
        v = getattr(st, "voltage", None) if getattr(st, "present", False) else None
        return float(v) if v is not None else None
    except Exception:                                      # noqa: BLE001
        return None


def with_supply_note(message: str, run) -> str:
    """*message* with the supply sentence FIRST when power is the likelier
    cause — the medic checks its own side before blaming the bench."""
    note = supply_warning(run)
    return f"{note} {message}" if note else message
