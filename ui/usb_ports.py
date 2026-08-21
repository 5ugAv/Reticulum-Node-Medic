"""Translate a tty into the number engraved beside its USB hole.

The medic's case has its four USB-A holes engraved 1-4, and "/dev/ttyACM1"
means nothing to an operator crouched over the box in the field — "Port 3"
does. The kernel names a device by its <bus>-<port> path segment (e.g.
/sys/bus/usb/devices/1-2), and which segment belongs to which engraved hole
is a fact about THIS medic's wiring, not something software can derive: we
measured it on 2026-08-21 by walking one board through every hole and
watching where it appeared. Jonesey (the medic's own permanent radio, in the
hole beside Ethernet) anchors the map — it must always read Port 1.

Honesty rule inherited from SPEC.md: a port number is either measured or
absent. The segments in the map (1-1, 1-2, 3-1, 3-2) are among the commonest
USB paths on ANY Linux box, so matching them proves nothing by itself — a
laptop, a Pi 4, the Lima sandbox or a second medic would all "match" against
an engraving they don't have. Every label therefore sits behind a POSITIVE
identity check, verified once per process: this host must be a Pi 5 with the
medic's roster machinery active AND Jonesey must actually resolve to segment
3-2 right now. If any of that fails — other machine, kernel renumbered the
buses, Jonesey unplugged for surgery — the whole map goes silent rather than
guess. Off the medic, every function here returns None / the raw path.
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Optional

#: Physical engraved hole per kernel <bus>-<port> segment, measured live on
#: 2026-08-21 by moving a single board through all four holes on THIS Pi 5.
#: Port 1 (top-left, beside Ethernet) is Jonesey's permanent home — which is
#: what lets _measure_medic() verify the map still holds at runtime.
#:
#: CAVEAT — shadow buses: a USB-3 device in one of the two blue holes shows
#: up on bus 2 or 4 ("2-1", "4-2"), and we have NOT walked a USB-3 device
#: through the holes to learn which blue hole is which. Those segments are
#: deliberately absent, so a 2-x/4-x device falls back to its raw /dev path
#: rather than getting a number we never measured.
MEDIC_PORT_MAP = {
    "3-2": 1,   # top-left, beside Ethernet — Jonesey
    "1-1": 2,   # top-right
    "1-2": 3,   # bottom-left
    "3-1": 4,   # bottom-right
}

#: A standalone <bus>-<port> path component, hub chains included ("1-1.3").
#: The ":1.0"-style interface suffix makes a component NOT match, which is
#: what keeps ".../1-2/1-2:1.0/..." resolving to "1-2".
_SEGMENT = re.compile(r"^\d+-\d+(\.\d+)*$")

#: tty -> (expiry, segment) memo. The chooser rebuild redraws several times a
#: second and must not fork udevadm each time — but a tty NUMBER is reused
#: across holes (unplug from Port 3, replug in Port 4, same /dev/ttyACM1), so
#: a forever-cache would eventually label the wrong hole. A few seconds is
#: long enough to absorb a redraw storm and short enough to follow a replug.
_MEMO_TTL = 5.0
_segment_memo: dict = {}

#: Once-per-process medic verdict (None = not yet measured). Reset in tests
#: via _reset_for_tests(); on the real medic the wiring doesn't change mid-run.
_medic_verdict: Optional[bool] = None


def _reset_for_tests() -> None:
    """Forget the cached medic verdict and segment memo (conftest calls this
    before every test so one test's host verdict can't leak into the next)."""
    global _medic_verdict
    _medic_verdict = None
    _segment_memo.clear()


def _run_udevadm(dev: str):
    """The one subprocess seam — tests replace THIS, never subprocess.run
    itself (patching the stdlib module's attribute is process-wide)."""
    return subprocess.run(["udevadm", "info", "-q", "path", "-n", dev],
                          capture_output=True, text=True, timeout=3)


def _segment_from_udev(dev: str) -> Optional[str]:
    """Ask udevadm for *dev*'s sysfs path and take the LAST plain segment
    before the interface suffix — the deepest hop is the device itself;
    earlier ones are hubs on the way. Any failure is None, never a guess."""
    try:
        out = _run_udevadm(dev)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    segments = [p for p in (out.stdout or "").strip().split("/")
                if _SEGMENT.match(p)]
    return segments[-1] if segments else None


def _resolve_segment(dev: str) -> Optional[str]:
    """Memoised (see _MEMO_TTL) segment lookup. Ungated — the identity check
    itself needs it to find where Jonesey is."""
    if not dev:
        return None
    now = time.monotonic()
    hit = _segment_memo.get(dev)
    if hit and hit[0] > now:
        return hit[1]
    seg = _segment_from_udev(dev)
    # Misses are memoised too: a host without udevadm shouldn't fork a doomed
    # child per redraw either.
    _segment_memo[dev] = (now + _MEMO_TTL, seg)
    return seg


def _device_tree_model() -> str:
    """The board name the firmware stamped into the device tree ("Raspberry Pi
    5 Model B Rev 1.0"), or "" wherever that file doesn't exist / can't be
    read — which already excludes every non-Pi host."""
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().decode("ascii", "ignore").rstrip("\x00")
    except Exception:
        return ""


def _jonesey_segment() -> Optional[str]:
    """Where Jonesey (the roster's rnode-role board) sits right now, or None.

    The roster records {role_tail: serial}; find the attached port carrying
    the rnode serial and resolve its segment. No rnode in the roster, Jonesey
    unplugged, or an unresolvable serial all mean None — and the caller then
    keeps the map dark."""
    try:
        from ui import onboard_roster as roster
        serials = {v for k, v in roster.load_roster().items()
                   if v and k.split("_")[0] == "rnode"}
        if not serials:
            return None
        for port in roster.attached_serial_ports():
            if roster.serial_for_port(port) in serials:
                return _resolve_segment(port)
    except Exception:
        return None
    return None


def _measure_medic() -> bool:
    """Positive identity: is this host THE medic whose holes we measured?

    Three independent facts must all hold — the roster machinery is live
    (guard_is_active: "the medic, and nowhere else"), the silicon says Pi 5
    (a Pi 4 or the Lima VM has the same bus numbers and no engraving), and
    Jonesey actually reads segment 3-2 today. The last one is the anchor: if
    a kernel update ever renumbers the buses, Jonesey moves off 3-2 and the
    whole map goes silent instead of lying about every hole at once."""
    try:
        from ui import onboard_roster as roster
        if not roster.guard_is_active():
            return False
    except Exception:
        return False
    if "Raspberry Pi 5" not in _device_tree_model():
        return False
    return _jonesey_segment() == "3-2"


def _medic_verified() -> bool:
    global _medic_verdict
    if _medic_verdict is None:
        _medic_verdict = _measure_medic()
    return _medic_verdict


def connection_is_local(connection) -> bool:
    """True only for a LocalConnection — a tty on THIS machine's USB tree.
    Emulated demos pin plausible port names for boards that are not plugged
    in, and an SSH'd command's /dev belongs to another machine entirely;
    labelling either with this box's engraving would be a fabrication."""
    try:
        from transport.connection import LocalConnection
        return isinstance(connection, LocalConnection)
    except Exception:
        return False


def usb_path_for_tty(dev: str) -> Optional[str]:
    """The kernel <bus>-<port> segment behind *dev* ("1-2", "1-1.3"), or
    None — including on any host that fails the medic identity check, where
    the answer would invite exactly the mistranslation this module refuses."""
    if not _medic_verified():
        return None
    return _resolve_segment(dev)


def case_port_label(dev: str) -> Optional[str]:
    """"Port 3", "Port 2 (via hub)", or None when we don't KNOW.

    A dotted segment ("1-1.3") means the board hangs off a hub plugged into
    the hole — the root before the first dot is the hole, and the label says
    so. Shadow-bus (2-x/4-x) and off-medic segments aren't in the map, so
    they come back None and the caller shows the raw path instead."""
    segment = usb_path_for_tty(dev)
    if not segment:
        return None
    root, dot, _ = segment.partition(".")
    number = MEDIC_PORT_MAP.get(root)
    if number is None:
        return None
    return f"Port {number} (via hub)" if dot else f"Port {number}"


def describe_port(dev: str, local: bool = True) -> str:
    """The drop-in for user-facing strings: "Port 3 (/dev/ttyACM1)" when the
    hole is known, otherwise the input unchanged. Callers whose port came
    through a Connection pass ``local=connection_is_local(conn)`` — a port an
    emulated or remote connection reported is never labelled."""
    if not dev or not local:
        return dev
    label = case_port_label(dev)
    return f"{label} ({dev})" if label else dev
